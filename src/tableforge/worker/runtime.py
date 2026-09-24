from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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
        if lease.job.spec.payload_type != "tableforge.v1.SimulationJobPayload":
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
            output = self.simulation_executor.execute(lease.job.spec.payload, heartbeat)
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
            self.coordinator.fail_job(
                current_lease.job.spec.job_id,
                current_lease.token,
                self.clock.now(),
                "EXECUTOR_ERROR",
                _safe_error_summary(error),
                retryable=True,
            )
            return lease
        artifact = self.coordinator.upload_artifact(
            ArtifactDescriptor(
                artifact_id=new_artifact_id(),
                kind=ArtifactKind.EVALUATION_RESULT,
                media_type="application/json",
                schema_version="tableforge.simulation-summary.v1",
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


def _safe_error_summary(error: Exception) -> str:
    summary = str(error).replace("\r", " ").replace("\n", " ").strip()
    return (summary or type(error).__name__)[:500]
