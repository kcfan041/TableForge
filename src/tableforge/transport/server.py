from __future__ import annotations

import hashlib
import io
from collections.abc import Callable, Iterator
from concurrent import futures
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NoReturn, Protocol

import grpc

from tableforge._generated.tableforge.v1 import (
    coordinator_pb2,
    coordinator_pb2_grpc,
)
from tableforge.application.ports import AuthRepository
from tableforge.coordinator.service import CoordinatorService
from tableforge.domain.auth import AuthenticationError
from tableforge.domain.ids import JobId, NodeId
from tableforge.transport import convert
from tableforge.transport.errors import ERROR_CODES, ERROR_METADATA_KEY
from tableforge.transport.tls import TlsMaterial

CHUNK_SIZE = 1024 * 1024
MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
MIN_LEASE = timedelta(seconds=1)
MAX_LEASE = timedelta(hours=1)


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class ServerConfig:
    session_ttl: timedelta = timedelta(days=7)


def _authenticate(
    auth: AuthRepository, context: grpc.ServicerContext, now: datetime
) -> NodeId:
    metadata = dict(context.invocation_metadata())
    header = metadata.get("authorization", "")
    if not isinstance(header, str) or not header.startswith("Bearer "):
        _abort(context, grpc.StatusCode.UNAUTHENTICATED, "UNAUTHENTICATED", "missing token")
    try:
        return auth.authenticate(header.removeprefix("Bearer "), now)
    except AuthenticationError as error:
        _abort(context, grpc.StatusCode.UNAUTHENTICATED, "UNAUTHENTICATED", str(error))


def _abort(
    context: grpc.ServicerContext, status: grpc.StatusCode, code: str, message: str
) -> NoReturn:
    context.set_trailing_metadata(((ERROR_METADATA_KEY, code),))
    context.abort(status, message)
    raise AssertionError("unreachable")


def _require_same_node(context: grpc.ServicerContext, caller: NodeId, node_id: str) -> None:
    if str(caller) != node_id:
        _abort(
            context,
            grpc.StatusCode.PERMISSION_DENIED,
            "NODE_MISMATCH",
            "request node does not match session",
        )


def _lease_duration(context: grpc.ServicerContext, value: timedelta) -> timedelta:
    if not MIN_LEASE <= value <= MAX_LEASE:
        _abort(
            context,
            grpc.StatusCode.INVALID_ARGUMENT,
            "INVALID_LEASE_DURATION",
            "lease duration out of range",
        )
    return value


def _guarded[T](context: grpc.ServicerContext, operation: Callable[[], T]) -> T:
    try:
        return operation()
    except grpc.RpcError:
        raise
    except tuple(ERROR_CODES) as error:
        code = ERROR_CODES[type(error)]
        status = (
            grpc.StatusCode.NOT_FOUND
            if code == "NOT_FOUND"
            else grpc.StatusCode.FAILED_PRECONDITION
        )
        _abort(context, status, code, str(error))
    except ValueError as error:
        _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "INVALID_ARGUMENT", str(error))


