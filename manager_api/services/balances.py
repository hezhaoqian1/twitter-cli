"""Persistence helpers for the latest read-only Kredo balance snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from ..adapters.protocol import KredoBalanceResult
from ..db.base import utc_now
from ..models.balances import BalanceSyncStatus, KredoBalanceSnapshot


@dataclass(frozen=True)
class BalanceView:
    """Public binding identity plus its latest balance snapshot."""

    snapshot: KredoBalanceSnapshot


class BalanceService:
    """Upsert one binding-scoped balance while preserving the last good values."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def sync_success(
        self,
        binding_id: UUID,
        result: KredoBalanceResult,
    ) -> BalanceView:
        """Write a successful summary and clear any previous sync error."""
        snapshot = self._get_or_create(binding_id)
        snapshot.points = result.points
        snapshot.cash_hsk_available = result.cash_hsk_available
        snapshot.positions_value_hsk = result.positions_value_hsk
        snapshot.sync_status = BalanceSyncStatus.SUCCESS
        snapshot.error_code = None
        snapshot.last_synced_at = utc_now()
        self.session.flush()
        return BalanceView(snapshot)

    def sync_error(self, binding_id: UUID, error_code: str) -> BalanceView:
        """Record a safe error code without erasing the last known balances."""
        snapshot = self._get_or_create(binding_id)
        snapshot.sync_status = BalanceSyncStatus.ERROR
        snapshot.error_code = error_code[:96]
        self.session.flush()
        return BalanceView(snapshot)

    def get(self, binding_id: UUID) -> BalanceView | None:
        """Load one latest snapshot without selecting any secret material."""
        snapshot = self.session.query(KredoBalanceSnapshot).filter_by(binding_id=binding_id).one_or_none()
        return BalanceView(snapshot) if snapshot is not None else None

    def _get_or_create(self, binding_id: UUID) -> KredoBalanceSnapshot:
        """Create the row lazily so bindings remain valid before first sync."""
        snapshot = (
            self.session.query(KredoBalanceSnapshot)
            .filter_by(binding_id=binding_id)
            .one_or_none()
        )
        if snapshot is None:
            snapshot = KredoBalanceSnapshot(binding_id=binding_id)
            self.session.add(snapshot)
            self.session.flush()
        return snapshot
