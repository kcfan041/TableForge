from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from tableforge.domain.artifacts import Artifact, ArtifactDescriptor
from tableforge.domain.auth import IssuedSession
from tableforge.domain.capabilities import NodeCapability
from tableforge.domain.checkpoints import Checkpoint
from tableforge.domain.experiments import Experiment, ExperimentConfig, ExperimentStatus
from tableforge.domain.ids import AgentId, CheckpointId, ExperimentId, JobId, NodeId
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


class ArtifactNotFoundError(Exception):
    pass


class ArtifactIntegrityError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SignedTransfer:
    """A short-lived HTTP request a worker may perform directly against object storage.

    Uploads are bound to one storage key, one SHA-256, and one length through the signature,
    so a worker cannot use the grant to write other content or other objects.
    """

    method: str
    url: str
    headers: tuple[tuple[str, str], ...]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class StoredObject:
    storage_key: str
    size_bytes: int
    modified_at: datetime


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


class ExperimentRepository(Protocol):
    def create(
        self, experiment_id: ExperimentId, config: ExperimentConfig, now: datetime
    ) -> Experiment: ...

    def get(self, experiment_id: ExperimentId) -> Experiment: ...

    def update_status(
        self,
        experiment_id: ExperimentId,
        expected_revision: int,
        status: ExperimentStatus,
        now: datetime,
    ) -> Experiment: ...

    def list_experiments(self) -> tuple[Experiment, ...]: ...


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


class CheckpointRepository(Protocol):
    def register_candidate(self, checkpoint: Checkpoint) -> Checkpoint: ...

    def decide(
        self, checkpoint_id: CheckpointId, promote: bool, reason: str, now: datetime
    ) -> Checkpoint: ...

    def get(self, checkpoint_id: CheckpointId) -> Checkpoint | None: ...

    def active(self, agent_id: AgentId) -> Checkpoint | None: ...

    def list_checkpoints(self, agent_id: AgentId | None = None) -> tuple[Checkpoint, ...]: ...


class AuthRepository(Protocol):
    def create_enrollment_token(self, now: datetime, ttl: timedelta) -> str: ...

    def enroll(
        self,
        enrollment_token: str,
        node_id: NodeId,
        now: datetime,
        session_ttl: timedelta,
    ) -> IssuedSession: ...

    def authenticate(self, session_token: str, now: datetime) -> NodeId: ...

    def revoke_node(self, node_id: NodeId) -> int: ...

    def rotate_session(
        self, session_token: str, now: datetime, session_ttl: timedelta
    ) -> IssuedSession: ...


class ArtifactStore(Protocol):
    def put(self, descriptor: ArtifactDescriptor, content: bytes) -> Artifact: ...

    def get(self, artifact_id: str) -> Artifact: ...

    def read(self, artifact_id: str) -> tuple[Artifact, bytes]: ...

    def exists(self, sha256: str) -> bool: ...

    def list_objects(self) -> Iterator[StoredObject]: ...

    def delete_object(self, storage_key: str) -> None: ...


class DirectArtifactStore(ArtifactStore, Protocol):
    """An artifact store that workers can reach without going through the coordinator."""

    def sign_upload(self, sha256: str, size_bytes: int, ttl: timedelta) -> SignedTransfer: ...

    def sign_download(self, artifact: Artifact, ttl: timedelta) -> SignedTransfer: ...

    def register(self, descriptor: ArtifactDescriptor, sha256: str, size_bytes: int) -> Artifact:
        """Record metadata for an object a worker uploaded with ``sign_upload``.

        Raises ``ArtifactNotFoundError`` if the object is missing and
        ``ArtifactIntegrityError`` if its stored checksum or size differs.
        """
        ...


def storage_key_for(sha256: str) -> str:
    if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
        raise ValueError("sha256 must be 64 lowercase hex characters")
    return f"sha256/{sha256[:2]}/{sha256[2:4]}/{sha256}"
