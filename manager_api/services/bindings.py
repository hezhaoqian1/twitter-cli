"""Immutable account-wallet pairing rules and lifecycle operations."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..db.base import utc_now
from ..models.accounts import LifecycleState, SocialAccount
from ..models.bindings import AccountWalletBinding, BindingState
from ..models.tasks import ResourceLease
from ..models.wallets import Wallet

ACCOUNT_LEASE_PREFIX = "account:"
WALLET_LEASE_PREFIX = "wallet:"


class BindingError(ValueError):
    """Base error for binding commands."""


class BindingNotFoundError(BindingError):
    """Raised when a binding identifier does not exist."""


class BindingConflictError(BindingError):
    """Raised when a binding command violates an immutable invariant."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class BindingView:
    """Redacted binding view shared by command and list responses."""

    binding: AccountWalletBinding
    account: SocialAccount
    wallet: Wallet


class BindingService:
    """Own transaction-safe pairing creation, confirmation, and archival."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_pending(
        self,
        social_account_id: UUID,
        wallet_id: UUID,
        *,
        binding_key: str | None = None,
    ) -> BindingView:
        """Create a pending intent after validating both resources atomically."""
        account = self.session.get(SocialAccount, social_account_id)
        wallet = self.session.get(Wallet, wallet_id)
        self._require_active_account(account)
        self._require_active_wallet(wallet)
        assert account is not None
        assert wallet is not None

        self._check_resource_history(account.id, wallet.id)
        self._check_leases(account.id, wallet.id)

        binding = AccountWalletBinding(
            social_account_id=account.id,
            wallet_id=wallet.id,
            binding_key=binding_key or f"bind:{uuid4()}",
            state=BindingState.PENDING,
        )
        try:
            with self.session.begin_nested():
                self.session.add(binding)
                self.session.flush()
        except IntegrityError as exc:
            raise BindingConflictError(
                "already_bound",
                "account or wallet already has an active binding",
            ) from exc
        return BindingView(binding=binding, account=account, wallet=wallet)

    def confirm(self, binding_id: UUID, external_reference: str) -> BindingView:
        """Finalize one pending binding without permitting resource changes."""
        reference = external_reference.strip()
        if not reference:
            raise BindingError("external reference must not be empty")
        binding = self.session.get(
            AccountWalletBinding,
            binding_id,
            options=(
                joinedload(AccountWalletBinding.account),
                joinedload(AccountWalletBinding.wallet),
                joinedload(AccountWalletBinding.balance_snapshot),
            ),
        )
        if binding is None:
            raise BindingNotFoundError("binding not found")
        assert binding.account is not None
        assert binding.wallet is not None

        if binding.state is BindingState.ARCHIVED:
            raise BindingConflictError("archived_binding", "archived binding cannot be confirmed")
        if binding.state is BindingState.BOUND:
            if binding.external_reference == reference:
                return BindingView(binding, binding.account, binding.wallet)
            raise BindingConflictError(
                "binding_immutable",
                "confirmed binding cannot be changed",
            )

        binding.state = BindingState.BOUND
        binding.bound_at = utc_now()
        binding.external_reference = reference
        self.session.flush()
        return BindingView(binding, binding.account, binding.wallet)

    def archive(self, binding_id: UUID) -> BindingView:
        """Archive a binding while retaining its immutable historical record."""
        binding = self.session.get(
            AccountWalletBinding,
            binding_id,
            options=(
                joinedload(AccountWalletBinding.account),
                joinedload(AccountWalletBinding.wallet),
            ),
        )
        if binding is None:
            raise BindingNotFoundError("binding not found")
        assert binding.account is not None
        assert binding.wallet is not None
        if binding.state is not BindingState.ARCHIVED:
            binding.state = BindingState.ARCHIVED
            binding.archived_at = utc_now()
            self.session.flush()
        return BindingView(binding, binding.account, binding.wallet)

    def get(self, binding_id: UUID) -> BindingView:
        """Load one binding with its public account and wallet identities."""
        binding = self.session.get(
            AccountWalletBinding,
            binding_id,
            options=(
                joinedload(AccountWalletBinding.account),
                joinedload(AccountWalletBinding.wallet),
            ),
        )
        if binding is None or binding.account is None or binding.wallet is None:
            raise BindingNotFoundError("binding not found")
        return BindingView(binding, binding.account, binding.wallet)

    def list(self, *, offset: int = 0, limit: int = 50) -> tuple[list[BindingView], int]:
        """Return binding history without selecting secret-bearing relationships."""
        bindings = self.session.scalars(
            select(AccountWalletBinding)
            .options(
                joinedload(AccountWalletBinding.account),
                joinedload(AccountWalletBinding.wallet),
                joinedload(AccountWalletBinding.balance_snapshot),
            )
            .order_by(AccountWalletBinding.created_at, AccountWalletBinding.id)
            .offset(offset)
            .limit(limit)
        ).all()
        total = self._count()
        return [
            BindingView(binding, binding.account, binding.wallet)
            for binding in bindings
            if binding.account is not None and binding.wallet is not None
        ], total

    def _check_resource_history(self, account_id: UUID, wallet_id: UUID) -> None:
        """Reject active intents and any previously confirmed reassignment."""
        rows = self.session.scalars(
            select(AccountWalletBinding).where(
                or_(
                    AccountWalletBinding.social_account_id == account_id,
                    AccountWalletBinding.wallet_id == wallet_id,
                )
            )
        ).all()
        for row in rows:
            if row.state is BindingState.PENDING and row.archived_at is None:
                raise BindingConflictError(
                    "binding_in_progress",
                    "account or wallet already has a pending binding",
                )
            if row.state is BindingState.BOUND or row.bound_at is not None:
                raise BindingConflictError(
                    "already_bound",
                    "confirmed account or wallet bindings are immutable",
                )

    def _check_leases(self, account_id: UUID, wallet_id: UUID) -> None:
        """Reject pairing while either resource is held by an active job lease."""
        keys = (
            f"{ACCOUNT_LEASE_PREFIX}{account_id}",
            f"{WALLET_LEASE_PREFIX}{wallet_id}",
        )
        lease = self.session.scalar(
            select(ResourceLease).where(
                ResourceLease.lease_key.in_(keys),
                ResourceLease.expires_at > utc_now(),
            )
        )
        if lease is not None:
            raise BindingConflictError(
                "resource_leased",
                "account or wallet is currently leased by another task",
            )

    @staticmethod
    def _require_active_account(account: SocialAccount | None) -> None:
        """Reject missing or archived social accounts with a stable conflict code."""
        if account is None:
            raise BindingConflictError("account_not_found", "social account not found")
        if account.state is not LifecycleState.ACTIVE or account.archived_at is not None:
            raise BindingConflictError("archived_account", "social account is archived")

    @staticmethod
    def _require_active_wallet(wallet: Wallet | None) -> None:
        """Reject missing or archived wallets with a stable conflict code."""
        if wallet is None:
            raise BindingConflictError("wallet_not_found", "wallet not found")
        if wallet.state != "active" or wallet.archived_at is not None:
            raise BindingConflictError("archived_wallet", "wallet is archived")

    def _count(self) -> int:
        """Count durable bindings for pagination."""
        return self.session.scalar(select(func.count()).select_from(AccountWalletBinding)) or 0
