"""Local provider fixtures for deterministic manager-worker acceptance runs."""

from __future__ import annotations

from collections import Counter
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from .adapters.protocol import (
    AccountHealthResult,
    AdapterEvidence,
    ExternalStatus,
)
from .models.accounts import AccountHealth


@dataclass
class SyntheticProviderState:
    """Keep synthetic call counts in one process without storing secret material."""

    verify_calls: Counter[str]
    repost_calls: Counter[str]
    bind_calls: Counter[str]
    status_calls: Counter[str]
    claim_calls: Counter[str]


_STATE = SyntheticProviderState(
    verify_calls=Counter(),
    repost_calls=Counter(),
    bind_calls=Counter(),
    status_calls=Counter(),
    claim_calls=Counter(),
)


class SyntheticXAdapter:
    """Simulate an authenticated X session and one idempotent repost."""

    def verify_account(self, account: Any) -> AccountHealthResult:
        """Return a healthy profile while recording one verification per handle."""
        _STATE.verify_calls[account.handle] += 1
        return AccountHealthResult(
            health=AccountHealth.HEALTHY,
            handle=account.handle,
            user_id=f"synthetic-user:{account.handle}",
            evidence=AdapterEvidence(
                code="account_verified",
                summary="synthetic account verified",
            ),
        )

    def repost(self, account: Any, operation: Any):
        """Accept one repost request and leave final confirmation to Kredo polling."""
        del operation
        _STATE.repost_calls[account.handle] += 1
        from .adapters.protocol import ExternalOperation

        return ExternalOperation(
            operation_ref=f"synthetic-x-repost:{account.handle}",
            status=ExternalStatus.SUCCEEDED,
            evidence=AdapterEvidence(
                code="repost_submitted",
                summary="synthetic X repost submitted",
            ),
        )


def build_synthetic_x_adapter() -> SyntheticXAdapter:
    """Build the injectable X adapter used by the local Worker process."""
    return SyntheticXAdapter()


class _SyntheticWorkflow(AbstractContextManager):
    """Own one short-lived provider context for a single adapter call."""

    def __init__(self, operation: Any) -> None:
        self.operation = operation

    def __enter__(self) -> "_SyntheticWorkflow":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        return None

    def bind(self, account: Any, wallet: Any, operation: Any):
        """Confirm one unique account-wallet binding."""
        del account, operation
        from .adapters.protocol import ExternalOperation

        _STATE.bind_calls[wallet.address] += 1
        return ExternalOperation(
            operation_ref=f"synthetic-binding:{wallet.address}",
            status=ExternalStatus.SUCCEEDED,
            evidence=AdapterEvidence(
                code="bound",
                summary="synthetic binding complete",
            ),
        )

    def repost(self, account: Any, wallet: Any, operation: Any):
        """Expose the same normalized operation contract if called directly."""
        del account, wallet, operation
        from .adapters.protocol import ExternalOperation

        return ExternalOperation(
            operation_ref=f"synthetic-kredo-repost:{self.operation.metadata.get('binding_id')}",
            status=ExternalStatus.PENDING,
            evidence=AdapterEvidence(
                code="repost_pending",
                summary="synthetic repost validation pending",
            ),
        )

    def status(self, operation: Any, account: Any | None = None, wallet: Any | None = None):
        """Return pending once, then succeeded, scoped to one binding."""
        del account, wallet
        from .adapters.protocol import ExternalObservation

        binding_id = str(operation.metadata.get("binding_id") or operation.operation_ref or "unknown")
        _STATE.status_calls[binding_id] += 1
        status = (
            ExternalStatus.PENDING
            if _STATE.status_calls[binding_id] == 1
            else ExternalStatus.SUCCEEDED
        )
        return ExternalObservation(
            operation_ref=f"synthetic-kredo-repost:{binding_id}",
            status=status,
            evidence=AdapterEvidence(
                code="repost_pending" if status.is_delayed else "repost_verified",
                summary="synthetic Kredo repost validation",
            ),
        )

    def claim(self, account: Any, wallet: Any, operation: Any):
        """Complete a claim only after the dependency graph allows execution."""
        del operation
        from .adapters.protocol import ExternalOperation

        _STATE.claim_calls[account.handle] += 1
        return ExternalOperation(
            operation_ref=f"synthetic-claim:{wallet.address}",
            status=ExternalStatus.SUCCEEDED,
            evidence=AdapterEvidence(
                code="claimed",
                summary="synthetic claim complete",
            ),
        )

    def account_summary(self, account: Any, wallet: Any, operation: Any):
        """Return deterministic Points and HSK values for the UI balance view."""
        del operation
        return {
            "points": 100,
            "cashHsk": {"available": 10},
            "portfolio": {"positionsValueHsk": 2.5},
            "summary": {
                "account": account.handle,
                "wallet": wallet.address,
            },
        }


def synthetic_workflow_factory(operation: Any) -> _SyntheticWorkflow:
    """Create one isolated synthetic browser-context replacement."""
    return _SyntheticWorkflow(operation)


def synthetic_state_snapshot() -> dict[str, dict[str, int]]:
    """Return redacted counters for acceptance assertions and operator logs."""
    return {
        "verify_calls": dict(_STATE.verify_calls),
        "repost_calls": dict(_STATE.repost_calls),
        "bind_calls": dict(_STATE.bind_calls),
        "status_calls": dict(_STATE.status_calls),
        "claim_calls": dict(_STATE.claim_calls),
    }