class CoordinatorServicer(coordinator_pb2_grpc.CoordinatorServiceServicer):
    def __init__(
        self,
        service: CoordinatorService,
        auth: AuthRepository,
        clock: Clock,
        config: ServerConfig,
    ) -> None:
        self._service = service
        self._auth = auth
        self._clock = clock
        self._config = config

    def Enroll(
        self, request: coordinator_pb2.EnrollRequest, context: grpc.ServicerContext
    ) -> coordinator_pb2.EnrollResponse:
        try:
            session = self._auth.enroll(
                request.enrollment_token,
                NodeId(request.node_id),
                self._clock.now(),
                self._config.session_ttl,
            )
        except AuthenticationError as error:
            _abort(context, grpc.StatusCode.UNAUTHENTICATED, "UNAUTHENTICATED", str(error))
        return coordinator_pb2.EnrollResponse(
            session_token=session.token,
            expires_at=convert.timestamp_to_proto(session.expires_at),
        )

    def RegisterNode(
        self, request: coordinator_pb2.RegisterNodeRequest, context: grpc.ServicerContext
    ) -> coordinator_pb2.RegisterNodeResponse:
        now = self._clock.now()
        caller = _authenticate(self._auth, context, now)
        _require_same_node(context, caller, request.capability.node_id)
        capability = _guarded(context, lambda: convert.capability_from_proto(request.capability))
        _guarded(context, lambda: self._service.register_node(capability, now))
        return coordinator_pb2.RegisterNodeResponse()

    def Heartbeat(
        self, request: coordinator_pb2.HeartbeatRequest, context: grpc.ServicerContext
    ) -> coordinator_pb2.HeartbeatResponse:
        now = self._clock.now()
        caller = _authenticate(self._auth, context, now)
        _require_same_node(context, caller, request.node_id)
        _guarded(
            context,
            lambda: self._service.heartbeat_node(
                NodeId(request.node_id), request.capability_revision, now
            ),
        )
        return coordinator_pb2.HeartbeatResponse()

    def ClaimJob(
        self, request: coordinator_pb2.ClaimJobRequest, context: grpc.ServicerContext
    ) -> coordinator_pb2.ClaimJobResponse:
        now = self._clock.now()
        caller = _authenticate(self._auth, context, now)
        _require_same_node(context, caller, request.node_id)
        _require_same_node(context, caller, request.capability.node_id)
        duration = _lease_duration(
            context, convert.duration_from_proto(request.lease_duration)
        )
        capability = _guarded(context, lambda: convert.capability_from_proto(request.capability))
        lease = _guarded(
            context,
            lambda: self._service.claim_job(caller, capability, now, duration),
        )
        response = coordinator_pb2.ClaimJobResponse()
        if lease is not None:
            response.lease.CopyFrom(convert.lease_to_proto(lease))
        return response

    def GetJob(
        self, request: coordinator_pb2.GetJobRequest, context: grpc.ServicerContext
    ) -> coordinator_pb2.GetJobResponse:
        _authenticate(self._auth, context, self._clock.now())
        job = _guarded(context, lambda: self._service.get_job(JobId(request.job_id)))
        return coordinator_pb2.GetJobResponse(job=convert.job_to_proto(job))

    def RenewLease(
        self, request: coordinator_pb2.RenewLeaseRequest, context: grpc.ServicerContext
    ) -> coordinator_pb2.RenewLeaseResponse:
        now = self._clock.now()
        _authenticate(self._auth, context, now)
        duration = _lease_duration(
            context, convert.duration_from_proto(request.lease_duration)
        )
        lease = _guarded(
            context,
            lambda: self._service.renew_job(
                JobId(request.job_id), request.lease_token, now, duration
            ),
        )
        return coordinator_pb2.RenewLeaseResponse(lease=convert.lease_to_proto(lease))

    def CompleteJob(
        self, request: coordinator_pb2.CompleteJobRequest, context: grpc.ServicerContext
    ) -> coordinator_pb2.CompleteJobResponse:
        now = self._clock.now()
        _authenticate(self._auth, context, now)
        outputs = tuple(convert.artifact_from_proto(artifact) for artifact in request.outputs)
        for output in outputs:
            if not self._service.artifacts.exists(output.sha256):
                _abort(
                    context,
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "ARTIFACT_MISSING",
                    "output artifact is not stored",
                )
        job = _guarded(
            context,
            lambda: self._service.complete_job(
                JobId(request.job_id), request.lease_token, now, request.result_hash, outputs
            ),
        )
        return coordinator_pb2.CompleteJobResponse(job=convert.job_to_proto(job))

    def FailJob(
        self, request: coordinator_pb2.FailJobRequest, context: grpc.ServicerContext
    ) -> coordinator_pb2.FailJobResponse:
        now = self._clock.now()
        _authenticate(self._auth, context, now)
        job = _guarded(
            context,
            lambda: self._service.fail_job(
                JobId(request.job_id),
                request.lease_token,
                now,
                request.error_code,
                request.error_summary,
                request.retryable,
                request.diagnostic_artifact_id or None,
            ),
        )
        return coordinator_pb2.FailJobResponse(job=convert.job_to_proto(job))

    def AcknowledgeCancel(
        self,
        request: coordinator_pb2.AcknowledgeCancelRequest,
        context: grpc.ServicerContext,
    ) -> coordinator_pb2.AcknowledgeCancelResponse:
        now = self._clock.now()
        _authenticate(self._auth, context, now)
        job = _guarded(
            context,
            lambda: self._service.acknowledge_cancel(
                JobId(request.job_id), request.lease_token, now
            ),
        )
        return coordinator_pb2.AcknowledgeCancelResponse(job=convert.job_to_proto(job))


