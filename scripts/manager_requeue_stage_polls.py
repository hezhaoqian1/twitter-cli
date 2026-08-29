#!/usr/bin/env python3
"""Preview or requeue waiting stage polls without replaying external actions."""

from __future__ import annotations

import argparse
import sys

from manager_api.config import get_settings
from manager_api.db.session import build_engine, session_scope
from manager_api.services.stage_polls import STAGE_TO_KIND, StagePollRequeueSummary, requeue_stage_polls
from manager_api.services.tasks import TaskError


def _format_summary(summary: StagePollRequeueSummary) -> str:
    """输出稳定 key=value，不含账号、地址、目标或外部引用。"""
    return "\n".join(
        [
            f"stage={summary.stage}",
            f"selected={summary.selected}",
            f"requeued={summary.requeued}",
            f"skipped_missing_ref={summary.skipped_missing_ref}",
            f"apply={str(summary.apply).lower()}",
        ]
    )


def main() -> int:
    """连接 manager 数据库并预览或重新入队等待轮询。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=sorted(STAGE_TO_KIND))
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="requeue selected waiting tasks; omit to preview only",
    )
    args = parser.parse_args()

    try:
        engine = build_engine(get_settings())
        with session_scope(engine) as session:
            summary = requeue_stage_polls(
                session,
                stage=args.stage,
                limit=args.limit,
                apply=args.apply,
            )
            print(_format_summary(summary))
        engine.dispose()
    except (TaskError, ValueError) as error:
        print(f"重新入队阶段轮询失败: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
