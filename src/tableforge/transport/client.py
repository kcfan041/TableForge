from __future__ import annotations

import hashlib
import os
import secrets
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypeVar

import grpc

from tableforge._generated.tableforge.v1 import common_pb2, coordinator_pb2, coordinator_pb2_grpc
from tableforge.domain.artifacts import Artifact, ArtifactDescriptor
from tableforge.domain.capabilities import NodeCapability
from tableforge.domain.ids import JobId, NodeId
from tableforge.domain.jobs import Job, JobLease
from tableforge.transport import convert, direct
from tableforge.transport.errors import (
    ERROR_METADATA_KEY,
    EXCEPTIONS_BY_CODE,
    RemoteCoordinatorError,
)
from tableforge.worker.cache import enforce_cache_limit

T = TypeVar("T")
CHUNK_SIZE = 1024 * 1024
RETRYABLE = {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED}


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 5
    initial_backoff: float = 0.2
    max_backoff: float = 5.0
    timeout: float = 30.0


def create_channel(target: str, ca_certificate: bytes | None) -> grpc.Channel:
    options = (
        ("grpc.max_receive_message_length", 8 * 1024 * 1024),
        ("grpc.max_send_message_length", 8 * 1024 * 1024),
    )
    if ca_certificate is None:
        return grpc.insecure_channel(target, options=options)
    credentials = grpc.ssl_channel_credentials(root_certificates=ca_certificate)
    return grpc.secure_channel(target, credentials, options=options)


def enroll(
    channel: grpc.Channel, node_id: NodeId, enrollment_token: str, timeout: float = 30.0
) -> tuple[str, datetime]:
    stub = coordinator_pb2_grpc.CoordinatorServiceStub(channel)
    try:
        response = stub.Enroll(
            coordinator_pb2.EnrollRequest(node_id=str(node_id), enrollment_token=enrollment_token),
            timeout=timeout,
        )
    except grpc.RpcError as error:
        raise _translate(error) from error
    return response.session_token, convert.timestamp_from_proto(response.expires_at)


def _translate(error: grpc.RpcError) -> Exception:
    code = ""
    trailing = error.trailing_metadata() if hasattr(error, "trailing_metadata") else None
    for item in trailing or ():
        if item.key == ERROR_METADATA_KEY:
            code = str(item.value)
    details = error.details() if hasattr(error, "details") else str(error)
    exception = EXCEPTIONS_BY_CODE.get(code)
    if exception is not None:
        return exception(details)
    status = error.code() if hasattr(error, "code") else None
    return RemoteCoordinatorError(code or (status.name if status else "UNKNOWN"), str(details))


