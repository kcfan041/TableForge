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
    renew_margin: timedelta = timedelta(seconds=30)

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
        current_lease = lease

        def heartbeat(completed_hands: int) -> None:
            nonlocal current_lease
            del completed_hands
            now = self.clock.now()
            if current_lease.expires_at - now <= self.renew_margin:
                current_lease = self.coordinator.renew_job(
                    current_lease.job.spec.job_id,
                    current_lease.token,
                    now,
                    self.lease_duration,
                )

        output = self.simulation_executor.execute(lease.job.spec.payload, heartbeat)
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
