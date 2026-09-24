from __future__ import annotations

from datetime import UTC, datetime, timedelta

from google.protobuf.duration_pb2 import Duration
from google.protobuf.message import Message
from google.protobuf.timestamp_pb2 import Timestamp

from tableforge._generated.tableforge.v1 import (
    capabilities_pb2,
    common_pb2,
    jobs_pb2,
)
from tableforge.domain.artifacts import (
    Artifact,
    ArtifactDescriptor,
    ArtifactKind,
    ArtifactStatus,
)
from tableforge.domain.capabilities import (
    GpuCapability,
    NodeCapability,
    RuntimeCapability,
    StorageCapability,
)
from tableforge.domain.ids import ArtifactId, AttemptId, ExperimentId, JobId, NodeId
from tableforge.domain.jobs import (
    Job,
    JobLease,
    JobSpec,
    JobStatus,
    JobType,
    ResourceRequest,
)

_ARTIFACT_KINDS = {
    ArtifactKind.UNSPECIFIED: common_pb2.ARTIFACT_KIND_UNSPECIFIED,
    ArtifactKind.MODEL_CHECKPOINT: common_pb2.ARTIFACT_KIND_MODEL_CHECKPOINT,
    ArtifactKind.TRAINING_STATE: common_pb2.ARTIFACT_KIND_TRAINING_STATE,
    ArtifactKind.EXPERIENCE_SHARD: common_pb2.ARTIFACT_KIND_EXPERIENCE_SHARD,
    ArtifactKind.HAND_HISTORY: common_pb2.ARTIFACT_KIND_HAND_HISTORY,
    ArtifactKind.EVALUATION_RESULT: common_pb2.ARTIFACT_KIND_EVALUATION_RESULT,
    ArtifactKind.DIAGNOSTIC: common_pb2.ARTIFACT_KIND_DIAGNOSTIC,
    ArtifactKind.DATASET_SNAPSHOT: common_pb2.ARTIFACT_KIND_DATASET_SNAPSHOT,
    ArtifactKind.LEAGUE_POPULATION: common_pb2.ARTIFACT_KIND_LEAGUE_POPULATION,
}
_ARTIFACT_KINDS_REVERSE = {value: key for key, value in _ARTIFACT_KINDS.items()}

_JOB_TYPES = {
    JobType.UNSPECIFIED: jobs_pb2.JOB_TYPE_UNSPECIFIED,
    JobType.SIMULATION: jobs_pb2.JOB_TYPE_SIMULATION,
    JobType.EVALUATION: jobs_pb2.JOB_TYPE_EVALUATION,
    JobType.TRAINING: jobs_pb2.JOB_TYPE_TRAINING,
}
_JOB_TYPES_REVERSE = {value: key for key, value in _JOB_TYPES.items()}

_JOB_STATUSES = {
    JobStatus.QUEUED: jobs_pb2.JOB_STATUS_QUEUED,
    JobStatus.LEASED: jobs_pb2.JOB_STATUS_LEASED,
    JobStatus.RUNNING: jobs_pb2.JOB_STATUS_RUNNING,
    JobStatus.RETRY_WAIT: jobs_pb2.JOB_STATUS_RETRY_WAIT,
    JobStatus.SUCCEEDED: jobs_pb2.JOB_STATUS_SUCCEEDED,
    JobStatus.FAILED: jobs_pb2.JOB_STATUS_FAILED,
    JobStatus.CANCELLED: jobs_pb2.JOB_STATUS_CANCELLED,
    JobStatus.CANCEL_REQUESTED: jobs_pb2.JOB_STATUS_CANCEL_REQUESTED,
}
_JOB_STATUSES_REVERSE = {value: key for key, value in _JOB_STATUSES.items()}


def timestamp_to_proto(value: datetime) -> Timestamp:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    message = Timestamp()
    message.FromDatetime(value.astimezone(UTC))
    return message


def timestamp_from_proto(message: Timestamp) -> datetime:
    return message.ToDatetime(tzinfo=UTC)


def duration_to_proto(value: timedelta) -> Duration:
    message = Duration()
    message.FromTimedelta(value)
    return message


def duration_from_proto(message: Duration) -> timedelta:
    return message.ToTimedelta()


def resources_to_proto(value: ResourceRequest) -> common_pb2.ResourceRequest:
    return common_pb2.ResourceRequest(
        cpu_cores=value.cpu_cores,
        memory_bytes=value.memory_bytes,
        gpu_count=value.gpu_count,
        gpu_memory_bytes=value.gpu_memory_bytes,
        required_role=value.required_role,
        storage_bytes=value.storage_bytes,
        required_runtime=value.required_runtime,
        exclusive_gpu=value.exclusive_gpu,
    )


