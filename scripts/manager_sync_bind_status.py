#!/usr/bin/env python3
"""Preview or queue read-only Kredo binding status sync jobs."""

from __future__ import annotations

import argparse
import sys

from manager_api.config import get_settings
from manager_api.db.session import build_engine, session_scope
from manager_api.services.bind_status_sync import BindStatusSyncSummary, queue_bind_status_sync


def _format_summary(summary: BindStatusSyncSummary) -> str:
    """输出稳定 key=value，不包含账号、地址、Cookie、token 或私钥。"""
    return "\n".join(
        [
            f"name={summary.name}",
            f"pending_bindings={summary.pending_bindings}",
            f"selected={summary.selected}",
            f"created_jobs={summary.created_jobs}",
            f"reused_jobs={summary.reused_jobs}",
            f"paused_action_jobs={summary.paused_action_jobs}",
            f"skipped_existing_status_job={summary.skipped_existing_status_job}",
            f"skipped_active_lease={summary.skipped_active_lease}",
            f"skipped_missing_secret={summary.skipped_missing_secret}",
            f"apply={str(summary.apply).lower()}",
        ]
    )


def main() -> int:
    """连接 manager 数据库并预览或创建只读绑定状态同步任务。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="bind status sync")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--dispatch-limit", type=int, default=10)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="queue selected status-only bind jobs; omit to preview only",
    )
    args = parser.parse_args()

    try:
        engine = build_engine(get_settings())
        with session_scope(engine) as session:
            summary = queue_bind_status_sync(
                session,
                name=args.name,
                limit=args.limit,
                dispatch_limit=args.dispatch_limit,
                apply=args.apply,
            )
            print(_format_summary(summary))
        engine.dispose()
    except ValueError as error:
        print(f"绑定状态同步失败: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
