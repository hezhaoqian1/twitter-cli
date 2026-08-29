#!/usr/bin/env python3
"""Print a redacted staged-operation status snapshot."""

from __future__ import annotations

import argparse
import sys


from manager_api.config import get_settings
from manager_api.db.session import build_engine, session_scope
from manager_api.services.stage_status import StageStatusSummary, collect_stage_status


def _print_table(summary: StageStatusSummary) -> None:
    """输出便于终端查看的阶段表，只展示聚合数量。"""
    resources = summary.resources
    print(
        "resources "
        f"accounts_active={resources['accounts_active']} "
        f"accounts_healthy={resources['accounts_healthy']} "
        f"wallets_active={resources['wallets_active']} "
        f"bindings_pending={resources['bindings_pending']} "
        f"bindings_bound={resources['bindings_bound']}"
    )
    print("stage\tready\twaiting\tfailed\tpollable\tsyncable\tretryable")
    for row in summary.stages:
        print(
            f"{row.stage}\t{row.ready}\t{row.waiting}\t{row.failed}\t"
            f"{row.pollable}\t{row.status_syncable}\t{row.retryable}"
        )


def main() -> int:
    """连接 manager 数据库并输出阶段化操作快照。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=500, help="preview cap for poll/retry counts")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()
    if args.limit < 1:
        print("阶段状态读取失败: limit must be positive", file=sys.stderr)
        return 1

    try:
        engine = build_engine(get_settings())
        with session_scope(engine) as session:
            summary = collect_stage_status(session, limit=args.limit)
            if args.json:
                print(summary.to_json())
            else:
                _print_table(summary)
        engine.dispose()
    except ValueError as error:
        print(f"阶段状态读取失败: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