def resources_from_proto(message: common_pb2.ResourceRequest) -> ResourceRequest:
    return ResourceRequest(
        cpu_cores=message.cpu_cores,
        memory_bytes=message.memory_bytes,
        gpu_count=message.gpu_count,
        gpu_memory_bytes=message.gpu_memory_bytes,
        storage_bytes=message.storage_bytes,
        required_role=message.required_role,
        required_runtime=message.required_runtime or "cpu",
        exclusive_gpu=message.exclusive_gpu,
    )


def artifact_ref_to_proto(value: ArtifactDescriptor) -> common_pb2.ArtifactRef:
    return common_pb2.ArtifactRef(
        artifact_id=str(value.artifact_id),
        media_type=value.media_type,
        kind=_ARTIFACT_KINDS[value.kind],
        schema_version=value.schema_version,
    )


def descriptor_from_proto(message: common_pb2.ArtifactRef) -> ArtifactDescriptor:
    return ArtifactDescriptor(
        artifact_id=ArtifactId(message.artifact_id),
        kind=_ARTIFACT_KINDS_REVERSE[message.kind],
        media_type=message.media_type,
        schema_version=message.schema_version,
    )


def artifact_to_proto(value: Artifact) -> common_pb2.Artifact:
    ref = artifact_ref_to_proto(value.descriptor)
    ref.sha256 = value.sha256
    ref.size_bytes = value.size_bytes
    return common_pb2.Artifact(
        ref=ref,
        storage_key=value.storage_key,
        created_at=timestamp_to_proto(value.created_at),
    )


def artifact_from_proto(message: common_pb2.Artifact) -> Artifact:
    return Artifact(
        descriptor=descriptor_from_proto(message.ref),
        sha256=message.ref.sha256,
        size_bytes=message.ref.size_bytes,
        status=ArtifactStatus.AVAILABLE,
        storage_key=message.storage_key,
        created_at=timestamp_from_proto(message.created_at),
    )


def job_spec_to_proto(value: JobSpec) -> jobs_pb2.JobSpec:
    message = jobs_pb2.JobSpec(
        schema_version=value.schema_version,
        job_id=str(value.job_id),
        experiment_id=str(value.experiment_id),
        type=_JOB_TYPES[value.type],
        priority=value.priority,
        resources=resources_to_proto(value.resources),
        payload=value.payload,
        payload_type=value.payload_type,
        idempotency_key=value.idempotency_key,
        max_attempts=value.max_attempts,
        created_at=timestamp_to_proto(value.created_at),
        input_artifacts=[artifact_to_proto(artifact) for artifact in value.inputs],
    )
    if value.not_before is not None:
        message.not_before.CopyFrom(timestamp_to_proto(value.not_before))
    return message


def job_spec_from_proto(message: jobs_pb2.JobSpec) -> JobSpec:
    return JobSpec(
        schema_version=message.schema_version,
        job_id=JobId(message.job_id),
        experiment_id=ExperimentId(message.experiment_id),
        type=_JOB_TYPES_REVERSE[message.type],
        priority=message.priority,
        resources=resources_from_proto(message.resources),
        payload=message.payload,
        payload_type=message.payload_type,
        idempotency_key=message.idempotency_key,
        max_attempts=message.max_attempts,
        inputs=tuple(artifact_from_proto(artifact) for artifact in message.input_artifacts),
        not_before=(
            timestamp_from_proto(message.not_before) if message.HasField("not_before") else None
        ),
        created_at=timestamp_from_proto(message.created_at),
    )


def job_to_proto(value: Job) -> jobs_pb2.Job:
    return jobs_pb2.Job(
        spec=job_spec_to_proto(value.spec),
        status=_JOB_STATUSES[value.status],
        version=value.version,
    )


def job_from_proto(message: jobs_pb2.Job) -> Job:
    return Job(
        spec=job_spec_from_proto(message.spec),
        status=_JOB_STATUSES_REVERSE[message.status],
        version=message.version,
    )


def lease_to_proto(value: JobLease) -> jobs_pb2.JobLease:
    return jobs_pb2.JobLease(
        job=job_to_proto(value.job),
        attempt_id=str(value.attempt_id),
        node_id=str(value.node_id),
        token=value.token,
        attempt_number=value.attempt_number,
        leased_at=timestamp_to_proto(value.leased_at),
        expires_at=timestamp_to_proto(value.expires_at),
    )


