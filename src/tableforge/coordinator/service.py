from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta

from tableforge.application.ports import (
    ArtifactStore,
    DirectArtifactStore,
    JobRepository,
    NodeRepository,
    SignedTransfer,
)
from tableforge.capability.evaluator import assess_compatibility
from tableforge.domain.artifacts import Artifact, ArtifactDescriptor
from tableforge.domain.capabilities import (
    CompatibilityAssessment,
    CompatibilityStatus,
    GpuCapability,
    NodeCapability,
    WorkloadProfile,
)
from tableforge.domain.ids import JobId, NodeId
from tableforge.domain.jobs import (
    Job,
    JobAttemptError,
    JobLease,
    JobOutput,
    JobSpec,
    ResourceRequest,
)
from tableforge.domain.nodes import RegisteredNode


class DirectTransferUnavailableError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class CoordinatorService:
    jobs: JobRepository
    artifacts: ArtifactStore
    workloads: tuple[WorkloadProfile, ...]
    nodes: NodeRepository | None = None
    node_ttl: timedelta = timedelta(minutes=2)
    direct_transfer: bool = True
    presign_ttl: timedelta = timedelta(minutes=15)

    def create_job(self, spec: JobSpec) -> Job:
        self._workload_for(spec)
        return self.jobs.create(spec)

    def get_job(self, job_id: JobId) -> Job:
        return self.jobs.get(job_id)

    def register_node(self, capability: NodeCapability, now: datetime) -> None:
        if self.nodes is None:
            return
        self.nodes.register(capability, now)

    def heartbeat_node(self, node_id: NodeId, revision: int, now: datetime) -> None:
        if self.nodes is None:
            return
        self.nodes.heartbeat(node_id, revision, now)

    def list_nodes(self) -> tuple[RegisteredNode, ...]:
        return () if self.nodes is None else self.nodes.list_nodes()

    def claim_job(
        self,
        node_id: NodeId,
        capability: NodeCapability,
        now: datetime,
        duration: timedelta,
    ) -> JobLease | None:
        effective = capability
        if self.nodes is not None:
            registered = self.nodes.get(node_id)
            if registered is None or registered.capability.revision != capability.revision:
                return None
            if now - registered.last_heartbeat_at > self.node_ttl:
                return None
            reserved = self.jobs.reserved_resources(node_id)
            effective = _subtract_reserved(capability, reserved)

        def assessment(job: Job) -> CompatibilityAssessment:
            return assess_compatibility(effective, self._workload_for(job.spec))

        def compatible(job: Job) -> bool:
            result = assessment(job)
            return result.status in (
                CompatibilityStatus.SUPPORTED,
                CompatibilityStatus.DEGRADED,
            )

        def workload_identity(job: Job) -> tuple[str, str]:
            workload = self._workload_for(job.spec)
            return workload.key, workload.version

        def execution_plan(job: Job) -> str:
            result = assessment(job)
            if result.plan is None:
                raise AssertionError("compatible job must have an execution plan")
            return json.dumps(asdict(result.plan), sort_keys=True)

        return self.jobs.claim_compatible(
            node_id,
            now,
            duration,
            compatible,
            capability_revision=capability.revision,
            workload_identity=workload_identity,
            execution_plan=execution_plan,
        )

    def renew_job(
        self,
        job_id: JobId,
        token: str,
        now: datetime,
        duration: timedelta,
    ) -> JobLease:
        return self.jobs.renew(job_id, token, now, duration)

    def complete_job(
        self,
        job_id: JobId,
        token: str,
        now: datetime,
        result_hash: str,
        outputs: tuple[Artifact, ...] = (),
    ) -> Job:
        return self.jobs.complete(job_id, token, now, result_hash, outputs)

    def list_jobs(self) -> tuple[Job, ...]:
        return self.jobs.list_jobs()

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
        return self.jobs.fail(
            job_id,
            token,
            now,
            error_code,
            error_summary,
            retryable,
            diagnostic_artifact_id,
        )

    def cancel_job(self, job_id: JobId, now: datetime) -> Job:
        return self.jobs.cancel(job_id, now)

    def acknowledge_cancel(self, job_id: JobId, token: str, now: datetime) -> Job:
        return self.jobs.acknowledge_cancel(job_id, token, now)

    def job_attempt_errors(self, job_id: JobId) -> tuple[JobAttemptError, ...]:
        return self.jobs.attempt_errors(job_id)

    def job_outputs(self, job_id: JobId) -> tuple[JobOutput, ...]:
        return self.jobs.outputs(job_id)

    def upload_artifact(self, descriptor: ArtifactDescriptor, content: bytes) -> Artifact:
        return self.artifacts.put(descriptor, content)

    def read_artifact(self, artifact_id: str) -> bytes:
        return self.artifacts.read(artifact_id)[1]

    def direct_artifacts(self) -> DirectArtifactStore | None:
        store = self.artifacts
        if self.direct_transfer and hasattr(store, "sign_upload"):
            return store  # type: ignore[return-value]
        return None

    def sign_artifact_upload(self, sha256: str, size_bytes: int) -> SignedTransfer:
        store = self._require_direct()
        return store.sign_upload(sha256, size_bytes, self.presign_ttl)

    def register_artifact(
        self, descriptor: ArtifactDescriptor, sha256: str, size_bytes: int
    ) -> Artifact:
        return self._require_direct().register(descriptor, sha256, size_bytes)

    def sign_artifact_download(self, artifact_id: str) -> tuple[Artifact, SignedTransfer]:
        store = self._require_direct()
        artifact = store.get(artifact_id)
        return artifact, store.sign_download(artifact, self.presign_ttl)

    def _require_direct(self) -> DirectArtifactStore:
        store = self.direct_artifacts()
        if store is None:
            raise DirectTransferUnavailableError("artifact store does not support direct transfer")
        return store

    def _workload_for(self, spec: JobSpec) -> WorkloadProfile:
        candidates = [
            workload for workload in self.workloads if workload.payload_type == spec.payload_type
        ]
        if not candidates:
            raise ValueError(f"no workload profile for {spec.payload_type}")
        for workload in candidates:
            if workload.minimum.required_runtime == spec.resources.required_runtime:
                return workload
        return candidates[0]


def _subtract_reserved(
    capability: NodeCapability, reserved: ResourceRequest
) -> NodeCapability:
    logical = capability.logical_cpu_cores
    memory = capability.total_memory_bytes
    gpus: tuple[GpuCapability, ...]
    if reserved.exclusive_gpu:
        gpus = ()
    else:
        gpus = tuple(
            replace(
                gpu,
                total_memory_bytes=(
                    None
                    if gpu.total_memory_bytes is None
                    else max(0, gpu.total_memory_bytes - reserved.gpu_memory_bytes)
                ),
            )
            for index, gpu in enumerate(capability.gpus)
            if index >= reserved.gpu_count
        )
    return replace(
        capability,
        logical_cpu_cores=None if logical is None else max(0, logical - reserved.cpu_cores),
        total_memory_bytes=None if memory is None else max(0, memory - reserved.memory_bytes),
        gpus=gpus,
    )
