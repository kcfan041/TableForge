from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from tableforge.coordinator.service import CoordinatorService
from tableforge.domain.artifacts import ArtifactDescriptor, ArtifactKind
from tableforge.domain.capabilities import NodeCapability
from tableforge.domain.ids import NodeId, new_artifact_id
from tableforge.domain.jobs import JobLease
from tableforge.worker.simulation import SimulationExecutor


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class WorkerRuntime:
    node_id: NodeId
    capability: NodeCapability
    coordinator: CoordinatorService
    clock: Clock
    lease_duration: timedelta
    simulation_executor: SimulationExecutor

    def run_once(self) -> JobLease | None:
        lease = self.coordinator.claim_job(
            self.node_id,
            self.capability,
            self.clock.now(),
            self.lease_duration,
        )
        if lease is None:
            return None
        if lease.job.spec.payload_type != "tableforge.v1.SimulationJobPayload":
            raise ValueError(f"unsupported payload type: {lease.job.spec.payload_type}")
        output = self.simulation_executor.execute(lease.job.spec.payload)
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
            lease.job.spec.job_id,
            lease.token,
            self.clock.now(),
            artifact.sha256,
        )
        return lease
