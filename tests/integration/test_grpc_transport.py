from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tableforge._generated.tableforge.v1 import common_pb2, coordinator_pb2
from tableforge.application.ports import LeaseFencedError
from tableforge.capability.workloads import BUILTIN_WORKLOADS
from tableforge.coordinator.service import CoordinatorService
from tableforge.domain.artifacts import ArtifactDescriptor, ArtifactKind
from tableforge.domain.capabilities import (
    NodeCapability,
    RuntimeCapability,
    StorageCapability,
)
from tableforge.domain.ids import NodeId, new_artifact_id, new_experiment_id, new_job_id
from tableforge.domain.jobs import JobSpec, JobStatus, JobType
from tableforge.domain.simulation import SimulationJobPayload
from tableforge.infrastructure.local_artifacts import LocalArtifactStore
from tableforge.infrastructure.sqlite.auth import SQLiteAuthRepository
from tableforge.infrastructure.sqlite.jobs import SQLiteJobRepository
from tableforge.infrastructure.sqlite.nodes import SQLiteNodeRepository
from tableforge.transport.client import GrpcCoordinatorClient, create_channel, enroll
from tableforge.transport.errors import ERROR_METADATA_KEY, RemoteCoordinatorError
from tableforge.transport.server import RunningServer, start_server
from tableforge.transport.tls import generate_self_signed
from tableforge.worker.runtime import WorkerRuntime
from tableforge.worker.simulation import SimulationExecutor


@dataclass
class MutableClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


@dataclass
class Harness:
    service: CoordinatorService
    auth: SQLiteAuthRepository
    server: RunningServer
    clock: MutableClock
    target: str
    ca: bytes | None


def capability(node_id: NodeId) -> NodeCapability:
    return NodeCapability(
        schema_version="1.0",
        node_id=node_id,
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
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def spec(key: str = "remote", hands: int = 20) -> JobSpec:
    profile = BUILTIN_WORKLOADS[0]
    return JobSpec(
        schema_version="1.0",
        job_id=new_job_id(),
        experiment_id=new_experiment_id(),
        type=JobType.SIMULATION,
        priority=1,
        resources=profile.minimum,
        payload=SimulationJobPayload("1.0", hands=hands, seed_start=7).to_bytes(),
        payload_type=profile.payload_type,
        idempotency_key=key,
    )


@pytest.fixture(params=["insecure", "tls"])
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Harness]:
    database = tmp_path / "tableforge.db"
    service = CoordinatorService(
        jobs=SQLiteJobRepository(database),
        artifacts=LocalArtifactStore(tmp_path / "artifacts"),
        workloads=BUILTIN_WORKLOADS,
        nodes=SQLiteNodeRepository(database),
    )
    auth = SQLiteAuthRepository(database)
    clock = MutableClock(datetime(2026, 1, 1, tzinfo=UTC))
    tls = generate_self_signed(tmp_path / "tls", ()) if request.param == "tls" else None
    server = start_server(service, auth, clock, "localhost:0", tls)
    try:
        yield Harness(
            service,
            auth,
            server,
            clock,
            f"localhost:{server.port}",
            tls.certificate_path.read_bytes() if tls else None,
        )
    finally:
        server.stop(0)


def connect(harness: Harness, node_id: NodeId) -> GrpcCoordinatorClient:
    token = harness.auth.create_enrollment_token(harness.clock.now(), timedelta(hours=1))
    channel = create_channel(harness.target, harness.ca)
    session, _ = enroll(channel, node_id, token)
    return GrpcCoordinatorClient(channel, session)


def test_remote_worker_completes_job(harness: Harness) -> None:
    job = spec()
    harness.service.create_job(job)
    node_id = NodeId("node-remote")
    client = connect(harness, node_id)

    WorkerRuntime(
        node_id=node_id,
        capability=capability(node_id),
        coordinator=client,
        clock=harness.clock,
        lease_duration=timedelta(minutes=5),
        simulation_executor=SimulationExecutor(),
    ).run_once()

    completed = harness.service.get_job(job.job_id)
    expected = hashlib.sha256(SimulationExecutor().execute(job.payload)).hexdigest()
    assert completed.status is JobStatus.SUCCEEDED
    assert harness.service.job_outputs(job.job_id)[0].artifact.sha256 == expected


def test_unauthenticated_call_is_rejected(harness: Harness) -> None:
    client = GrpcCoordinatorClient(create_channel(harness.target, harness.ca), "forged")

    with pytest.raises(RemoteCoordinatorError) as error:
        client.heartbeat_node(NodeId("node-x"), 1, harness.clock.now())

    assert error.value.code == "UNAUTHENTICATED"


def test_session_cannot_act_as_other_node(harness: Harness) -> None:
    client = connect(harness, NodeId("node-a"))

    with pytest.raises(RemoteCoordinatorError) as error:
        client.register_node(capability(NodeId("node-b")), harness.clock.now())

    assert error.value.code == "NODE_MISMATCH"


def test_enrollment_token_cannot_be_reused_by_other_node(harness: Harness) -> None:
    token = harness.auth.create_enrollment_token(harness.clock.now(), timedelta(hours=1))
    channel = create_channel(harness.target, harness.ca)
    enroll(channel, NodeId("node-a"), token)

    with pytest.raises(RemoteCoordinatorError) as error:
        enroll(channel, NodeId("node-b"), token)

    assert error.value.code == "UNAUTHENTICATED"


def test_expired_lease_maps_to_fenced_error(harness: Harness) -> None:
    job = spec()
    harness.service.create_job(job)
    node_id = NodeId("node-a")
    client = connect(harness, node_id)
    client.register_node(capability(node_id), harness.clock.now())
    lease = client.claim_job(
        node_id, capability(node_id), harness.clock.now(), timedelta(seconds=30)
    )
    assert lease is not None
    harness.clock.value += timedelta(minutes=1)

    with pytest.raises(LeaseFencedError):
        client.renew_job(job.job_id, lease.token, harness.clock.now(), timedelta(seconds=30))


