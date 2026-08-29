"""Runtime observability endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...api.dependencies import get_db, get_redis_client
from ...schemas.runtime import (
    AcceptanceActionResponse,
    AcceptanceAuditResponse,
    AcceptanceStageResponse,
    NextStageRecommendationResponse,
    OperationsSummaryResponse,
    RuntimeMetricsResponse,
)
from ...services.acceptance import collect_acceptance_audit
from ...services.runtime import (
    RuntimeRedisClient,
    collect_operations_summary,
    collect_runtime_metrics,
)
from ...services.stage_status import collect_stage_status, recommend_next_stage

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


@router.get("/next-stage", response_model=NextStageRecommendationResponse)
def next_stage_recommendation(
    limit: int = Query(default=500, ge=1, le=500),
    session: Session = Depends(get_db),
) -> NextStageRecommendationResponse:
    """Return the next aggregate stage command without mutating external state."""
    recommendation = recommend_next_stage(collect_stage_status(session, limit=limit))
    return NextStageRecommendationResponse(
        action=recommendation.action,
        stage=recommendation.stage,
        command=recommendation.command,
        reason=recommendation.reason,
    )


@router.get("/acceptance-audit", response_model=AcceptanceAuditResponse)
def acceptance_audit(
    limit: int = Query(default=500, ge=1, le=500),
    session: Session = Depends(get_db),
) -> AcceptanceAuditResponse:
    """Return the read-only interface-first acceptance checklist."""
    audit = collect_acceptance_audit(session, limit=limit)
    return AcceptanceAuditResponse(
        resources=audit.resources,
        stages=[
            AcceptanceStageResponse(
                stage=row.stage,
                ready=row.ready,
                waiting=row.waiting,
                failed=row.failed,
                pollable=row.pollable,
                retryable=row.retryable,
                status_syncable=row.status_syncable,
            )
            for row in audit.stages
        ],
        next_action=NextStageRecommendationResponse(
            action=audit.next_action.action,
            stage=audit.next_action.stage,
            command=audit.next_action.command,
            reason=audit.next_action.reason,
        ),
        actions=[
            AcceptanceActionResponse(
                action=action.action,
                stage=action.stage,
                count=action.count,
                command=action.command,
            )
            for action in audit.actions
        ],
    )
