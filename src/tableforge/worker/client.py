from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from tableforge.domain.artifacts import Artifact, ArtifactDescriptor
from tableforge.domain.capabilities import NodeCapability
from tableforge.domain.ids import JobId, NodeId
from tableforge.domain.jobs import Job, JobLease


class CoordinatorClient(Protocol):
    def register_node(self, capability: NodeCapability, now: datetime) -> None: ...

    def heartbeat_node(self, node_id: NodeId, revision: int, now: datetime) -> None: ...

    def claim_job(
        self,
        node_id: NodeId,
        capability: NodeCapability,
        now: datetime,
        duration: timedelta,
    ) -> JobLease | None: ...

    def get_job(self, job_id: JobId) -> Job: ...

    def renew_job(
        self,
        job_id: JobId,
        token: str,
        now: datetime,
        duration: timedelta,
    ) -> JobLease: ...

    def complete_job(
        self,
        job_id: JobId,
        token: str,
        now: datetime,
        result_hash: str,
        outputs: tuple[Artifact, ...] = (),
    ) -> Job: ...

    def fail_job(
        self,
        job_id: JobId,
        token: str,
        now: datetime,
        error_code: str,
        error_summary: str,
        retryable: bool,
        diagnostic_artifact_id: str | None = None,
    ) -> Job: ...

    def acknowledge_cancel(self, job_id: JobId, token: str, now: datetime) -> Job: ...

    def upload_artifact(self, descriptor: ArtifactDescriptor, content: bytes) -> Artifact: ...

    def read_artifact(self, artifact_id: str) -> bytes: ...
