#!/usr/bin/env python3
"""Start one deployable manager worker process."""

from __future__ import annotations

import importlib
import os
import signal
from threading import Event
from types import ModuleType
from typing import Any, cast

import redis

from manager_api.adapters.kredo_adapter import KredoAdapter
from manager_api.adapters.x_adapter import XAdapter, build_twitter_client_factory
from manager_api.config import ManagerSettings, get_settings
from manager_api.db.session import build_engine, session_factory
from manager_api.heartbeat import HeartbeatRedisClient, WorkerHeartbeat
from manager_api.queue import RedisTaskQueue
from manager_api.runner import TaskRunner
from manager_api.scheduler import Scheduler
from manager_api.services.execution import ExecutionConfig, TaskExecutionService
from manager_api.services.vault import VaultService
from manager_api.worker import TaskWorker


def _load_symbol(spec: str) -> Any:
    """Load a provider factory from ``module:attribute`` without logging secrets."""
    module_name, separator, attribute_name = spec.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError("factory must use module:attribute notation")
    module: ModuleType = importlib.import_module(module_name)
    return getattr(module, attribute_name)


def build_runner(
    settings: ManagerSettings,
    *,
    session,
    redis_client,
    x_adapter,
    kredo_workflow_factory,
) -> TaskRunner:
    """Assemble the production runner from environment-backed dependencies."""
    vault = VaultService(
        session,
        cache_ttl_seconds=settings.vault_cache_ttl_seconds,
    )
    worker_password = settings.worker_vault_password
    if worker_password:
        vault.unlock_with_password(worker_password.get_secret_value())

    execution = TaskExecutionService(
        session,
        vault=vault,
        x_adapter=x_adapter,
        kredo_adapter=KredoAdapter(kredo_workflow_factory),
        config=ExecutionConfig(
            poll_delay_seconds=max(1, int(settings.external_poll_interval_seconds)),
        ),
    )
    scheduler = Scheduler(
        session,
        lease_ttl_seconds=settings.worker_lease_ttl_seconds,
        worker_concurrency=settings.worker_concurrency,
        browser_concurrency=settings.browser_concurrency,
    )
    return TaskRunner(
        scheduler=scheduler,
        worker=TaskWorker(session, scheduler=scheduler),
        queue=RedisTaskQueue(redis_client),
        handler=execution.handle,
    )


def main() -> int:
    """Run until Railway or the local supervisor sends a termination signal."""
    settings = get_settings()
    x_factory_spec = os.environ.get("MANAGER_X_ADAPTER_FACTORY", "").strip()
    factory_spec = os.environ.get("MANAGER_KREDO_WORKFLOW_FACTORY", "").strip()
    if not factory_spec:
        raise RuntimeError("MANAGER_KREDO_WORKFLOW_FACTORY is required for the worker")

    engine = build_engine(settings)
    session = session_factory(engine)()
    redis_client = redis.Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        health_check_interval=30,
    )
    stop_event = Event()

    def request_stop(signum: int, _frame: Any) -> None:
        """将 TERM/INT 转换成循环可观察的停止状态。"""
        del signum
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    try:
        if x_factory_spec:
            x_adapter = _load_symbol(x_factory_spec)()
        else:
            x_adapter = XAdapter(build_twitter_client_factory())
        runner = build_runner(
            settings,
            session=session,
            redis_client=redis_client,
            x_adapter=x_adapter,
            kredo_workflow_factory=_load_symbol(factory_spec),
        )
        runner.run_forever(
            heartbeat=WorkerHeartbeat(cast(HeartbeatRedisClient, redis_client)),
            stop_requested=stop_event.is_set,
            dispatch_limit=settings.worker_concurrency,
            receive_timeout=1,
            recovery_interval_seconds=settings.worker_recovery_interval_seconds,
            heartbeat_interval_seconds=settings.worker_heartbeat_interval_seconds,
            idle_sleep_seconds=settings.worker_idle_sleep_seconds,
        )
    finally:
        session.close()
        redis_client.close()
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
