from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tableforge.capability.workloads import BUILTIN_WORKLOADS
from tableforge.coordinator.service import CoordinatorService
from tableforge.domain.artifacts import Artifact, ArtifactDescriptor
from tableforge.domain.capabilities import (
    NodeCapability,
    RuntimeCapability,
    StorageCapability,
)
from tableforge.domain.ids import JobId, NodeId, new_experiment_id, new_job_id
from tableforge.domain.jobs import Job, JobLease, JobSpec, JobStatus, JobType
from tableforge.domain.simulation import SimulationJobPayload
from tableforge.infrastructure.local_artifacts import LocalArtifactStore
from tableforge.infrastructure.sqlite.jobs import SQLiteJobRepository
from tableforge.worker.client import CoordinatorClient
from tableforge.worker.runtime import WorkerRuntime
from tableforge.worker.simulation import SimulationExecutor

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class RecordingClient:
    def __init__(self, inner: CoordinatorService) -> None:
        self.inner = inner
        self.calls: list[str] = []

    def register_node(self, capability: NodeCapability, now: datetime) -> None:
        self.calls.append("register_node")
        self.inner.register_node(capability, now)

    def heartbeat_node(self, node_id: NodeId, revision: int, now: datetime) -> None:
        self.calls.append("heartbeat_node")
        self.inner.heartbeat_node(node_id, revision, now)

    def claim_job(
        self,
        node_id: NodeId,
        capability: NodeCapability,
        now: datetime,
        duration: timedelta,
    ) -> JobLease | None:
        self.calls.append("claim_job")
        return self.inner.claim_job(node_id, capability, now, duration)

    def get_job(self, job_id: JobId) -> Job:
        self.calls.append("get_job")
        return self.inner.get_job(job_id)

    def renew_job(
        self, job_id: JobId, token: str, now: datetime, duration: timedelta
    ) -> JobLease:
        self.calls.append("renew_job")
        return self.inner.renew_job(job_id, token, now, duration)

    def complete_job(
        self,
        job_id: JobId,
        token: str,
        now: datetime,
        result_hash: str,
        outputs: tuple[Artifact, ...] = (),
    ) -> Job:
        self.calls.append("complete_job")
        return self.inner.complete_job(job_id, token, now, result_hash, outputs)

    def fail_job(
        self,
        job_id: JobId,
        token: str,
        now: datetime,
        error_code: str,
        error_summary: str,
        retryable: bool,
        diagnostic_artifact_id: str | None = None,
    ) -> Job:
        self.calls.append("fail_job")
        return self.inner.fail_job(
            job_id, token, now, error_code, error_summary, retryable, diagnostic_artifact_id
        )

    def acknowledge_cancel(self, job_id: JobId, token: str, now: datetime) -> Job:
        self.calls.append("acknowledge_cancel")
        return self.inner.acknowledge_cancel(job_id, token, now)

    def upload_artifact(self, descriptor: ArtifactDescriptor, content: bytes) -> Artifact:
        self.calls.append("upload_artifact")
        return self.inner.upload_artifact(descriptor, content)

    def read_artifact(self, artifact_id: str) -> bytes:
        self.calls.append("read_artifact")
        return self.inner.read_artifact(artifact_id)


class Clock:
    def now(self) -> datetime:
        return NOW


def test_coordinator_service_satisfies_client_protocol(tmp_path: Path) -> None:
    service: CoordinatorClient = CoordinatorService(
        jobs=SQLiteJobRepository(tmp_path / "db.sqlite"),
        artifacts=LocalArtifactStore(tmp_path / "artifacts"),
        workloads=BUILTIN_WORKLOADS,
    )
    assert service is not None


def test_worker_runs_through_client_protocol_only(tmp_path: Path) -> None:
    inner = CoordinatorService(
        jobs=SQLiteJobRepository(tmp_path / "db.sqlite"),
        artifacts=LocalArtifactStore(tmp_path / "artifacts"),
        workloads=BUILTIN_WORKLOADS,
    )
    profile = BUILTIN_WORKLOADS[0]
    spec = JobSpec(
        schema_version="1.0",
        job_id=new_job_id(),
        experiment_id=new_experiment_id(),
        type=JobType.SIMULATION,
        priority=1,
        resources=profile.minimum,
        payload=SimulationJobPayload("1.0", hands=20, seed_start=0).to_bytes(),
        payload_type=profile.payload_type,
        idempotency_key="client-protocol",
    )
    inner.create_job(spec)
    client = RecordingClient(inner)
    capability = NodeCapability(
        schema_version="1.0",
        node_id=NodeId("node-a"),
        revision=1,
        operating_system="test",
        architecture="x86_64",
        logical_cpu_cores=4,
        physical_cpu_cores=2,
        total_memory_bytes=8 * 1024**3,
        available_memory_bytes=8 * 1024**3,
        gpus=(),
        storage=(StorageCapability("data", 20 * 1024**3),),
        runtime=RuntimeCapability("3.12", ("cpu",)),
        enabled_roles=("simulation",),
        captured_at=NOW,
    )

    WorkerRuntime(
        node_id=NodeId("node-a"),
        capability=capability,
        coordinator=client,
        clock=Clock(),
        lease_duration=timedelta(minutes=5),
        simulation_executor=SimulationExecutor(),
    ).run_once()

    assert inner.get_job(spec.job_id).status is JobStatus.SUCCEEDED
    assert client.calls[:3] == ["register_node", "heartbeat_node", "claim_job"]
    assert client.calls[-2:] == ["upload_artifact", "complete_job"]
