"""Queue-backed worker loop for the manager deployment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

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