def test_artifact_round_trip(harness: Harness, tmp_path: Path) -> None:
    client = connect(harness, NodeId("node-a"))
    content = b"x" * (3 * 1024 * 1024 + 17)
    descriptor = ArtifactDescriptor(
        new_artifact_id(), ArtifactKind.DIAGNOSTIC, "application/octet-stream", "1"
    )

    uploaded = client.upload_artifact(descriptor, content)
    downloaded, path = client.download_artifact(
        str(uploaded.descriptor.artifact_id), tmp_path / "cache"
    )

    assert downloaded.sha256 == hashlib.sha256(content).hexdigest()
    assert path.read_bytes() == content


def test_invalid_lease_duration_is_rejected(harness: Harness) -> None:
    node_id = NodeId("node-a")
    client = connect(harness, node_id)
    client.register_node(capability(node_id), harness.clock.now())

    with pytest.raises(RemoteCoordinatorError) as error:
        client.claim_job(node_id, capability(node_id), harness.clock.now(), timedelta(days=2))

    assert error.value.code == "INVALID_LEASE_DURATION"


@pytest.fixture
def s3_harness(tmp_path: Path, s3_store: object) -> Iterator[Harness]:
    database = tmp_path / "tableforge.db"
    service = CoordinatorService(
        jobs=SQLiteJobRepository(database),
        artifacts=s3_store,  # type: ignore[arg-type]
        workloads=BUILTIN_WORKLOADS,
        nodes=SQLiteNodeRepository(database),
    )
    auth = SQLiteAuthRepository(database)
    clock = MutableClock(datetime.now(UTC))
    server = start_server(service, auth, clock, "localhost:0", None)
    try:
        yield Harness(service, auth, server, clock, f"localhost:{server.port}", None)
    finally:
        server.stop(0)


@pytest.mark.s3
def test_direct_transfer_bypasses_coordinator(s3_harness: Harness, tmp_path: Path) -> None:
    from tableforge.observability import COUNTERS

    client = connect(s3_harness, NodeId("node-a"))
    content = b"y" * (3 * 1024 * 1024 + 5)
    descriptor = ArtifactDescriptor(
        new_artifact_id(), ArtifactKind.DIAGNOSTIC, "application/octet-stream", "1"
    )
    before = COUNTERS.snapshot()

    uploaded = client.upload_artifact(descriptor, content)
    downloaded, path = client.download_artifact(
        str(uploaded.descriptor.artifact_id), tmp_path / "cache"
    )
    again = client.upload_artifact(
        ArtifactDescriptor(new_artifact_id(), ArtifactKind.DIAGNOSTIC, "text/plain", "1"),
        content,
    )

    after = COUNTERS.snapshot()

    def delta(direction: str) -> float:
        key = ("tableforge_artifact_bytes_total", (("direction", direction),))
        return after.get(key, 0.0) - before.get(key, 0.0)

    assert path.read_bytes() == content
    assert downloaded.sha256 == uploaded.sha256 == again.sha256
    assert delta("upload") == 0 and delta("download") == 0
    assert delta("direct_upload") == 2 * len(content)
    assert delta("direct_download") == len(content)


@pytest.mark.s3
def test_worker_completes_job_through_direct_storage(s3_harness: Harness) -> None:
    job = spec("direct-job")
    s3_harness.service.create_job(job)
    node_id = NodeId("node-direct")
    client = connect(s3_harness, node_id)

    WorkerRuntime(
        node_id=node_id,
        capability=capability(node_id),
        coordinator=client,
        clock=s3_harness.clock,
        lease_duration=timedelta(minutes=5),
        simulation_executor=SimulationExecutor(),
    ).run_once()

    output = s3_harness.service.job_outputs(job.job_id)[0].artifact
    assert s3_harness.service.get_job(job.job_id).status is JobStatus.SUCCEEDED
    assert s3_harness.service.artifacts.exists(output.sha256)


@pytest.mark.s3
def test_commit_without_upload_is_rejected(s3_harness: Harness) -> None:
    client = connect(s3_harness, NodeId("node-a"))
    ref = common_pb2.ArtifactRef(
        artifact_id="ghost",
        sha256=hashlib.sha256(b"never uploaded").hexdigest(),
        size_bytes=14,
        media_type="text/plain",
        kind=common_pb2.ARTIFACT_KIND_DIAGNOSTIC,
        schema_version="1",
    )

    import grpc

    with pytest.raises(grpc.RpcError) as error:
        client._artifacts.CommitUpload(  # noqa: SLF001
            coordinator_pb2.CommitUploadRequest(descriptor=ref), metadata=client._metadata
        )

    assert error.value.code() is grpc.StatusCode.FAILED_PRECONDITION  # type: ignore[attr-defined]
    codes = dict(error.value.trailing_metadata() or ())  # type: ignore[attr-defined]
    assert codes.get(ERROR_METADATA_KEY) == "ARTIFACT_MISSING"


def test_local_store_falls_back_to_streaming(harness: Harness, tmp_path: Path) -> None:
    client = connect(harness, NodeId("node-a"))
    content = b"streamed"
    uploaded = client.upload_artifact(
        ArtifactDescriptor(new_artifact_id(), ArtifactKind.DIAGNOSTIC, "text/plain", "1"), content
    )

    _, path = client.download_artifact(str(uploaded.descriptor.artifact_id), tmp_path / "c")

    assert path.read_bytes() == content
    assert client._direct is False  # noqa: SLF001
