"""Wallet validation, MetaMask-compatible derivation, and encrypted persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.wallets import Wallet, WalletSecret, WalletSource, WalletSourceType
from .vault import VaultService

DEFAULT_DERIVATION_PATH = "m/44'/60'/0'/0/{index}"
MAX_DERIVATION_INDEX = 2**31 - 1


class WalletInputError(ValueError):
    """Raised when wallet material or derivation parameters are invalid."""


class WalletImportStatus(str, Enum):
    """Safe classification values for wallet import candidates."""

    VALID = "valid"
    DUPLICATE_IN_FILE = "duplicate_in_file"
    DUPLICATE_EXISTING = "duplicate_existing"
    COMMITTED = "committed"


@dataclass(frozen=True)
class WalletCandidate:
    """A derived candidate kept in memory only for the current operation."""

    index: int | None
    address: str
    normalized_address: str
    derivation_path: str | None
    private_key: str


@dataclass
class WalletDecision:
    """Internal candidate decision whose private key never crosses the API."""

    candidate: WalletCandidate
    status: WalletImportStatus
    diagnostic_code: str | None = None
    diagnostic_detail: str | None = None
    wallet_id: UUID | None = None


@dataclass
class WalletPreview:
    """Redacted preview shared by preview and commit operations."""

    source_type: WalletSourceType
    label: str | None
    start_index: int
    count: int
    decisions: list[WalletDecision]

    @property
    def total(self) -> int:
        """Return the number of wallet candidates."""
        return len(self.decisions)

    def summary(self, *, committed: int = 0) -> dict[str, int]:
        """Return counts without exposing source material or private keys."""
        counts = {
            "total": self.total,
            "valid": 0,
            "duplicate_in_file": 0,
            "duplicate_existing": 0,
            "committed": committed,
            "skipped": 0,
        }
        for decision in self.decisions:
            if decision.status is WalletImportStatus.VALID:
                counts["valid"] += 1
            elif decision.status is WalletImportStatus.DUPLICATE_IN_FILE:
                counts["duplicate_in_file"] += 1
            elif decision.status is WalletImportStatus.DUPLICATE_EXISTING:
                counts["duplicate_existing"] += 1
        counts["skipped"] = counts["duplicate_in_file"] + counts["duplicate_existing"]
        return counts


class WalletService:
    """Own wallet input handling and the encrypted wallet persistence boundary."""

    def __init__(self, session: Session, vault: VaultService | None = None) -> None:
        self.session = session
        self.vault = vault or VaultService(session)

    def preview(
        self,
        source_type: WalletSourceType,
        secret: str,
        *,
        label: str | None = None,
        start_index: int = 0,
        count: int = 1,
    ) -> WalletPreview:
        """Validate material, derive candidates, and classify known addresses."""
        normalized_secret = self._validate_request(source_type, secret, start_index, count)
        candidates = self._candidates(source_type, normalized_secret, start_index, count)
        existing = self._existing_addresses(
            {candidate.normalized_address for candidate in candidates}
        )
        seen: set[str] = set()
        decisions: list[WalletDecision] = []
        for candidate in candidates:
            if candidate.normalized_address in seen:
                decisions.append(
                    WalletDecision(
                        candidate=candidate,
                        status=WalletImportStatus.DUPLICATE_IN_FILE,
                        diagnostic_code="duplicate_address_in_file",
                        diagnostic_detail="same normalized address appeared earlier in this request",
                    )
                )
            elif candidate.normalized_address in existing:
                decisions.append(
                    WalletDecision(
                        candidate=candidate,
                        status=WalletImportStatus.DUPLICATE_EXISTING,
                        diagnostic_code="address_already_exists",
                        diagnostic_detail="normalized address already exists",
                    )
                )
            else:
                decisions.append(
                    WalletDecision(candidate=candidate, status=WalletImportStatus.VALID)
                )
            seen.add(candidate.normalized_address)
        return WalletPreview(
            source_type=source_type,
            label=label,
            start_index=start_index,
            count=count,
            decisions=decisions,
        )

    def commit(
        self,
        source_type: WalletSourceType,
        secret: str,
        *,
        label: str | None = None,
        start_index: int = 0,
        count: int = 1,
    ) -> tuple[WalletSource | None, WalletPreview]:
        """Encrypt source material and each accepted private key before commit."""
        normalized_secret = self._validate_request(source_type, secret, start_index, count)
        preview = self.preview(
            source_type,
            normalized_secret,
            label=label,
            start_index=start_index,
            count=count,
        )
        accepted = [
            decision
            for decision in preview.decisions
            if decision.status is WalletImportStatus.VALID
        ]
        if not accepted:
            return None, preview

        source = WalletSource(
            source_type=source_type,
            label=label,
            derivation_path=(
                DEFAULT_DERIVATION_PATH if source_type is WalletSourceType.MNEMONIC else None
            ),
            encrypted_source_ref=b"pending",
            envelope_version=1,
        )
        self.session.add(source)
        self.session.flush()
        source.encrypted_source_ref = self.vault.encrypt_field(
            "wallet_sources",
            source.id,
            "source_material",
            normalized_secret,
        )

        for decision in accepted:
            wallet = Wallet(
                wallet_source_id=source.id,
                address=decision.candidate.address,
                normalized_address=decision.candidate.normalized_address,
                derivation_path=decision.candidate.derivation_path,
                derivation_index=decision.candidate.index,
                state="active",
            )
            self.session.add(wallet)
            self.session.flush()
            wallet_secret = self._new_wallet_secret(
                wallet.id,
                source_type,
                decision.candidate,
            )
            self.session.add(wallet_secret)
            self.session.flush()
            wallet_secret.envelope = self.vault.encrypt_field(
                "wallet_secrets",
                wallet_secret.id,
                "private_key",
                decision.candidate.private_key,
            )
            decision.status = WalletImportStatus.COMMITTED
            decision.wallet_id = wallet.id
        self.session.flush()
        return source, preview

    def derive(
        self,
        source_id: UUID,
        *,
        start_index: int = 0,
        count: int = 1,
    ) -> tuple[WalletSource, WalletPreview]:
        """Decrypt an existing mnemonic source and add new derived addresses."""
        self._validate_range(start_index, count)
        source = self.session.get(WalletSource, source_id)
        if source is None or source.archived_at is not None:
            raise WalletInputError("wallet source not found")
        if source.source_type is not WalletSourceType.MNEMONIC:
            raise WalletInputError("only mnemonic sources support derivation")
        mnemonic = self.vault.decrypt_field(
            "wallet_sources",
            source.id,
            "source_material",
            source.encrypted_source_ref,
        ).decode("utf-8")
        preview = self.preview(
            WalletSourceType.MNEMONIC,
            mnemonic,
            label=source.label,
            start_index=start_index,
            count=count,
        )
        accepted = [
            decision
            for decision in preview.decisions
            if decision.status is WalletImportStatus.VALID
        ]
        for decision in accepted:
            wallet = Wallet(
                wallet_source_id=source.id,
                address=decision.candidate.address,
                normalized_address=decision.candidate.normalized_address,
                derivation_path=decision.candidate.derivation_path,
                derivation_index=decision.candidate.index,
                state="active",
            )
            self.session.add(wallet)
            self.session.flush()
            wallet_secret = self._new_wallet_secret(
                wallet.id,
                WalletSourceType.MNEMONIC,
                decision.candidate,
            )
            self.session.add(wallet_secret)
            self.session.flush()
            wallet_secret.envelope = self.vault.encrypt_field(
                "wallet_secrets",
                wallet_secret.id,
                "private_key",
                decision.candidate.private_key,
            )
            decision.status = WalletImportStatus.COMMITTED
            decision.wallet_id = wallet.id
        self.session.flush()
        return source, preview

    def _new_wallet_secret(
        self,
        wallet_id: UUID,
        source_type: WalletSourceType,
        candidate: WalletCandidate,
    ) -> WalletSecret:
        """Create redacted metadata before encrypting the private key."""
        return WalletSecret(
            wallet_id=wallet_id,
            version=1,
            is_current=True,
            envelope=b"pending",
            envelope_version=1,
            secret_fingerprint=self.vault.fingerprint(candidate.private_key),
            redacted_metadata=json.dumps(
                {
                    "source_type": source_type.value,
                    "derivation_index": candidate.index,
                    "derivation_path": candidate.derivation_path,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

    @staticmethod
    def _validate_request(
        source_type: WalletSourceType,
        secret: str,
        start_index: int,
        count: int,
    ) -> str:
        """Normalize and strictly validate the input before any derivation."""
        if not isinstance(secret, str) or not secret.strip():
            raise WalletInputError("wallet secret must not be empty")
        WalletService._validate_range(start_index, count)
        normalized = " ".join(secret.strip().split())
        if source_type is WalletSourceType.PRIVATE_KEY:
            if start_index != 0 or count != 1:
                raise WalletInputError("private-key imports require start_index=0 and count=1")
            return WalletService._validate_private_key(normalized)
        if source_type is WalletSourceType.MNEMONIC:
            return WalletService._validate_mnemonic(normalized)
        raise WalletInputError("unsupported wallet source type")

    @staticmethod
    def _validate_range(start_index: int, count: int) -> None:
        """Keep derivation requests inside the non-hardened BIP-44 index range."""
        if start_index < 0 or count < 1:
            raise WalletInputError("start_index must be non-negative and count must be positive")
        if start_index > MAX_DERIVATION_INDEX or start_index + count - 1 > MAX_DERIVATION_INDEX:
            raise WalletInputError("derivation index is out of range")

    @staticmethod
    def _validate_private_key(secret: str) -> str:
        """Validate secp256k1 key material and return canonical lowercase hex."""
        candidate = secret[2:] if secret.lower().startswith("0x") else secret
        if len(candidate) != 64 or any(char not in "0123456789abcdefABCDEF" for char in candidate):
            raise WalletInputError("private key must be exactly 32 bytes of hexadecimal")
        try:
            from eth_account import Account

            account = Account.from_key(bytes.fromhex(candidate))
        except (ImportError, ValueError, TypeError) as exc:
            raise WalletInputError("private key is invalid") from exc
        if not account.address:
            raise WalletInputError("private key is invalid")
        return candidate.lower()

    @staticmethod
    def _validate_mnemonic(secret: str) -> str:
        """Validate checksum and supported BIP-39 word count before derivation."""
        try:
            from mnemonic import Mnemonic
        except ImportError as exc:
            raise WalletInputError("mnemonic support requires the mnemonic package") from exc
        words = secret.split()
        if len(words) not in {12, 15, 18, 21, 24}:
            raise WalletInputError("mnemonic must contain 12, 15, 18, 21, or 24 words")
        if not any(Mnemonic(language).check(secret) for language in Mnemonic.list_languages()):
            raise WalletInputError("mnemonic checksum or language is invalid")
        return " ".join(words)

    @staticmethod
    def _candidates(
        source_type: WalletSourceType,
        secret: str,
        start_index: int,
        count: int,
    ) -> list[WalletCandidate]:
        """Derive public addresses and private keys with the MetaMask path."""
        try:
            from eth_account import Account
        except ImportError as exc:
            raise WalletInputError("wallet derivation requires the eth-account package") from exc
        candidates: list[WalletCandidate] = []
        if source_type is WalletSourceType.PRIVATE_KEY:
            account = Account.from_key(bytes.fromhex(secret))
            address = WalletService._checksum_address(account.address)
            candidates.append(
                WalletCandidate(
                    index=None,
                    address=address,
                    normalized_address=WalletService.normalize_address(address),
                    derivation_path=None,
                    private_key=secret,
                )
            )
            return candidates

        try:
            Account.enable_unaudited_hdwallet_features()
            for index in range(start_index, start_index + count):
                path = DEFAULT_DERIVATION_PATH.format(index=index)
                account = Account.from_mnemonic(secret, account_path=path)
                address = WalletService._checksum_address(account.address)
                candidates.append(
                    WalletCandidate(
                        index=index,
                        address=address,
                        normalized_address=WalletService.normalize_address(address),
                        derivation_path=path,
                        private_key=account.key.hex(),
                    )
                )
        except (ValueError, TypeError) as exc:
            raise WalletInputError("mnemonic derivation failed") from exc
        return candidates

    def _existing_addresses(self, addresses: set[str]) -> set[str]:
        """Query only normalized public addresses, never selecting secret rows."""
        if not addresses:
            return set()
        rows = self.session.scalars(
            select(Wallet.normalized_address).where(Wallet.normalized_address.in_(addresses))
        ).all()
        return set(rows)

    @staticmethod
    def normalize_address(address: str) -> str:
        """Validate an Ethereum address and return its lowercase comparison key."""
        try:
            from eth_utils import is_address
        except ImportError as exc:
            raise WalletInputError("address validation requires the eth-utils package") from exc
        if not isinstance(address, str) or not is_address(address):
            raise WalletInputError("invalid Ethereum address")
        return address.lower()

    @staticmethod
    def _checksum_address(address: str) -> str:
        """Return the canonical EIP-55 representation for API/public storage."""
        try:
            from eth_utils import to_checksum_address
        except ImportError as exc:
            raise WalletInputError("address formatting requires the eth-utils package") from exc
        try:
            return to_checksum_address(address)
        except (TypeError, ValueError) as exc:
            raise WalletInputError("invalid Ethereum address") from exc
