from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from tableforge.application.ports import LeaseConflictError, LeaseFencedError
from tableforge.worker.runtime import WorkerRuntime


def _code(error: BaseException) -> str:
    return str(getattr(error, "code", "") or "")


def _is_transient(error: BaseException) -> bool:
    transient = getattr(error, "transient", None)
    if isinstance(transient, bool):
        return transient
    return isinstance(error, ConnectionError | TimeoutError | OSError)


def _is_fatal(error: BaseException) -> bool:
    fatal = getattr(error, "fatal", None)
    return fatal is True


def _fatal_hint(error: BaseException) -> str:
    code = _code(error)
    if code == "UNAUTHENTICATED":
        return (
            "the coordinator rejected this node's credential (revoked or expired); ask the "
            "admin for a new invite and join again with a fresh --data-dir"
        )
    if code == "INCOMPATIBLE_VERSION":
        return str(error)
    return f"the coordinator refused this worker ({code}): {error}"


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


class WorkerStoppedError(Exception):
    """The worker cannot continue without a person fixing something (e.g. revoked node)."""


@dataclass(frozen=True, slots=True)
class WorkerLoop:
    runtime: WorkerRuntime
    logger: EventLogger
    idle_seconds: float = 1.0
    sleep: Callable[[float], None] = time.sleep
    on_idle: Callable[[], bool] | None = None
    max_backoff_seconds: float = 60.0
    before_iteration: Callable[[], None] | None = None

    def run(
        self,
        should_stop: Callable[[], bool] = lambda: False,
        max_jobs: int | None = None,
    ) -> int:
        """Claim and run jobs until stopped.

        Transient coordinator/network failures never end the loop: the worker backs off
        (1s doubling to ``max_backoff_seconds``) and logs only the transitions between
        reachable and unreachable. A lost lease (the job was reassigned while we were away)
        is logged and skipped. Fatal errors such as a revoked credential raise
        ``WorkerStoppedError`` with a message saying what to do.
        """
        completed = 0
        idle = False
        backoff = 0.0
        self.logger.emit("worker_started", node_id=str(self.runtime.node_id))
        while not should_stop():
            try:
                if self.before_iteration is not None:
                    self.before_iteration()
                lease = self.runtime.run_once()
            except (LeaseFencedError, LeaseConflictError) as error:
                self.logger.emit(
                    "lease_lost",
                    node_id=str(self.runtime.node_id),
                    detail=str(error)[:200],
                )
                continue
            except Exception as error:
                if _is_fatal(error):
                    self.logger.emit(
                        "worker_stopping",
                        node_id=str(self.runtime.node_id),
                        reason=str(error)[:300],
                    )
                    raise WorkerStoppedError(_fatal_hint(error)) from error
                if not _is_transient(error):
                    raise
                if backoff == 0.0:
                    self.logger.emit(
                        "coordinator_unreachable",
                        node_id=str(self.runtime.node_id),
                        detail=str(error)[:200],
                    )
                backoff = min(max(1.0, backoff * 2), self.max_backoff_seconds)
                if max_jobs is not None:
                    break
                self.sleep(backoff)
                continue
            if backoff:
                self.logger.emit("coordinator_reconnected", node_id=str(self.runtime.node_id))
                backoff = 0.0
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
            try:
                status = self.runtime.coordinator.get_job(lease.job.spec.job_id).status.value
            except Exception as error:
                if not _is_transient(error):
                    raise
                status = "unknown"
            self.logger.emit(
                "job_finished",
                attempt_id=str(lease.attempt_id),
                experiment_id=str(lease.job.spec.experiment_id),
                job_id=str(lease.job.spec.job_id),
                node_id=str(self.runtime.node_id),
                payload_type=lease.job.spec.payload_type,
                status=status,
            )
            if max_jobs is not None and completed >= max_jobs:
                break
        self.logger.emit("worker_stopped", node_id=str(self.runtime.node_id), jobs=completed)
        return completed
