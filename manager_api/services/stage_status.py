"""Read-only staged-operation status and recommendation helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .bind_status_sync import queue_bind_status_sync
from .runtime import collect_operations_summary
from .stage_polls import requeue_stage_polls
from .stage_retries import retry_stage_failures


@dataclass(frozen=True)
class StageStatusRow:
    """One stage row with only aggregate operation counts."""

    stage: str
    ready: int
    waiting: int
    failed: int
    pollable: int
    retryable: int
    status_syncable: int = 0


@dataclass(frozen=True)
class StageStatusSummary:
    """Complete redacted operator status snapshot."""

    resources: dict[str, int]
    stages: tuple[StageStatusRow, ...]

    def to_json(self) -> str:
        """输出稳定 JSON，不包含账号、地址、链接、Cookie 或私钥。"""
        return json.dumps(
            {
                "resources": self.resources,
                "stages": [row.__dict__ for row in self.stages],
            },
            ensure_ascii=False,
            sort_keys=True,
        )


@dataclass(frozen=True)
class NextStageRecommendation:
    """Aggregate-only next action for a server operator."""

    action: str
    stage: str | None
    command: str
    reason: str

    def to_json(self) -> str:
        """输出稳定 JSON，不包含账号、地址、链接、Cookie 或私钥。"""
        return json.dumps(self.__dict__, ensure_ascii=False, sort_keys=True)


def collect_stage_status(session: Session, *, limit: int) -> StageStatusSummary:
    """读取当前阶段操作面，不修改任务、绑定或资源。"""
    operations = collect_operations_summary(session)
    stage_rows: list[StageStatusRow] = []
    for stage in operations.stages:
        pollable = 0
        if stage.key in {"bind", "repost", "claim"}:
            pollable = requeue_stage_polls(
                session,
                stage=stage.key,
                limit=limit,
                apply=False,
            ).selected
        retryable = retry_stage_failures(
            session,
            stage=stage.key,
            limit=limit,
            apply=False,
        ).selected
        status_syncable = 0
        if stage.key == "bind":
            status_syncable = queue_bind_status_sync(
                session,
                name="stage status bind sync preview",
                limit=limit,
                dispatch_limit=10,
                apply=False,
            ).selected
        stage_rows.append(
            StageStatusRow(
                stage=stage.key,
                ready=stage.ready,
                waiting=stage.waiting,
                failed=stage.failed,
                pollable=pollable,
                retryable=retryable,
                status_syncable=status_syncable,
            )
        )
    return StageStatusSummary(
        resources=operations.resources.model_dump(),
        stages=tuple(stage_rows),
    )


def recommend_next_stage(
    summary: StageStatusSummary,
    *,
    target_placeholder: str = "REPOST_TARGET",
) -> NextStageRecommendation:
    """根据阶段快照推荐下一条操作命令，不修改数据库。"""
    rows = {row.stage: row for row in summary.stages}
    for stage in ("bind", "repost", "claim"):
        row = rows.get(stage)
        if row is not None and row.pollable > 0:
            return NextStageRecommendation(
                action="poll",
                stage=stage,
                command=f"uv run python scripts/manager_requeue_stage_polls.py {stage} --limit 10 --apply",
                reason=f"{stage} has delayed external status that can be re-read",
            )
    bind_row = rows.get("bind")
    if bind_row is not None and bind_row.status_syncable > 0:
        return NextStageRecommendation(
            action="sync_bind_status",
            stage="bind",
            command="uv run python scripts/manager_sync_bind_status.py --limit 10 --apply",
            reason="bind has pending rows that can be checked through Kredo task API status",
        )
    for stage in ("verify", "bind", "repost", "claim"):
        row = rows.get(stage)
        if row is not None and row.retryable > 0:
            return NextStageRecommendation(
                action="retry",
                stage=stage,
                command=f"uv run python scripts/manager_retry_stage_failures.py {stage} --limit 10 --apply",
                reason=f"{stage} has failed jobs ready for explicit retry",
            )
    for stage in ("claim", "repost", "bind", "verify"):
        row = rows.get(stage)
        if row is None or row.ready < 1:
            continue
        target_flag = f' --target "{target_placeholder}"' if stage == "repost" else ""
        return NextStageRecommendation(
            action="create_stage",
            stage=stage,
            command=(
                "uv run python scripts/manager_create_stage_batch.py "
                f"{stage}{target_flag} --limit 10 --dispatch-limit 10"
            ),
            reason=f"{stage} has rows ready for a new independent stage batch",
        )
    return NextStageRecommendation(
        action="wait",
        stage=None,
        command="uv run python scripts/manager_stage_status.py",
        reason="no stage has ready, pollable, or retryable work",
    )