class ArtifactServicer(coordinator_pb2_grpc.ArtifactServiceServicer):
    def __init__(
        self, service: CoordinatorService, auth: AuthRepository, clock: Clock
    ) -> None:
        self._service = service
        self._auth = auth
        self._clock = clock

    def UploadArtifact(
        self,
        request_iterator: Iterator[coordinator_pb2.UploadArtifactChunk],
        context: grpc.ServicerContext,
    ) -> coordinator_pb2.UploadArtifactResponse:
        _authenticate(self._auth, context, self._clock.now())
        descriptor_message = None
        buffer = io.BytesIO()
        for chunk in request_iterator:
            if descriptor_message is None:
                if not chunk.HasField("descriptor"):
                    _abort(
                        context,
                        grpc.StatusCode.INVALID_ARGUMENT,
                        "INVALID_ARGUMENT",
                        "first chunk must include descriptor",
                    )
                descriptor_message = chunk.descriptor
            buffer.write(chunk.data)
            if buffer.tell() > MAX_ARTIFACT_BYTES:
                _abort(
                    context,
                    grpc.StatusCode.RESOURCE_EXHAUSTED,
                    "ARTIFACT_TOO_LARGE",
                    "artifact exceeds maximum size",
                )
        if descriptor_message is None:
            _abort(
                context, grpc.StatusCode.INVALID_ARGUMENT, "INVALID_ARGUMENT", "empty upload"
            )
        content = buffer.getvalue()
        expected = descriptor_message.sha256
        if expected and hashlib.sha256(content).hexdigest() != expected:
            _abort(
                context,
                grpc.StatusCode.DATA_LOSS,
                "ARTIFACT_HASH_MISMATCH",
                "uploaded content does not match declared hash",
            )
        descriptor = _guarded(context, lambda: convert.descriptor_from_proto(descriptor_message))
        artifact = _guarded(
            context, lambda: self._service.upload_artifact(descriptor, content)
        )
        return coordinator_pb2.UploadArtifactResponse(
            artifact=convert.artifact_to_proto(artifact)
        )

    def DownloadArtifact(
        self,
        request: coordinator_pb2.DownloadArtifactRequest,
        context: grpc.ServicerContext,
    ) -> Iterator[coordinator_pb2.DownloadArtifactChunk]:
        _authenticate(self._auth, context, self._clock.now())
        try:
            grant = self._service.artifacts.get_download(request.artifact_id)
        except Exception as error:
            _abort(context, grpc.StatusCode.NOT_FOUND, "NOT_FOUND", str(error))
        artifact = convert.artifact_to_proto(grant.artifact)
        first = True
        with grant.path.open("rb") as source:
            while True:
                data = source.read(CHUNK_SIZE)
                if not data and not first:
                    break
                chunk = coordinator_pb2.DownloadArtifactChunk(data=data)
                if first:
                    chunk.artifact.CopyFrom(artifact)
                    first = False
                yield chunk
                if not data:
                    break


@dataclass
class RunningServer:
    server: grpc.Server
    port: int

    def stop(self, grace: float | None = 1.0) -> None:
        self.server.stop(grace).wait()


def start_server(
    service: CoordinatorService,
    auth: AuthRepository,
    clock: Clock,
    address: str,
    tls: TlsMaterial | None,
    config: ServerConfig | None = None,
    max_workers: int = 16,
) -> RunningServer:
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=max_workers),
        options=(
            ("grpc.max_receive_message_length", 8 * 1024 * 1024),
            ("grpc.max_send_message_length", 8 * 1024 * 1024),
        ),
    )
    coordinator_pb2_grpc.add_CoordinatorServiceServicer_to_server(
        CoordinatorServicer(service, auth, clock, config or ServerConfig()), server
    )
    coordinator_pb2_grpc.add_ArtifactServiceServicer_to_server(
        ArtifactServicer(service, auth, clock), server
    )
    if tls is None:
        port = server.add_insecure_port(address)
    else:
        credentials = grpc.ssl_server_credentials([tls.load()])
        port = server.add_secure_port(address, credentials)
    if port == 0:
        raise OSError(f"cannot bind coordinator to {address}")
    server.start()
    return RunningServer(server, port)
