#!/usr/bin/env python3
"""Run a bounded manager worker drain for local or server-side operations."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import asdict

import redis

from manager_api.config import get_settings
from manager_api.db.session import build_engine, session_factory
from scripts.manager_worker import (
    _load_symbol,
    apply_worker_environment,
    build_runner,
    build_x_adapter,
    required_kredo_factory_spec,
)


def _format_summary(result: object) -> str:
    """Render drain result as stable Chinese-friendly log lines."""
    values = asdict(result)
    return "\n".join(f"{key}={values[key]}" for key in ("cycles", "dispatched", "completed"))


def main() -> int:
    """Connect to configured dependencies, drain runnable work, and exit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-cycles", type=int, default=20)
    parser.add_argument("--dispatch-limit", type=int)
    parser.add_argument("--max-jobs-per-cycle", type=int)
    args = parser.parse_args()

    settings = get_settings()
    apply_worker_environment(settings)
    x_factory_spec = os.environ.get(
        "MANAGER_X_ADAPTER_FACTORY",
        getattr(settings, "manager_x_adapter_factory", ""),
    ).strip()
    try:
        factory_spec = required_kredo_factory_spec(settings)
        engine = build_engine(settings)
        session = session_factory(engine)()
        redis_client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            health_check_interval=30,
        )
        try:
            runner = build_runner(
                settings,
                session=session,
                redis_client=redis_client,
                x_adapter=build_x_adapter(x_factory_spec),
                kredo_workflow_factory=_load_symbol(factory_spec),
            )
            result = runner.run_until_idle(
                dispatch_limit=args.dispatch_limit,
                max_jobs_per_cycle=args.max_jobs_per_cycle,
                max_cycles=args.max_cycles,
            )
            session.commit()
            print(_format_summary(result))
        finally:
            session.close()
            redis_client.close()
            engine.dispose()
    except Exception as error:
        print(f"worker drain 失败: {type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
