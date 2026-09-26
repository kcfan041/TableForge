from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from tableforge.domain.artifacts import ArtifactDescriptor, ArtifactKind
from tableforge.domain.capabilities import NodeCapability
from tableforge.domain.ids import NodeId, new_artifact_id
from tableforge.domain.jobs import JobLease, JobStatus
from tableforge.worker.client import CoordinatorClient


class Clock(Protocol):
    def now(self) -> datetime: ...


class JobExecutor(Protocol):
    def execute(
        self,
        payload_bytes: bytes,
        on_progress: Callable[[int], None] | None = None,
    ) -> bytes: ...


SIMULATION_PAYLOAD_TYPE = "tableforge.v1.SimulationJobPayload"


@dataclass(frozen=True, slots=True)
class OutputSpec:
    kind: ArtifactKind
    media_type: str
    schema_version: str


SIMULATION_OUTPUT = OutputSpec(
    ArtifactKind.EVALUATION_RESULT, "application/json", "tableforge.simulation-summary.v1"
)


class PlanAwareExecutor(Protocol):
    def execute_with_plan(
        self,
        payload_bytes: bytes,
        plan: Mapping[str, object],
        on_progress: Callable[[int], None] | None = None,
    ) -> bytes: ...


@dataclass(frozen=True, slots=True)
class RegisteredExecutor:
    executor: JobExecutor
    output: OutputSpec

    def run(
        self,
        payload: bytes,
        plan_json: str,
        on_progress: Callable[[int], None] | None,
    ) -> bytes:
        plan = json.loads(plan_json or "{}")
        if hasattr(self.executor, "execute_with_plan") and isinstance(plan, dict):
            executor: PlanAwareExecutor = self.executor  # type: ignore[assignment]
            return executor.execute_with_plan(payload, plan, on_progress)
        return self.executor.execute(payload, on_progress)


class JobCancelledError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class WorkerRuntime:
    node_id: NodeId
    capability: NodeCapability
    coordinator: CoordinatorClient
    clock: Clock
    lease_duration: timedelta
    simulation_executor: JobExecutor
    renew_margin: timedelta = timedelta(seconds=30)
    extra_executors: Mapping[str, RegisteredExecutor] = field(default_factory=dict)

    def _executor_for(self, payload_type: str) -> RegisteredExecutor | None:
        if payload_type == SIMULATION_PAYLOAD_TYPE:
            return RegisteredExecutor(self.simulation_executor, SIMULATION_OUTPUT)
        return self.extra_executors.get(payload_type)

    def run_once(self) -> JobLease | None:
        now = self.clock.now()
        self.coordinator.register_node(self.capability, now)
        self.coordinator.heartbeat_node(self.node_id, self.capability.revision, now)
        lease = self.coordinator.claim_job(
            self.node_id,
            self.capability,
            self.clock.now(),
            self.lease_duration,
        )
        if lease is None:
            return None
        registered = self._executor_for(lease.job.spec.payload_type)
        if registered is None:
            self.coordinator.fail_job(
                lease.job.spec.job_id,
                lease.token,
                self.clock.now(),
                "UNSUPPORTED_PAYLOAD",
                "Worker does not support the assigned payload type",
                retryable=False,
            )
            return lease
        current_lease = lease

        def heartbeat(completed_hands: int) -> None:
            nonlocal current_lease
            del completed_hands
            current = self.coordinator.get_job(current_lease.job.spec.job_id)
            if current.status is JobStatus.CANCEL_REQUESTED:
                raise JobCancelledError
            now = self.clock.now()
            if current_lease.expires_at - now <= self.renew_margin:
                current_lease = self.coordinator.renew_job(
                    current_lease.job.spec.job_id,
                    current_lease.token,
                    now,
                    self.lease_duration,
                )

        try:
            output = registered.run(lease.job.spec.payload, lease.execution_plan_json, heartbeat)
        except JobCancelledError:
            self.coordinator.acknowledge_cancel(
                current_lease.job.spec.job_id,
                current_lease.token,
                self.clock.now(),
            )
            return lease
        except (ValueError, AssertionError) as error:
            self.coordinator.fail_job(
                current_lease.job.spec.job_id,
                current_lease.token,
                self.clock.now(),
                type(error).__name__.upper(),
                _safe_error_summary(error),
                retryable=False,
            )
            return lease
        except Exception as error:
            out_of_memory = _is_out_of_memory(error)
            self.coordinator.fail_job(
                current_lease.job.spec.job_id,
                current_lease.token,
                self.clock.now(),
                "GPU_OUT_OF_MEMORY" if out_of_memory else "EXECUTOR_ERROR",
                _oom_summary(error, lease.execution_plan_json)
                if out_of_memory
                else _safe_error_summary(error),
                retryable=True,
            )
            return lease
        artifact = self.coordinator.upload_artifact(
            ArtifactDescriptor(
                artifact_id=new_artifact_id(),
                kind=registered.output.kind,
                media_type=registered.output.media_type,
                schema_version=registered.output.schema_version,
            ),
            output,
        )
        self.coordinator.complete_job(
            current_lease.job.spec.job_id,
            current_lease.token,
            self.clock.now(),
            artifact.sha256,
            (artifact,),
        )
        return lease


def _is_out_of_memory(error: BaseException) -> bool:
    if type(error).__name__ == "OutOfMemoryError":
        return True
    message = str(error).lower()
    return "out of memory" in message and ("cuda" in message or "gpu" in message)


def _oom_summary(error: Exception, plan_json: str) -> str:
    peak: int | None = None
    try:
        import torch

        if torch.cuda.is_available():
            peak = int(torch.cuda.max_memory_allocated())
            torch.cuda.empty_cache()
    except Exception:
        peak = None
    return json.dumps(
        {
            "error": _safe_error_summary(error)[:200],
            "execution_plan": json.loads(plan_json or "{}"),
            "peak_gpu_memory_bytes": peak,
        },
        sort_keys=True,
    )[:500]


def _safe_error_summary(error: Exception) -> str:
    summary = str(error).replace("\r", " ").replace("\n", " ").strip()
    return (summary or type(error).__name__)[:500]
