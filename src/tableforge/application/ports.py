from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from tableforge.domain.artifacts import Artifact, ArtifactDescriptor
from tableforge.domain.ids import ExperimentId, JobId
from tableforge.domain.jobs import Job, JobSpec


class IdempotencyConflictError(Exception):
    pass


class JobNotFoundError(Exception):
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


class ArtifactStore(Protocol):
    def begin_upload(self, descriptor: ArtifactDescriptor) -> UploadGrant: ...

    def write_upload(self, grant: UploadGrant, source: BinaryIO) -> tuple[str, int]: ...

    def commit_upload(self, grant: UploadGrant, sha256: str, size_bytes: int) -> Artifact: ...

    def get_download(self, artifact_id: str) -> DownloadGrant: ...

    def exists(self, sha256: str) -> bool: ...