def lease_from_proto(message: jobs_pb2.JobLease) -> JobLease:
    return JobLease(
        job=job_from_proto(message.job),
        attempt_id=AttemptId(message.attempt_id),
        node_id=NodeId(message.node_id),
        token=message.token,
        attempt_number=message.attempt_number,
        leased_at=timestamp_from_proto(message.leased_at),
        expires_at=timestamp_from_proto(message.expires_at),
    )


def capability_to_proto(value: NodeCapability) -> capabilities_pb2.NodeCapability:
    message = capabilities_pb2.NodeCapability(
        schema_version=value.schema_version,
        node_id=str(value.node_id),
        revision=value.revision,
        operating_system=value.operating_system,
        architecture=value.architecture,
        gpus=[_gpu_to_proto(gpu) for gpu in value.gpus],
        storage=[_storage_to_proto(storage) for storage in value.storage],
        runtime=capabilities_pb2.RuntimeCapability(
            python_version=value.runtime.python_version,
            runtimes=list(value.runtime.runtimes),
            container_runtime=value.runtime.container_runtime or "",
        ),
        enabled_roles=list(value.enabled_roles),
        captured_at=timestamp_to_proto(value.captured_at),
        warnings=list(value.warnings),
    )
    if value.logical_cpu_cores is not None:
        message.logical_cpu_cores = value.logical_cpu_cores
    if value.physical_cpu_cores is not None:
        message.physical_cpu_cores = value.physical_cpu_cores
    if value.total_memory_bytes is not None:
        message.total_memory_bytes = value.total_memory_bytes
    if value.available_memory_bytes is not None:
        message.available_memory_bytes = value.available_memory_bytes
    return message


def capability_from_proto(message: capabilities_pb2.NodeCapability) -> NodeCapability:
    return NodeCapability(
        schema_version=message.schema_version,
        node_id=NodeId(message.node_id),
        revision=message.revision,
        operating_system=message.operating_system,
        architecture=message.architecture,
        logical_cpu_cores=_optional(message, "logical_cpu_cores"),
        physical_cpu_cores=_optional(message, "physical_cpu_cores"),
        total_memory_bytes=_optional(message, "total_memory_bytes"),
        available_memory_bytes=_optional(message, "available_memory_bytes"),
        gpus=tuple(_gpu_from_proto(gpu) for gpu in message.gpus),
        storage=tuple(_storage_from_proto(storage) for storage in message.storage),
        runtime=RuntimeCapability(
            python_version=message.runtime.python_version,
            runtimes=tuple(message.runtime.runtimes),
            container_runtime=message.runtime.container_runtime or None,
        ),
        enabled_roles=tuple(message.enabled_roles),
        captured_at=timestamp_from_proto(message.captured_at),
        warnings=tuple(message.warnings),
    )


def _gpu_to_proto(value: GpuCapability) -> capabilities_pb2.GpuCapability:
    message = capabilities_pb2.GpuCapability(
        device_id=value.device_id,
        vendor=value.vendor,
        model=value.model,
        driver_version=value.driver_version or "",
        compute_capability=value.compute_capability or "",
        runtimes=list(value.runtimes),
    )
    if value.total_memory_bytes is not None:
        message.total_memory_bytes = value.total_memory_bytes
    if value.available_memory_bytes is not None:
        message.available_memory_bytes = value.available_memory_bytes
    return message


def _gpu_from_proto(message: capabilities_pb2.GpuCapability) -> GpuCapability:
    return GpuCapability(
        device_id=message.device_id,
        vendor=message.vendor,
        model=message.model,
        total_memory_bytes=_optional(message, "total_memory_bytes"),
        available_memory_bytes=_optional(message, "available_memory_bytes"),
        driver_version=message.driver_version or None,
        compute_capability=message.compute_capability or None,
        runtimes=tuple(message.runtimes),
    )


def _storage_to_proto(value: StorageCapability) -> capabilities_pb2.StorageCapability:
    message = capabilities_pb2.StorageCapability(
        volume_id=value.volume_id,
        storage_class=value.storage_class,
    )
    if value.available_bytes is not None:
        message.available_bytes = value.available_bytes
    return message


def _storage_from_proto(message: capabilities_pb2.StorageCapability) -> StorageCapability:
    return StorageCapability(
        volume_id=message.volume_id,
        available_bytes=_optional(message, "available_bytes"),
        storage_class=message.storage_class,
    )


def _optional(message: Message, field: str) -> int | None:
    if not message.HasField(field):
        return None
    return int(getattr(message, field))
