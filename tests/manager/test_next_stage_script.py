from __future__ import annotations

from manager_api.services.stage_status import (
    StageStatusRow,
    StageStatusSummary,
    recommend_next_stage,
)


def _summary(*rows: StageStatusRow) -> StageStatusSummary:
    """构造只包含聚合数量的阶段快照。"""
    return StageStatusSummary(resources={}, stages=rows)


def test_next_stage_prefers_pollable_slow_external_state() -> None:
    """慢回写优先轮询，避免重复提交外部动作。"""
    recommendation = recommend_next_stage(
        _summary(
            StageStatusRow("verify", 5, 0, 0, 0, 0),
            StageStatusRow("bind", 0, 5, 1, 2, 1),
            StageStatusRow("repost", 0, 0, 0, 0, 0),
            StageStatusRow("claim", 0, 0, 0, 0, 0),
        )
    )

    assert recommendation.action == "poll"
    assert recommendation.stage == "bind"
    assert "manager_requeue_stage_polls.py bind" in recommendation.command


def test_next_stage_recommends_bind_status_sync_for_pending_rows() -> None:
    """pending 绑定没有普通 pollable 任务时，优先提示只读状态同步。"""
    recommendation = recommend_next_stage(
        _summary(
            StageStatusRow("verify", 5, 0, 0, 0, 0),
            StageStatusRow("bind", 0, 5, 0, 0, 0, 3),
            StageStatusRow("repost", 0, 0, 0, 0, 0),
            StageStatusRow("claim", 0, 0, 0, 0, 0),
        )
    )

    assert recommendation.action == "sync_bind_status"
    assert recommendation.stage == "bind"
    assert "manager_sync_bind_status.py" in recommendation.command


def test_next_stage_retries_failures_before_new_batches() -> None:
    """失败任务优先用原任务重试，不创建重复阶段批次。"""
    recommendation = recommend_next_stage(
        _summary(
            StageStatusRow("verify", 5, 0, 0, 0, 0),
            StageStatusRow("bind", 0, 0, 1, 0, 1),
        )
    )

    assert recommendation.action == "retry"
    assert recommendation.stage == "bind"
    assert "manager_retry_stage_failures.py bind" in recommendation.command


def test_next_stage_creates_highest_downstream_ready_stage() -> None:
    """已有下游就绪时优先领取，再转发，再绑定，再校验。"""
    recommendation = recommend_next_stage(
        _summary(
            StageStatusRow("verify", 5, 0, 0, 0, 0),
            StageStatusRow("bind", 3, 0, 0, 0, 0),
            StageStatusRow("repost", 2, 0, 0, 0, 0),
            StageStatusRow("claim", 1, 0, 0, 0, 0),
        )
    )

    assert recommendation.action == "create_stage"
    assert recommendation.stage == "claim"
    assert "manager_create_stage_batch.py claim" in recommendation.command


def test_next_stage_returns_wait_when_no_work_exists() -> None:
    """没有可执行、可轮询或可重试任务时只建议重新查看状态。"""
    recommendation = recommend_next_stage(
        _summary(
            StageStatusRow("verify", 0, 0, 0, 0, 0),
            StageStatusRow("bind", 0, 0, 0, 0, 0),
        )
    )

    assert recommendation.action == "wait"
    assert recommendation.stage is None
    assert "manager_stage_status.py" in recommendation.command
