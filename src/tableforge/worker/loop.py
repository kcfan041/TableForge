from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from tableforge.worker.runtime import WorkerRuntime


class EventLogger(Protocol):
    def emit(self, event: str, **fields: object) -> None: ...


@dataclass(frozen=True, slots=True)
class JsonLineLogger:
    sink: Callable[[str], None] = print

    def emit(self, event: str, **fields: object) -> None:
        self.sink(json.dumps({"event": event, **fields}, sort_keys=True, default=str))


@dataclass(frozen=True, slots=True)
class WorkerLoop:
    runtime: WorkerRuntime
    logger: EventLogger
    idle_seconds: float = 1.0
    sleep: Callable[[float], None] = time.sleep

    def run(
        self,
        should_stop: Callable[[], bool] = lambda: False,
        max_jobs: int | None = None,
    ) -> int:
        completed = 0
        self.logger.emit("worker_started", node_id=str(self.runtime.node_id))
        while not should_stop():
            lease = self.runtime.run_once()
            if lease is None:
                self.logger.emit("worker_idle", node_id=str(self.runtime.node_id))
                if max_jobs is not None:
                    break
                self.sleep(self.idle_seconds)
                continue
            completed += 1
            job = self.runtime.coordinator.get_job(lease.job.spec.job_id)
            self.logger.emit(
                "job_finished",
                attempt_id=str(lease.attempt_id),
                job_id=str(lease.job.spec.job_id),
                status=job.status.value,
            )
            if max_jobs is not None and completed >= max_jobs:
                break
        self.logger.emit("worker_stopped", node_id=str(self.runtime.node_id), jobs=completed)
        return completed
