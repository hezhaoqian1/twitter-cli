#!/usr/bin/env python3
"""Print a read-only staged acceptance audit for manager operations."""

from __future__ import annotations

import argparse
import sys

from manager_api.config import get_settings
from manager_api.db.session import build_engine, session_scope
from manager_api.services.acceptance import AcceptanceAudit, collect_acceptance_audit


def _print_table(audit: AcceptanceAudit) -> None:
    """输出阶段验收表和下一步动作，不包含任何敏感原文。"""
    resources = audit.resources
    print(
        "resources "
        f"accounts_active={resources['accounts_active']} "
        f"accounts_healthy={resources['accounts_healthy']} "
        f"wallets_active={resources['wallets_active']} "
        f"bindings_pending={resources['bindings_pending']} "
        f"bindings_bound={resources['bindings_bound']}"
    )
    print("stage\tready\twaiting\tfailed\tpollable\tsyncable\tretryable")
    for row in audit.stages:
        print(
            f"{row.stage}\t{row.ready}\t{row.waiting}\t{row.failed}\t"
            f"{row.pollable}\t{row.status_syncable}\t{row.retryable}"
        )
    print("next")
    print(f"action={audit.next_action.action}")
    print(f"stage={audit.next_action.stage or '-'}")
    print(f"reason={audit.next_action.reason}")
    print(f"command={audit.next_action.command}")
    if audit.actions:
        print("actions")
        for action in audit.actions:
            print(
                f"{action.action}\t{action.stage}\tcount={action.count}\t"
                f"command={action.command}"
            )


def main() -> int:
    """连接 manager 数据库并输出只读验收审计。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument(
        "--target-placeholder",
        default="REPOST_TARGET",
        help="placeholder used in repost command suggestions",
    )
    args = parser.parse_args()

    try:
        engine = build_engine(get_settings())
        with session_scope(engine) as session:
            audit = collect_acceptance_audit(
                session,
                limit=args.limit,
                target_placeholder=args.target_placeholder,
            )
            if args.json:
                print(audit.to_json())
            else:
                _print_table(audit)
        engine.dispose()
    except ValueError as error:
        print(f"验收审计失败: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
