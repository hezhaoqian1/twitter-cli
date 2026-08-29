#!/usr/bin/env python3
"""Create a bind stage from account/private-key files using exact row pairing."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from manager_api.config import get_settings
from manager_api.db.session import build_engine, session_scope
from manager_api.services.paired_bindings import (
    PairingDecision,
    PairingSummary,
    create_bound_pair_batch,
    select_bound_pairs,
)
from manager_api.services.wallets import WalletInputError

__all__ = [
    "PairingDecision",
    "PairingSummary",
    "create_bound_pair_batch_from_files",
    "select_bound_pairs_from_files",
]


def _read_text(path: Path) -> str:
    """读取 UTF-8 文件，避免把输入内容打印到日志。"""
    return path.read_text(encoding="utf-8")


def select_bound_pairs_from_files(
    session,
    *,
    accounts_file: Path,
    private_keys_file: Path,
    limit: int,
    include_unverified: bool = False,
) -> list[PairingDecision]:
    """按两个文件的同一行配对，返回脱敏选择结果。"""
    return select_bound_pairs(
        session,
        accounts_content=_read_text(accounts_file),
        private_keys_content=_read_text(private_keys_file),
        limit=limit,
        include_unverified=include_unverified,
    )


def create_bound_pair_batch_from_files(
    session,
    *,
    accounts_file: Path,
    private_keys_file: Path,
    name: str,
    limit: int,
    dispatch_limit: int,
    include_unverified: bool = False,
    dry_run: bool = False,
) -> PairingSummary:
    """从精确行配对创建一个 bind 阶段批次，且不串联后续阶段。"""
    return create_bound_pair_batch(
        session,
        accounts_content=_read_text(accounts_file),
        private_keys_content=_read_text(private_keys_file),
        name=name,
        limit=limit,
        dispatch_limit=dispatch_limit,
        include_unverified=include_unverified,
        apply=not dry_run,
    )


def main() -> int:
    """命令行入口：创建按行配对的绑定阶段批次。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accounts-file", type=Path, required=True)
    parser.add_argument("--private-keys-file", type=Path, required=True)
    parser.add_argument("--name", default="bind paired rows")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--dispatch-limit", type=int, default=10)
    parser.add_argument(
        "--include-unverified",
        action="store_true",
        help="allow unknown/degraded accounts in the paired bind batch",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print selection counts without creating bindings or tasks",
    )
    args = parser.parse_args()

    try:
        engine = build_engine(get_settings())
        with session_scope(engine) as session:
            summary = create_bound_pair_batch_from_files(
                session,
                accounts_file=args.accounts_file,
                private_keys_file=args.private_keys_file,
                name=args.name,
                limit=args.limit,
                dispatch_limit=args.dispatch_limit,
                include_unverified=args.include_unverified,
                dry_run=args.dry_run,
            )
            print(summary.to_json())
        engine.dispose()
    except (OSError, ValueError, WalletInputError) as error:
        print(f"创建配对绑定批次失败: {type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
