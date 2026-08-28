#!/usr/bin/env python3
"""Create, verify, or restore an encrypted manager backup package."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from manager_api.config import get_settings
from manager_api.db.session import build_engine, session_scope
from manager_api.services.backup import (
    BackupError,
    BackupFormatError,
    create_backup,
    restore_backup,
    verify_backup,
    write_backup,
)
from manager_api.services.vault import VaultService, VaultUnlockError


def _recovery_key(value: str | None) -> str:
    """Read the recovery key from an explicit argument, environment, or prompt."""
    return value or os.environ.get("MANAGER_RECOVERY_KEY") or getpass.getpass(
        "Vault recovery key: "
    )


def _summary(result: object) -> str:
    """Render only redacted backup metadata for terminal output."""
    return json.dumps(
        {
            key: getattr(result, key)
            for key in (
                "format_version",
                "table_count",
                "row_count",
                "vault_recovery_key_valid",
                "checksums_valid",
            )
            if hasattr(result, key)
        },
        sort_keys=True,
    )


def main() -> int:
    """Run one backup operation against the configured database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("create", "verify", "restore"))
    parser.add_argument("package", type=Path)
    parser.add_argument("--recovery-key", dest="recovery_key")
    args = parser.parse_args()
    recovery_key = _recovery_key(args.recovery_key)
    engine = build_engine(get_settings())

    try:
        with session_scope(engine) as session:
            vault = VaultService(session)
            if args.operation == "create":
                package = create_backup(session, vault, recovery_key)
                write_backup(args.package, package)
                result = verify_backup(session, vault, package, recovery_key)
                print(json.dumps({"path": str(args.package), **json.loads(_summary(result))}))
            elif args.operation == "verify":
                result = verify_backup(session, vault, args.package.read_bytes(), recovery_key)
                print(_summary(result))
            else:
                result = restore_backup(session, vault, args.package.read_bytes(), recovery_key)
                print(_summary(result))
    except (BackupError, BackupFormatError, VaultUnlockError, OSError) as exc:
        print(f"manager backup {args.operation} failed: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
