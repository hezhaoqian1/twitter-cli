"""Transactional acquisition and release of account and wallet leases."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db.base import utc_now
from ..models.tasks import ResourceLease, TaskJob


@dataclass(frozen=True)
class LeaseGrant:
    """The owner token and expiry returned after all resource keys are held."""

    owner_token: str
    lease_keys: tuple[str, ...]
    expires_at: datetime


class LeaseRepository:
    """Own the database writes for short-lived resource leases."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def acquire(
        self,
        job: TaskJob,
        *,
        ttl_seconds: float,
        owner_token: str | None = None,
        now: datetime | None = None,
    ) -> LeaseGrant | None:
        """Acquire every resource key or return None without a partial grant."""
        keys = tuple(dict.fromkeys(job.lease_keys))
        if not keys:
            return None

        current_time = now or utc_now()
        expires_at = current_time + timedelta(seconds=ttl_seconds)
        token = owner_token or secrets.token_urlsafe(32)

        try:
            with self.session.begin_nested():
                self.session.execute(
                    delete(ResourceLease).where(
                        ResourceLease.lease_key.in_(keys),
                        ResourceLease.expires_at <= current_time,
                    )
                )
                active = self.session.scalars(
                    select(ResourceLease).where(
                        ResourceLease.lease_key.in_(keys),
                        ResourceLease.expires_at > current_time,
                    )
                ).all()
                if active:
                    return None

                self.session.add_all(
                    [
                        ResourceLease(
                            lease_key=key,
                            task_job_id=job.id,
                            owner_token=token,
                            acquired_at=current_time,
                            expires_at=expires_at,
                        )
                        for key in keys
                    ]
                )
                self.session.flush()
        except IntegrityError:
            # A concurrent transaction may win the unique lease key race.
            return None

        return LeaseGrant(
            owner_token=token,
            lease_keys=keys,
            expires_at=expires_at,
        )

    def release(
        self,
        owner_token: str,
        *,
        task_job_id: UUID | None = None,
        lease_keys: tuple[str, ...] | None = None,
    ) -> int:
        """Release only rows owned by the supplied worker token."""
        conditions = [ResourceLease.owner_token == owner_token]
        if task_job_id is not None:
            conditions.append(ResourceLease.task_job_id == task_job_id)
        if lease_keys is not None:
            conditions.append(ResourceLease.lease_key.in_(lease_keys))

        result = self.session.execute(delete(ResourceLease).where(*conditions))
        self.session.flush()
        return int(getattr(result, "rowcount", 0) or 0)
