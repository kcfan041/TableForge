from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from tableforge.worker.runtime import WorkerRuntime


class EventLogger(Protocol):
    def emit(self, event: str, **fields: object) -> None: ...


def _print_line(line: str) -> None:
    print(line, flush=True)


@dataclass(frozen=True, slots=True)
class JsonLineLogger:
    sink: Callable[[str], None] = _print_line
    context: tuple[tuple[str, object], ...] = ()

    def emit(self, event: str, **fields: object) -> None:
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            **dict(self.context),
            "event": event,
            **fields,
        }
        self.sink(json.dumps(record, sort_keys=True, default=str))

    def bind(self, **fields: object) -> JsonLineLogger:
        merged = {**dict(self.context), **fields}
        return JsonLineLogger(self.sink, tuple(sorted(merged.items())))


@dataclass(frozen=True, slots=True)
class WorkerLoop:
    runtime: WorkerRuntime
    logger: EventLogger
    idle_seconds: float = 1.0
    sleep: Callable[[float], None] = time.sleep
    on_idle: Callable[[], bool] | None = None

    def run(
        self,
        should_stop: Callable[[], bool] = lambda: False,
        max_jobs: int | None = None,
    ) -> int:
        completed = 0
        idle = False
        self.logger.emit("worker_started", node_id=str(self.runtime.node_id))
        while not should_stop():
            lease = self.runtime.run_once()
            if lease is None:
                if self.on_idle is not None and self.on_idle():
                    self.logger.emit("experiments_advanced", node_id=str(self.runtime.node_id))
                    continue
                if not idle:
                    self.logger.emit("worker_idle", node_id=str(self.runtime.node_id))
                    idle = True
                if max_jobs is not None:
                    break
                self.sleep(self.idle_seconds)
                continue
            idle = False
            completed += 1
            job = self.runtime.coordinator.get_job(lease.job.spec.job_id)
            self.logger.emit(
                "job_finished",
                attempt_id=str(lease.attempt_id),
                experiment_id=str(lease.job.spec.experiment_id),
                job_id=str(lease.job.spec.job_id),
                node_id=str(self.runtime.node_id),
                payload_type=lease.job.spec.payload_type,
                status=job.status.value,
            )
            if max_jobs is not None and completed >= max_jobs:
                break
        self.logger.emit("worker_stopped", node_id=str(self.runtime.node_id), jobs=completed)
        return completed
