#!/usr/bin/env python3
"""Recommend the next redacted stage command from current manager state."""

from __future__ import annotations

import argparse
import sys

from manager_api.config import get_settings
from manager_api.db.session import build_engine, session_scope
from manager_api.services.stage_status import collect_stage_status, recommend_next_stage


def main() -> int:
    """连接 manager 数据库并输出下一步建议。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=500, help="preview cap for status counts")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument(
        "--target-placeholder",
        default="REPOST_TARGET",
        help="placeholder used in the repost command suggestion",
    )
    args = parser.parse_args()
    if args.limit < 1:
        print("下一阶段建议失败: limit must be positive", file=sys.stderr)
        return 1

    try:
        engine = build_engine(get_settings())
        with session_scope(engine) as session:
            recommendation = recommend_next_stage(
                collect_stage_status(session, limit=args.limit),
                target_placeholder=args.target_placeholder,
            )
            if args.json:
                print(recommendation.to_json())
            else:
                print(f"action={recommendation.action}")
                print(f"stage={recommendation.stage or '-'}")
                print(f"reason={recommendation.reason}")
                print(f"command={recommendation.command}")
        engine.dispose()
    except ValueError as error:
        print(f"下一阶段建议失败: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
