from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from tableforge.application.ports import ArtifactStore, JobRepository
from tableforge.capability.evaluator import assess_compatibility
from tableforge.domain.artifacts import Artifact, ArtifactDescriptor
from tableforge.domain.capabilities import (
    CompatibilityStatus,
    NodeCapability,
    WorkloadProfile,
)
from tableforge.domain.ids import JobId, NodeId
from tableforge.domain.jobs import Job, JobAttemptError, JobLease, JobOutput, JobSpec


@dataclass(frozen=True, slots=True)
class CoordinatorService:
    jobs: JobRepository
    artifacts: ArtifactStore
    workloads: tuple[WorkloadProfile, ...]

    def create_job(self, spec: JobSpec) -> Job:
        self._workload_for(spec)
        return self.jobs.create(spec)

    def get_job(self, job_id: JobId) -> Job:
        return self.jobs.get(job_id)

    def claim_job(
        self,
        node_id: NodeId,
        capability: NodeCapability,
        now: datetime,
        duration: timedelta,
    ) -> JobLease | None:
        def compatible(job: Job) -> bool:
            workload = self._workload_for(job.spec)
            assessment = assess_compatibility(capability, workload)
            return assessment.status is CompatibilityStatus.SUPPORTED

        return self.jobs.claim_compatible(node_id, now, duration, compatible)

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
        import io

        grant = self.artifacts.begin_upload(descriptor)
        sha256, size = self.artifacts.write_upload(grant, io.BytesIO(content))
        return self.artifacts.commit_upload(grant, sha256, size)

    def _workload_for(self, spec: JobSpec) -> WorkloadProfile:
        for workload in self.workloads:
            if workload.payload_type == spec.payload_type:
                return workload
        raise ValueError(f"no workload profile for {spec.payload_type}")
