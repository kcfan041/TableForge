from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tableforge.capability.workloads import BUILTIN_WORKLOADS
from tableforge.domain.artifacts import Artifact, ArtifactDescriptor, ArtifactKind
from tableforge.domain.capabilities import (
    GpuCapability,
    NodeCapability,
    RuntimeCapability,
    StorageCapability,
)
from tableforge.domain.ids import (
    AttemptId,
    NodeId,
    new_artifact_id,
    new_experiment_id,
    new_job_id,
)
from tableforge.domain.jobs import Job, JobLease, JobSpec, JobStatus, JobType
from tableforge.transport import convert

NOW = datetime(2026, 1, 1, 12, 30, 15, 123456, tzinfo=UTC)


def test_capability_round_trip_preserves_unknown_values() -> None:
    capability = NodeCapability(
        schema_version="1.0",
        node_id=NodeId("node-a"),
        revision=3,
        operating_system="darwin",
        architecture="arm64",
        logical_cpu_cores=10,
        physical_cpu_cores=None,
        total_memory_bytes=16 * 1024**3,
        available_memory_bytes=None,
        gpus=(
            GpuCapability(
                device_id="cuda:0",
                vendor="nvidia",
                model="test",
                total_memory_bytes=None,
                available_memory_bytes=None,
                runtimes=("cuda",),
            ),
        ),
        storage=(StorageCapability("data", None),),
        runtime=RuntimeCapability("3.12", ("cpu",)),
        enabled_roles=("simulation",),
        captured_at=NOW,
        warnings=("AVAILABLE_MEMORY_UNKNOWN",),
    )

    assert convert.capability_from_proto(convert.capability_to_proto(capability)) == capability


def test_lease_round_trip() -> None:
    profile = BUILTIN_WORKLOADS[0]
    artifact = Artifact.available(
        ArtifactDescriptor(new_artifact_id(), ArtifactKind.DIAGNOSTIC, "text/plain", "1"),
        "a" * 64,
        12,
        "sha256/aa/aa/" + "a" * 64,
    )
    spec = JobSpec(
        schema_version="1.0",
        job_id=new_job_id(),
        experiment_id=new_experiment_id(),
        type=JobType.SIMULATION,
        priority=5,
        resources=profile.minimum,
        payload=b"\x00\x01",
        payload_type=profile.payload_type,
        idempotency_key="key",
        inputs=(artifact,),
        not_before=NOW,
        created_at=NOW,
    )
    lease = JobLease(
        job=Job(spec, JobStatus.CANCEL_REQUESTED, 4),
        attempt_id=AttemptId("attempt"),
        node_id=NodeId("node-a"),
        token="token",
        attempt_number=2,
        leased_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )

    assert convert.lease_from_proto(convert.lease_to_proto(lease)) == lease