class GrpcCoordinatorClient:
    def __init__(
        self,
        channel: grpc.Channel,
        session_token: str,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        cache_dir: Path | None = None,
        cache_max_bytes: int | None = None,
        storage_endpoint: str = "",
    ) -> None:
        self._cache_dir = cache_dir
        self._cache_max_bytes = cache_max_bytes
        self._direct: bool | None = None
        self._storage_endpoint = storage_endpoint
        self._worker_id = ""
        self._stub = coordinator_pb2_grpc.CoordinatorServiceStub(channel)
        self._artifacts = coordinator_pb2_grpc.ArtifactServiceStub(channel)
        self._metadata = (("authorization", f"Bearer {session_token}"),)
        self._retry = retry or RetryPolicy()
        self._sleep = sleep

    def _call(self, operation: Callable[[], T]) -> tuple[T, bool]:
        backoff = self._retry.initial_backoff
        retried = False
        for attempt in range(1, self._retry.attempts + 1):
            try:
                return operation(), retried
            except grpc.RpcError as error:
                status = error.code() if hasattr(error, "code") else None
                if status not in RETRYABLE or attempt == self._retry.attempts:
                    raise _translate(error) from error
                retried = True
                self._sleep(backoff)
                backoff = min(backoff * 2, self._retry.max_backoff)
        raise AssertionError("unreachable")

    def rotate_session(self) -> tuple[str, datetime]:
        response, _ = self._call(
            lambda: self._stub.RotateSession(
                coordinator_pb2.RotateSessionRequest(),
                metadata=self._metadata,
                timeout=self._retry.timeout,
            )
        )
        self._metadata = (("authorization", f"Bearer {response.session_token}"),)
        return response.session_token, convert.timestamp_from_proto(response.expires_at)

    def register_node(self, capability: NodeCapability, now: datetime) -> None:
        del now
        request = coordinator_pb2.RegisterNodeRequest(
            capability=convert.capability_to_proto(capability)
        )
        self._call(
            lambda: self._stub.RegisterNode(
                request, metadata=self._metadata, timeout=self._retry.timeout
            )
        )

    def heartbeat_node(self, node_id: NodeId, revision: int, now: datetime) -> None:
        del now
        request = coordinator_pb2.HeartbeatRequest(
            node_id=str(node_id), capability_revision=revision
        )
        self._call(
            lambda: self._stub.Heartbeat(
                request, metadata=self._metadata, timeout=self._retry.timeout
            )
        )

    def start_worker(self, node_id: NodeId, label: str = "") -> coordinator_pb2.StartWorkerResponse:
        """Register this process as one worker slot of the node.

        Also refreshes the storage endpoint choice: if the configured endpoint is no longer
        offered or reachable, the first reachable candidate is used instead.
        """
        response, _ = self._call(
            lambda: self._stub.StartWorker(
                coordinator_pb2.StartWorkerRequest(node_id=str(node_id), label=label),
                metadata=self._metadata,
                timeout=self._retry.timeout,
            )
        )
        self._worker_id = response.worker_id
        offered = {item.name for item in response.node_settings.storage_endpoints}
        if offered and (not self._storage_endpoint or self._storage_endpoint not in offered):
            from tableforge.transport.join import choose_storage_endpoint

            chosen, _ = choose_storage_endpoint(response.node_settings)
            self._storage_endpoint = chosen or ""
        return response

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def storage_endpoint(self) -> str:
        return self._storage_endpoint

    def claim_job(
        self,
        node_id: NodeId,
        capability: NodeCapability,
        now: datetime,
        duration: timedelta,
    ) -> JobLease | None:
        del now
        request = coordinator_pb2.ClaimJobRequest(
            node_id=str(node_id),
            capability=convert.capability_to_proto(capability),
            lease_duration=convert.duration_to_proto(duration),
            worker_id=self._worker_id,
        )
        response, _ = self._call(
            lambda: self._stub.ClaimJob(
                request, metadata=self._metadata, timeout=self._retry.timeout
            )
        )
        if not response.HasField("lease"):
            return None
        return convert.lease_from_proto(response.lease)

    def get_job(self, job_id: JobId) -> Job:
        request = coordinator_pb2.GetJobRequest(job_id=str(job_id))
        response, _ = self._call(
            lambda: self._stub.GetJob(request, metadata=self._metadata, timeout=self._retry.timeout)
        )
        return convert.job_from_proto(response.job)

    def renew_job(self, job_id: JobId, token: str, now: datetime, duration: timedelta) -> JobLease:
        del now
        request = coordinator_pb2.RenewLeaseRequest(
            job_id=str(job_id),
            lease_token=token,
            lease_duration=convert.duration_to_proto(duration),
        )
        response, _ = self._call(
            lambda: self._stub.RenewLease(
                request, metadata=self._metadata, timeout=self._retry.timeout
            )
        )
        return convert.lease_from_proto(response.lease)

    def complete_job(
        self,
        job_id: JobId,
        token: str,
        now: datetime,
        result_hash: str,
        outputs: tuple[Artifact, ...] = (),
    ) -> Job:
        del now
        request = coordinator_pb2.CompleteJobRequest(
            job_id=str(job_id),
            lease_token=token,
            result_hash=result_hash,
            outputs=[convert.artifact_to_proto(output) for output in outputs],
        )
        response, _ = self._call(
            lambda: self._stub.CompleteJob(
                request, metadata=self._metadata, timeout=self._retry.timeout
            )
        )
        return convert.job_from_proto(response.job)

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
        del now
        request = coordinator_pb2.FailJobRequest(
            job_id=str(job_id),
            lease_token=token,
            error_code=error_code,
            error_summary=error_summary,
            retryable=retryable,
            diagnostic_artifact_id=diagnostic_artifact_id or "",
        )
        return self._terminal_call(
            job_id,
            lambda: (
                self._stub.FailJob(
                    request, metadata=self._metadata, timeout=self._retry.timeout
                ).job
            ),
        )

    def acknowledge_cancel(self, job_id: JobId, token: str, now: datetime) -> Job:
        del now
        request = coordinator_pb2.AcknowledgeCancelRequest(job_id=str(job_id), lease_token=token)
        return self._terminal_call(
            job_id,
            lambda: (
                self._stub.AcknowledgeCancel(
                    request, metadata=self._metadata, timeout=self._retry.timeout
                ).job
            ),
        )

    def _terminal_call(self, job_id: JobId, operation: Callable[[], object]) -> Job:
        from tableforge.application.ports import LeaseConflictError, LeaseFencedError

        backoff = self._retry.initial_backoff
        retried = False
        for attempt in range(1, self._retry.attempts + 1):
            try:
                message = operation()
                return convert.job_from_proto(message)  # type: ignore[arg-type]
            except grpc.RpcError as error:
                translated = _translate(error)
                if retried and isinstance(translated, LeaseFencedError | LeaseConflictError):
                    return self.get_job(job_id)
                status = error.code() if hasattr(error, "code") else None
                if status not in RETRYABLE or attempt == self._retry.attempts:
                    raise translated from error
                retried = True
                self._sleep(backoff)
                backoff = min(backoff * 2, self._retry.max_backoff)
        raise AssertionError("unreachable")

    def upload_artifact(self, descriptor: ArtifactDescriptor, content: bytes) -> Artifact:
        ref = convert.artifact_ref_to_proto(descriptor)
        ref.sha256 = hashlib.sha256(content).hexdigest()
        ref.size_bytes = len(content)
        if self._direct is not False:
            artifact = self._upload_direct(ref, content)
            if artifact is not None:
                return artifact
        return self._upload_streamed(ref, content)

    def _upload_direct(self, ref: common_pb2.ArtifactRef, content: bytes) -> Artifact | None:
        request = coordinator_pb2.RequestUploadRequest(
            sha256=ref.sha256, size_bytes=ref.size_bytes, endpoint=self._storage_endpoint
        )
        response, _ = self._call(
            lambda: self._artifacts.RequestUpload(
                request, metadata=self._metadata, timeout=self._retry.timeout
            )
        )
        if not response.direct:
            self._direct = False
            return None
        self._direct = True
        if not response.already_stored:
            transfer = convert.transfer_from_proto(response.transfer)
            self._with_direct_retry(lambda: direct.put(transfer, content))
        committed, _ = self._call(
            lambda: self._artifacts.CommitUpload(
                coordinator_pb2.CommitUploadRequest(descriptor=ref),
                metadata=self._metadata,
                timeout=self._retry.timeout,
            )
        )
        artifact = convert.artifact_from_proto(committed.artifact)
        if artifact.sha256 != ref.sha256:
            raise RemoteCoordinatorError("ARTIFACT_HASH_MISMATCH", "server stored different bytes")
        return artifact

    def _with_direct_retry(self, operation: Callable[[], T]) -> T:
        backoff = self._retry.initial_backoff
        for attempt in range(1, self._retry.attempts + 1):
            try:
                return operation()
            except direct.DirectTransferError as error:
                if not error.retryable or attempt == self._retry.attempts:
                    raise RemoteCoordinatorError("DIRECT_TRANSFER_FAILED", str(error)) from error
                self._sleep(backoff)
                backoff = min(backoff * 2, self._retry.max_backoff)
        raise AssertionError("unreachable")

    def _upload_streamed(self, ref: common_pb2.ArtifactRef, content: bytes) -> Artifact:
        def chunks() -> Iterator[coordinator_pb2.UploadArtifactChunk]:
            offsets = range(0, max(len(content), 1), CHUNK_SIZE)
            for index, offset in enumerate(offsets):
                chunk = coordinator_pb2.UploadArtifactChunk(
                    data=content[offset : offset + CHUNK_SIZE]
                )
                if index == 0:
                    chunk.descriptor.CopyFrom(ref)
                yield chunk

        response, _ = self._call(
            lambda: self._artifacts.UploadArtifact(
                chunks(), metadata=self._metadata, timeout=self._retry.timeout
            )
        )
        artifact = convert.artifact_from_proto(response.artifact)
        if artifact.sha256 != ref.sha256:
            raise RemoteCoordinatorError("ARTIFACT_HASH_MISMATCH", "server stored different bytes")
        return artifact

    def read_artifact(self, artifact_id: str) -> bytes:
        if self._cache_dir is None:
            raise RemoteCoordinatorError("NO_CACHE", "artifact cache directory is not configured")
        _, path = self.download_artifact(artifact_id, self._cache_dir)
        content = path.read_bytes()
        if self._cache_max_bytes is not None:
            enforce_cache_limit(self._cache_dir, self._cache_max_bytes)
        return content

    def download_artifact(self, artifact_id: str, cache_dir: Path) -> tuple[Artifact, Path]:
        request = coordinator_pb2.DownloadArtifactRequest(
            artifact_id=artifact_id, endpoint=self._storage_endpoint
        )
        fetched: tuple[Artifact, bytes] | None = None
        if self._direct is not False:
            fetched = self._download_direct(request, cache_dir)
        if fetched is None:
            fetched = self._download_streamed(request)
        artifact, body = fetched
        return artifact, self._store_in_cache(cache_dir, artifact, body)

    def _download_direct(
        self, request: coordinator_pb2.DownloadArtifactRequest, cache_dir: Path
    ) -> tuple[Artifact, bytes] | None:
        response, _ = self._call(
            lambda: self._artifacts.RequestDownload(
                request, metadata=self._metadata, timeout=self._retry.timeout
            )
        )
        if not response.direct:
            self._direct = False
            return None
        self._direct = True
        artifact = convert.artifact_from_proto(response.artifact)
        cached = cache_dir / artifact.sha256
        if cached.is_file():
            content = cached.read_bytes()
            if hashlib.sha256(content).hexdigest() == artifact.sha256:
                return artifact, content
        transfer = convert.transfer_from_proto(response.transfer)
        body = self._with_direct_retry(
            lambda: direct.get(transfer, artifact.sha256, artifact.size_bytes)
        )
        return artifact, body

    def _download_streamed(
        self, request: coordinator_pb2.DownloadArtifactRequest
    ) -> tuple[Artifact, bytes]:
        def fetch() -> tuple[Artifact, bytes]:
            artifact: Artifact | None = None
            body = bytearray()
            for chunk in self._artifacts.DownloadArtifact(
                request, metadata=self._metadata, timeout=self._retry.timeout
            ):
                if chunk.HasField("artifact"):
                    artifact = convert.artifact_from_proto(chunk.artifact)
                body.extend(chunk.data)
            if artifact is None:
                raise RemoteCoordinatorError("INVALID_RESPONSE", "missing artifact header")
            return artifact, bytes(body)

        (artifact, body), _ = self._call(fetch)
        if hashlib.sha256(body).hexdigest() != artifact.sha256 or len(body) != artifact.size_bytes:
            raise RemoteCoordinatorError("ARTIFACT_HASH_MISMATCH", "downloaded bytes are corrupt")
        return artifact, body

    def _store_in_cache(self, cache_dir: Path, artifact: Artifact, body: bytes) -> Path:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / artifact.sha256
        if not path.exists():
            temporary = cache_dir / f"{artifact.sha256}.{secrets.token_hex(6)}.part"
            temporary.write_bytes(body)
            os.replace(temporary, path)
        else:
            os.utime(path)
        return path
