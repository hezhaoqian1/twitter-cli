#!/usr/bin/env python3
"""Run the manager Alembic migrations against DATABASE_URL."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    """Delegate to Alembic from the repository root."""
    root = Path(__file__).resolve().parents[1]
    return subprocess.call(
        [sys.executable, "-m", "alembic", "-c", str(root / "alembic.ini"), "upgrade", "head"],
        cwd=root,
    )


if __name__ == "__main__":
    raise SystemExit(main())
