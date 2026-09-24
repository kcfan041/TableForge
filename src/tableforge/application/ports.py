from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Protocol

from tableforge.domain.artifacts import Artifact, ArtifactDescriptor
from tableforge.domain.capabilities import NodeCapability
from tableforge.domain.ids import ExperimentId, JobId, NodeId
from tableforge.domain.jobs import (
    Job,
    JobAttemptError,
    JobAttemptProvenance,
    JobLease,
    JobOutput,
    JobSpec,
    ResourceRequest,
)
from tableforge.domain.nodes import RegisteredNode


class IdempotencyConflictError(Exception):
    pass


class JobNotFoundError(Exception):
    pass


class LeaseConflictError(Exception):
    pass


class LeaseFencedError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class UploadGrant:
    artifact_id: str
    temporary_path: Path


@dataclass(frozen=True, slots=True)
class DownloadGrant:
    artifact: Artifact
    path: Path


class JobRepository(Protocol):
    def create(self, spec: JobSpec) -> Job: ...

    def get(self, job_id: JobId) -> Job: ...

    def get_by_idempotency_key(
        self, experiment_id: ExperimentId, idempotency_key: str
    ) -> Job | None: ...

    def claim_next(
        self, node_id: NodeId, now: datetime, duration: timedelta
    ) -> JobLease | None: ...

    def claim_compatible(
        self,
        node_id: NodeId,
        now: datetime,
        duration: timedelta,
        compatible: Callable[[Job], bool],
        capability_revision: int = 0,
        workload_identity: Callable[[Job], tuple[str, str]] | None = None,
        execution_plan: Callable[[Job], str] | None = None,
    ) -> JobLease | None: ...

    def renew(
        self, job_id: JobId, token: str, now: datetime, duration: timedelta
    ) -> JobLease: ...

    def complete(
        self,
        job_id: JobId,
        token: str,
        now: datetime,
        result_hash: str,
        outputs: tuple[Artifact, ...] = (),
    ) -> Job: ...

    def fail(
        self,
        job_id: JobId,
        token: str,
        now: datetime,
        error_code: str,
        error_summary: str,
        retryable: bool,
        diagnostic_artifact_id: str | None = None,
    ) -> Job: ...

    def cancel(self, job_id: JobId, now: datetime) -> Job: ...

    def acknowledge_cancel(self, job_id: JobId, token: str, now: datetime) -> Job: ...

    def attempt_errors(self, job_id: JobId) -> tuple[JobAttemptError, ...]: ...

    def attempt_provenance(self, job_id: JobId) -> tuple[JobAttemptProvenance, ...]: ...

    def reserved_resources(self, node_id: NodeId) -> ResourceRequest: ...

    def outputs(self, job_id: JobId) -> tuple[JobOutput, ...]: ...

    def list_jobs(self, experiment_id: ExperimentId | None = None) -> tuple[Job, ...]: ...

    def requeue_expired(self, now: datetime) -> int: ...


class NodeRepository(Protocol):
    def register(self, capability: NodeCapability, now: datetime) -> RegisteredNode: ...

    def heartbeat(
        self,
        node_id: NodeId,
        capability_revision: int,
        now: datetime,
        available_memory_bytes: int | None = None,
    ) -> RegisteredNode: ...

    def get(self, node_id: NodeId) -> RegisteredNode | None: ...

    def list_nodes(self) -> tuple[RegisteredNode, ...]: ...


class ArtifactStore(Protocol):
    def begin_upload(self, descriptor: ArtifactDescriptor) -> UploadGrant: ...

    def write_upload(self, grant: UploadGrant, source: BinaryIO) -> tuple[str, int]: ...

    def commit_upload(self, grant: UploadGrant, sha256: str, size_bytes: int) -> Artifact: ...

    def get_download(self, artifact_id: str) -> DownloadGrant: ...

    def exists(self, sha256: str) -> bool: ...
