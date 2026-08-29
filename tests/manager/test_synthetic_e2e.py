from __future__ import annotations

from scripts.manager_synthetic_e2e import _format_summary, _run


def test_synthetic_stage_e2e_reaches_claim_after_delayed_repost_poll() -> None:
    """本地验收脚本覆盖分阶段批量、慢回写轮询和最终领取。"""
    result = _run()

    assert result["jobs"] == 16
    assert result["bindings_bound"] == 4
    assert result["delayed_reposts_polled"] == 4
    assert result["repost_calls"] == 4
    assert result["claim_calls"] == 4
    assert result["queue_ready"] == 0
    assert result["queue_processing"] == 0


def test_synthetic_stage_e2e_summary_is_log_friendly() -> None:
    """验收摘要使用稳定 key=value 行，便于部署日志检索。"""
    assert _format_summary(
        {
            "cycles": 9,
            "jobs": 16,
            "bindings_bound": 4,
            "delayed_reposts_polled": 4,
            "repost_calls": 4,
            "claim_calls": 4,
            "queue_ready": 0,
            "queue_processing": 0,
        }
    ) == "\n".join(
        [
            "cycles=9",
            "jobs=16",
            "bindings_bound=4",
            "delayed_reposts_polled=4",
            "repost_calls=4",
            "claim_calls=4",
            "queue_ready=0",
            "queue_processing=0",
        ]
    )
