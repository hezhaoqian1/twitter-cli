"""Read-only acceptance audit for staged manager operations."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .stage_status import NextStageRecommendation, StageStatusRow, recommend_next_stage
from .stage_status import collect_stage_status


@dataclass(frozen=True)
class AcceptanceAction:
    """一个可执行的阶段动作建议，只包含聚合数量和命令模板。"""

    action: str
    stage: str
    count: int
    command: str


@dataclass(frozen=True)
class AcceptanceAudit:
    """完整验收快照，不包含账号、地址、Cookie、token、私钥或目标原文。"""

    resources: dict[str, int]
    stages: tuple[StageStatusRow, ...]
    next_action: NextStageRecommendation
    actions: tuple[AcceptanceAction, ...]

    def to_json(self) -> str:
        """输出稳定 JSON，用于服务器日志或 CI 留档。"""
        return json.dumps(
            {
                "resources": self.resources,
                "stages": [row.__dict__ for row in self.stages],
                "next_action": self.next_action.__dict__,
                "actions": [action.__dict__ for action in self.actions],
            },
            ensure_ascii=False,
            sort_keys=True,
        )


def _stage_create_command(stage: StageStatusRow, target_placeholder: str, limit: int) -> str:
    """根据阶段生成只含占位符的批次创建命令。"""
    count_limit = min(stage.ready, limit)
    target_flag = f' --target "{target_placeholder}"' if stage.stage == "repost" else ""
    return (
        "uv run python scripts/manager_create_stage_batch.py "
        f"{stage.stage}{target_flag} --limit {count_limit} --dispatch-limit {min(count_limit, 10)}"
    )


def collect_acceptance_audit(
    session: Session,
    *,
    limit: int,
    target_placeholder: str = "REPOST_TARGET",
) -> AcceptanceAudit:
    """收集当前阶段验收清单，不修改任务、绑定或资源。"""
    if limit < 1:
        raise ValueError("limit must be positive")
    summary = collect_stage_status(session, limit=limit)
    actions: list[AcceptanceAction] = []
    for row in summary.stages:
        if row.pollable > 0 and row.stage in {"bind", "repost", "claim"}:
            actions.append(
                AcceptanceAction(
                    action="poll",
                    stage=row.stage,
                    count=row.pollable,
                    command=(
                        f"uv run python scripts/manager_requeue_stage_polls.py {row.stage} "
                        f"--limit {min(row.pollable, limit)} --apply"
                    ),
                )
            )
        if row.stage == "bind" and row.status_syncable > 0:
            actions.append(
                AcceptanceAction(
                    action="sync_bind_status",
                    stage=row.stage,
                    count=row.status_syncable,
                    command=(
                        "uv run python scripts/manager_sync_bind_status.py "
                        f"--limit {min(row.status_syncable, limit)} --apply"
                    ),
                )
            )
        if row.retryable > 0:
            actions.append(
                AcceptanceAction(
                    action="retry",
                    stage=row.stage,
                    count=row.retryable,
                    command=(
                        f"uv run python scripts/manager_retry_stage_failures.py {row.stage} "
                        f"--limit {min(row.retryable, limit)} --apply"
                    ),
                )
            )
        if row.ready > 0:
            actions.append(
                AcceptanceAction(
                    action="create_stage",
                    stage=row.stage,
                    count=row.ready,
                    command=_stage_create_command(row, target_placeholder, limit),
                )
            )
    return AcceptanceAudit(
        resources=summary.resources,
        stages=summary.stages,
        next_action=recommend_next_stage(summary, target_placeholder=target_placeholder),
        actions=tuple(actions),
    )
