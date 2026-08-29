"""Runtime observability endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...api.dependencies import get_db, get_redis_client
from ...schemas.runtime import OperationsSummaryResponse, RuntimeMetricsResponse
from ...services.runtime import (
    RuntimeRedisClient,
    collect_operations_summary,
    collect_runtime_metrics,
)

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


@router.get("/metrics", response_model=RuntimeMetricsResponse)
def runtime_metrics(
    session: Session = Depends(get_db),
    redis_client: RuntimeRedisClient = Depends(get_redis_client),
) -> RuntimeMetricsResponse:
    """Return one redacted snapshot for the operator overview."""
    return collect_runtime_metrics(session, redis_client)


@router.get("/operations-summary", response_model=OperationsSummaryResponse)
def operations_summary(
    session: Session = Depends(get_db),
) -> OperationsSummaryResponse:
    """Return staged workflow readiness without exposing secrets or raw targets."""
    return collect_operations_summary(session)
