"""Queue-backed worker loop for the manager deployment."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import time

from .heartbeat import WorkerHeartbeat
from .queue import TaskQueue
from .scheduler import DispatchGrant, Scheduler
from .worker import TaskWorker


@dataclass(frozen=True)
class RunnerCycleResult:
    """Redacted result of one dispatch-and-consume cycle."""

    dispatched: int
    completed: int


@dataclass(frozen=True)
class RunnerDrainResult:
    """Summary of a bounded worker drain."""

    cycles: int
    dispatched: int
    completed: int


@dataclass(frozen=True)
class RunnerLoopResult:
    """Redacted summary returned after a worker receives a stop request."""

    cycles: int
    recovered: int
    dispatched: int
    completed: int
    errors: int


class TaskRunner:
    """Join scheduler, durable queue, and one-job worker behind one loop."""

    def __init__(
        self,
        *,
        scheduler: Scheduler,
        worker: TaskWorker,
        queue: TaskQueue,
        handler: Callable,
    ) -> None:
        self.scheduler = scheduler
        self.worker = worker
        self.queue = queue
        self.handler = handler

    def dispatch(self, *, limit: int | None = None) -> list[DispatchGrant]:
        """Lease eligible tasks and publish only successfully enqueued grants."""
        return self.scheduler.dispatch_to_queue(self.queue, limit=limit)

    def run_one(self, *, timeout: int = 0):
        """Consume one reliable-list message and acknowledge after completion."""
        message = self.queue.receive(timeout=timeout)
        if message is None:
            return None
        return self.worker.run_message(message, self.queue, self.handler)

    def run_cycle(
        self,
        *,
        dispatch_limit: int | None = None,
        max_jobs: int | None = None,
    ) -> RunnerCycleResult:
        """Dispatch a bounded window, then drain the messages available now."""
        dispatched = len(self.dispatch(limit=dispatch_limit))
        completed = 0
        while max_jobs is None or completed < max_jobs:
            if self.run_one() is None:
                break
            completed += 1
        return RunnerCycleResult(dispatched=dispatched, completed=completed)

    def run_until_idle(
        self,
        *,
        dispatch_limit: int | None = None,
        max_jobs_per_cycle: int | None = None,
        max_cycles: int = 100,
    ) -> RunnerDrainResult:
        """Drain immediately runnable work without spinning forever."""
        if max_cycles < 1:
            raise ValueError("max_cycles must be positive")

        total_dispatched = 0
        total_completed = 0
        cycles = 0
        while cycles < max_cycles:
            cycle = self.run_cycle(
                dispatch_limit=dispatch_limit,
                max_jobs=max_jobs_per_cycle,
            )
            cycles += 1
            total_dispatched += cycle.dispatched
            total_completed += cycle.completed
            if cycle.dispatched == 0 and cycle.completed == 0:
                break
        return RunnerDrainResult(
            cycles=cycles,
            dispatched=total_dispatched,
            completed=total_completed,
        )

    def run_forever(
        self,
        *,
        heartbeat: WorkerHeartbeat | None = None,
        stop_requested: Callable[[], bool] | None = None,
        dispatch_limit: int | None = None,
        receive_timeout: int = 1,
        recovery_interval_seconds: float = 30.0,
        heartbeat_interval_seconds: float = 15.0,
        idle_sleep_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> RunnerLoopResult:
        """Consume work until stopped while maintaining recovery and liveness."""
        if receive_timeout < 0:
            raise ValueError("receive_timeout must not be negative")
        if recovery_interval_seconds <= 0:
            raise ValueError("recovery_interval_seconds must be positive")
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        if idle_sleep_seconds <= 0:
            raise ValueError("idle_sleep_seconds must be positive")

        should_stop = stop_requested or (lambda: False)
        cycles = recovered = dispatched = completed = errors = 0
        last_recovery = monotonic() - recovery_interval_seconds

        try:
            if heartbeat is not None:
                heartbeat.start(heartbeat_interval_seconds)
            while not should_stop():
                current = monotonic()
                if current - last_recovery >= recovery_interval_seconds:
                    recovered += len(self.worker.recover_expired())
                    self._commit()
                    last_recovery = current

                dispatched += len(self.dispatch(limit=dispatch_limit))
                self._commit()
                try:
                    result = self.run_one(timeout=receive_timeout)
                except Exception:
                    # 单个任务失败已经回放到可靠队列，继续处理其他独立任务。
                    self._rollback()
                    errors += 1
                    sleep(idle_sleep_seconds)
                    continue

                cycles += 1
                if result is None:
                    sleep(idle_sleep_seconds)
                    continue
                self._commit()
                completed += 1
        finally:
            self.worker.shutdown()
            self._commit()
            if heartbeat is not None:
                heartbeat.close()

        return RunnerLoopResult(
            cycles=cycles,
            recovered=recovered,
            dispatched=dispatched,
            completed=completed,
            errors=errors,
        )

    def _commit(self) -> None:
        """Commit one durable lifecycle boundary for the long-lived session."""
        self.worker.session.commit()

    def _rollback(self) -> None:
        """Clear a failed transaction before the next queue iteration."""
        self.worker.session.rollback()
