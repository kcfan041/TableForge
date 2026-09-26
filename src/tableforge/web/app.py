"""Browser-facing HTTP API served by the coordinator.

It shares the application services and the permission model with the gRPC
``ControlService``: every request is made on behalf of an operator credential (viewer,
operator or admin). Browsers sign in once with an invite code or an operator credential
and then use an HttpOnly, SameSite=Strict session cookie; state-changing requests also
need the per-session CSRF token. Scripts may send ``Authorization: Bearer tfo_...``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter, deque
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from tableforge.application.ports import IdempotencyConflictError, JobNotFoundError
from tableforge.composition import Services
from tableforge.domain.auth import (
    INVITABLE_ROLES,
    AuthenticationError,
    InviteScope,
    OperatorIdentity,
    OperatorRole,
)
from tableforge.domain.experiments import BUILTIN_PRESETS, Experiment
from tableforge.domain.ids import AgentId, ExperimentId, InviteId, NodeId, OperatorId
from tableforge.infrastructure.sql.events import DomainEvent
from tableforge.infrastructure.sql.migrations import schema_version
from tableforge.transport.invite import InviteCode, InviteFormatError
from tableforge.transport.invite_issue import InviteIssueError, coordinator_tls, issue_invite
from tableforge.version import MIN_PROTOCOL_VERSION, PROTOCOL_VERSION, package_version

SESSION_COOKIE = "tf_session"
CSRF_HEADER = "x-csrf-token"
STATIC_DIR = Path(__file__).parent / "static"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass
class LoginLimiter:
    """Slows down guessing: at most ``limit`` failed sign-ins per client per window."""

    limit: int = 10
    window_seconds: float = 300.0
    _failures: dict[str, deque[float]] = field(default_factory=dict)

    def check(self, client: str) -> None:
        failures = self._recent(client)
        if len(failures) >= self.limit:
            raise HTTPException(429, "too many failed sign-in attempts; wait a few minutes")

    def failed(self, client: str) -> None:
        self._recent(client).append(time.monotonic())

    def _recent(self, client: str) -> deque[float]:
        failures = self._failures.setdefault(client, deque())
        cutoff = time.monotonic() - self.window_seconds
        while failures and failures[0] < cutoff:
            failures.popleft()
        return failures


@dataclass(frozen=True)
class WebContext:
    services: Services
    clock: Clock
    static_dir: Path | None = STATIC_DIR
    session_ttl: timedelta = timedelta(days=30)
    operator_ttl: timedelta = timedelta(days=30)
    stream_seconds: float = 300.0
    poll_seconds: float = 1.0
    heartbeat_seconds: float = 15.0
    limiter: LoginLimiter = field(default_factory=LoginLimiter)


@dataclass(frozen=True, slots=True)
class Caller:
    identity: OperatorIdentity
    csrf_token: str | None


class LoginRequest(BaseModel):
    credential: str = Field(min_length=1, max_length=4096)


class CreateExperimentRequest(BaseModel):
    preset: str = Field(min_length=1, max_length=200)


class CreateInviteRequest(BaseModel):
    address: str = Field(min_length=3, max_length=300)
    node: bool = False
    role: OperatorRole | None = None
    ttl_hours: float = Field(default=24.0, gt=0, le=24 * 30)
    label: str = Field(default="", max_length=200)


def create_app(context: WebContext) -> FastAPI:
    app = FastAPI(
        title="TableForge",
        version=package_version(),
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    services = context.services
    auth = services.repositories.auth

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[..., Any]) -> Response:
        response: Response = await call_next(request)
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(JobNotFoundError)
    async def not_found(request: Request, error: JobNotFoundError) -> JSONResponse:
        return JSONResponse({"detail": f"not found: {error}"}, status_code=404)

    @app.exception_handler(IdempotencyConflictError)
    async def conflict(request: Request, error: IdempotencyConflictError) -> JSONResponse:
        return JSONResponse({"detail": f"conflict: {error}"}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=409)

    def caller(request: Request) -> Caller:
        now = context.clock.now()
        header = request.headers.get("authorization", "")
        if header.startswith("Bearer "):
            try:
                return Caller(auth.authenticate_operator(header.removeprefix("Bearer "), now), None)
            except AuthenticationError as error:
                raise HTTPException(401, str(error)) from error
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            raise HTTPException(401, "sign in required")
        try:
            session = auth.web_session(token, now)
        except AuthenticationError as error:
            raise HTTPException(401, str(error)) from error
        if request.method not in _SAFE_METHODS and request.headers.get(CSRF_HEADER) != (
            session.csrf_token
        ):
            raise HTTPException(403, "missing or invalid CSRF token; reload the page")
        return Caller(session.identity, session.csrf_token)

    def require(role: OperatorRole) -> Callable[[Caller], Caller]:
        def check(current: Caller = Depends(caller)) -> Caller:  # noqa: B008
            if not current.identity.role.allows(role):
                raise HTTPException(
                    403,
                    f"requires the {role.value} role; you are {current.identity.role.value}",
                )
            return current

        return check

    viewer = Depends(require(OperatorRole.VIEWER))
    operator = Depends(require(OperatorRole.OPERATOR))
    admin = Depends(require(OperatorRole.ADMIN))

    def identity_view(identity: OperatorIdentity) -> dict[str, object]:
        return {
            "operator_id": str(identity.operator_id),
            "role": identity.role.value,
            "label": identity.label,
            "cluster_id": str(auth.cluster_id(context.clock.now())),
        }

    @app.post("/api/session")
    def login(body: LoginRequest, request: Request, response: Response) -> dict[str, object]:
        client = request.client.host if request.client else "unknown"
        context.limiter.check(client)
        now = context.clock.now()
        credential = body.credential.strip()
        try:
            token = _operator_token(credential, context, now)
            session = auth.create_web_session(token, now, context.session_ttl)
        except (AuthenticationError, InviteFormatError) as error:
            context.limiter.failed(client)
            raise HTTPException(401, str(error)) from error
        secure = request.url.scheme == "https" or (
            request.headers.get("x-forwarded-proto", "") == "https"
        )
        response.set_cookie(
            SESSION_COOKIE,
            session.token,
            max_age=max(1, int((session.expires_at - now).total_seconds())),
            httponly=True,
            samesite="strict",
            secure=secure,
            path="/",
        )
        return {**identity_view(session.identity), "csrf_token": session.csrf_token}

    @app.get("/api/session")
    def current_session(current: Caller = viewer) -> dict[str, object]:
        return {**identity_view(current.identity), "csrf_token": current.csrf_token}

    @app.delete("/api/session")
    def logout(request: Request, response: Response, current: Caller = viewer) -> dict[str, bool]:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            auth.end_web_session(token)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/overview")
    def overview(current: Caller = viewer) -> dict[str, object]:
        now = context.clock.now()
        nodes = services.coordinator.list_nodes()
        online = [node for node in nodes if _online(node.last_heartbeat_at, now, services)]
        jobs = services.coordinator.jobs.list_jobs()
        experiments = services.experiments.list_experiments()
        return {
            "cluster_id": str(auth.cluster_id(now)),
            "version": package_version(),
            "nodes": {"total": len(nodes), "online": len(online)},
            "gpus": sum(len(node.capability.gpus) for node in online),
            "experiments": dict(Counter(item.status.value for item in experiments)),
            "jobs": dict(Counter(job.status.value for job in jobs)),
            "latest_event": services.repositories.events.latest_sequence(),
        }

    @app.get("/api/nodes")
    def nodes(current: Caller = viewer) -> list[dict[str, object]]:
        now = context.clock.now()
        workers: dict[str, list[dict[str, object]]] = {}
        for worker_id, node_id, label, last_seen in auth.list_worker_slots():
            workers.setdefault(str(node_id), []).append(
                {"worker_id": str(worker_id), "label": label, "last_seen_at": _iso(last_seen)}
            )
        return [
            {
                "node_id": str(node.node_id),
                "online": _online(node.last_heartbeat_at, now, services),
                "last_heartbeat_at": _iso(node.last_heartbeat_at),
                "registered_at": _iso(node.registered_at),
                "operating_system": node.capability.operating_system,
                "architecture": node.capability.architecture,
                "logical_cpu_cores": node.capability.logical_cpu_cores,
                "total_memory_bytes": node.capability.total_memory_bytes,
                "available_memory_bytes": node.capability.available_memory_bytes,
                "roles": list(node.capability.enabled_roles),
                "runtimes": list(node.capability.runtime.runtimes),
                "python_version": node.capability.runtime.python_version,
                "capability_revision": node.capability.revision,
                "warnings": list(node.capability.warnings),
                "gpus": [
                    {
                        "device_id": gpu.device_id,
                        "model": gpu.model,
                        "total_memory_bytes": gpu.total_memory_bytes,
                        "available_memory_bytes": gpu.available_memory_bytes,
                        "driver_version": gpu.driver_version,
                        "compute_capability": gpu.compute_capability,
                    }
                    for gpu in node.capability.gpus
                ],
                "workers": workers.get(str(node.node_id), []),
            }
            for node in services.coordinator.list_nodes()
        ]

    @app.get("/api/presets")
    def presets(current: Caller = viewer) -> list[dict[str, object]]:
        return [
            {
                "key": preset.key,
                "version": preset.version,
                "kind": preset.kind.value,
                "hands": preset.hands,
                "hands_per_job": preset.hands_per_job,
                "players": preset.players,
                "rounds": preset.rounds,
                "league": preset.league,
                "learner_runtime": preset.learner_runtime,
            }
            for preset in BUILTIN_PRESETS
        ]

    def experiment_view(experiment: Experiment, detailed: bool = False) -> dict[str, object]:
        jobs = services.coordinator.jobs.list_jobs(experiment.experiment_id)
        view: dict[str, object] = {
            "experiment_id": str(experiment.experiment_id),
            "preset": experiment.config.preset_key,
            "kind": experiment.config.kind.value,
            "players": experiment.config.players,
            "status": experiment.status.value,
            "revision": experiment.revision,
            "created_at": _iso(experiment.created_at),
            "updated_at": _iso(experiment.updated_at),
            "job_counts": dict(Counter(job.status.value for job in jobs)),
            "league": experiment.config.league,
        }
        if detailed:
            view["config"] = {
                key: value.value if hasattr(value, "value") else value
                for key, value in asdict(experiment.config).items()
            }
            view["jobs"] = [
                {
                    "job_id": str(job.spec.job_id),
                    "type": job.spec.type.value,
                    "payload_type": job.spec.payload_type,
                    "status": job.status.value,
                    "created_at": _iso(job.spec.created_at),
                }
                for job in jobs
            ]
            view["evaluation"] = services.experiments.evaluation_result(experiment.experiment_id)
            view["results"] = _simulation_results(services, jobs)
        return view

    @app.get("/api/experiments")
    def experiments(current: Caller = viewer) -> list[dict[str, object]]:
        items = sorted(
            services.experiments.list_experiments(), key=lambda item: item.created_at, reverse=True
        )
        return [experiment_view(item) for item in items]

    @app.get("/api/experiments/{experiment_id}")
    def experiment(experiment_id: str, current: Caller = viewer) -> dict[str, object]:
        found = services.experiments.experiments.get(ExperimentId(experiment_id))
        return experiment_view(found, detailed=True)

    @app.post("/api/experiments")
    def create_experiment(
        body: CreateExperimentRequest, current: Caller = operator
    ) -> dict[str, object]:
        created = services.experiments.create(body.preset, context.clock.now())
        return experiment_view(created)

    @app.post("/api/experiments/{experiment_id}/start")
    def start_experiment(experiment_id: str, current: Caller = operator) -> dict[str, object]:
        started = services.experiments.start(ExperimentId(experiment_id), context.clock.now())
        return experiment_view(started)

    @app.post("/api/experiments/{experiment_id}/stop")
    def stop_experiment(experiment_id: str, current: Caller = operator) -> dict[str, object]:
        stopped = services.experiments.stop(ExperimentId(experiment_id), context.clock.now())
        return experiment_view(stopped)

    @app.get("/api/experiments/{experiment_id}/matrix")
    def matrix(experiment_id: str, current: Caller = viewer) -> dict[str, object]:
        found = services.experiments.experiments.get(ExperimentId(experiment_id))
        if not found.config.league:
            return {"available": False, "reason": "not_league"}
        cells = services.experiments.league_matrix(found.experiment_id)
        if cells is None:
            return {"available": False, "reason": "no_matchups"}
        return {
            "available": True,
            "players": sorted(cells),
            "cells": {
                row: {column: asdict(cell) for column, cell in columns.items()}
                for row, columns in cells.items()
            },
        }

    @app.get("/api/checkpoints")
    def checkpoints(agent: str = "", current: Caller = viewer) -> list[dict[str, object]]:
        items = services.repositories.checkpoints.list_checkpoints(
            AgentId(agent) if agent else None
        )
        return [
            {
                "checkpoint_id": str(item.checkpoint_id),
                "agent_id": str(item.agent_id),
                "experiment_id": str(item.experiment_id),
                "round_index": item.round_index,
                "status": item.status.value,
                "decision_reason": item.decision_reason,
                "parent_id": str(item.parent_id) if item.parent_id else None,
                "sha256": item.sha256,
                "created_at": _iso(item.created_at),
                "updated_at": _iso(item.updated_at),
            }
            for item in sorted(items, key=lambda item: item.created_at, reverse=True)
        ]

    @app.get("/api/events")
    def events(limit: int = 100, current: Caller = viewer) -> list[dict[str, object]]:
        limit = max(1, min(limit, 500))
        return [_event_view(item) for item in services.repositories.events.recent_events(limit)]

    @app.get("/api/events/stream")
    async def event_stream(
        request: Request, after: int = -1, current: Caller = viewer
    ) -> StreamingResponse:
        log = services.repositories.events
        start = await asyncio.to_thread(log.latest_sequence) if after < 0 else after

        async def generate() -> AsyncIterator[str]:
            cursor = start
            began = time.monotonic()
            last_sent = began
            yield "retry: 3000\n\n"
            while time.monotonic() - began < context.stream_seconds:
                if await request.is_disconnected():
                    return
                batch = await asyncio.to_thread(log.list_events, cursor, 100)
                for item in batch:
                    cursor = item.sequence
                    data = json.dumps(_event_view(item), sort_keys=True)
                    yield f"id: {item.sequence}\nevent: domain\ndata: {data}\n\n"
                    last_sent = time.monotonic()
                if time.monotonic() - last_sent >= context.heartbeat_seconds:
                    yield ": keep-alive\n\n"
                    last_sent = time.monotonic()
                if not batch:
                    await asyncio.sleep(context.poll_seconds)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/diagnostics")
    def diagnostics(current: Caller = viewer) -> dict[str, object]:
        settings = services.settings
        material = coordinator_tls(settings)
        certificate: dict[str, object] = {"present": material.certificate_path.is_file()}
        if material.certificate_path.is_file():
            certificate["hosts"] = list(material.hosts())
            certificate["fingerprint"] = material.fingerprint()
        return {
            "version": package_version(),
            "protocol": PROTOCOL_VERSION,
            "min_protocol": MIN_PROTOCOL_VERSION,
            "schema_version": schema_version(services.repositories.database),
            "metadata_backend": settings.metadata.backend,
            "artifact_backend": settings.artifacts.backend,
            "direct_transfer": settings.artifacts.direct_transfer,
            "grpc_listen": settings.coordinator.listen,
            "http_listen": settings.coordinator.http_listen,
            "node_ttl_seconds": settings.coordinator.node_ttl_seconds,
            "certificate": certificate,
            "unpublished_events": services.repositories.events.unpublished_count(),
        }

    @app.get("/api/admin/invites")
    def invites(current: Caller = admin) -> list[dict[str, object]]:
        now = context.clock.now()
        return [
            {
                "invite_id": str(item.invite_id),
                "scope": item.scope.encode(),
                "label": item.label,
                "created_at": _iso(item.created_at),
                "expires_at": _iso(item.expires_at),
                "redeemed_at": _iso(item.redeemed_at) if item.redeemed_at else None,
                "redeemed_by": item.redeemed_by,
                "state": "revoked"
                if item.revoked
                else "used"
                if item.redeemed_at
                else "expired"
                if not item.usable(now)
                else "open",
            }
            for item in sorted(auth.list_invites(), key=lambda item: item.created_at, reverse=True)
        ]

    @app.post("/api/admin/invites")
    def create_invite(body: CreateInviteRequest, current: Caller = admin) -> dict[str, object]:
        if body.role is not None and body.role not in INVITABLE_ROLES:
            raise HTTPException(400, "the admin role cannot be granted by invite")
        try:
            scope = InviteScope(node=body.node, role=body.role)
            issued = issue_invite(
                auth,
                services.settings,
                body.address.strip(),
                scope,
                timedelta(hours=body.ttl_hours),
                body.label,
                context.clock.now(),
            )
        except InviteIssueError as error:
            raise HTTPException(400, str(error)) from error
        return {
            "invite_id": str(issued.invite.invite_id),
            "invite": issued.code,
            "scope": scope.encode(),
            "expires_at": _iso(issued.invite.expires_at),
            "warnings": list(issued.warnings),
        }

    @app.delete("/api/admin/invites/{invite_id}")
    def revoke_invite(invite_id: str, current: Caller = admin) -> dict[str, bool]:
        if not auth.revoke_invite(InviteId(invite_id)):
            raise HTTPException(404, "no open invite with that id")
        return {"ok": True}

    @app.get("/api/admin/operators")
    def operators(current: Caller = admin) -> list[dict[str, object]]:
        return [
            {
                "operator_id": str(record.identity.operator_id),
                "role": record.identity.role.value,
                "label": record.identity.label,
                "created_at": _iso(record.created_at),
                "expires_at": _iso(record.expires_at),
                "current": record.identity.operator_id == current.identity.operator_id,
            }
            for record in auth.list_operator_records()
        ]

    @app.delete("/api/admin/operators/{operator_id}")
    def revoke_operator(operator_id: str, current: Caller = admin) -> dict[str, bool]:
        if operator_id == str(current.identity.operator_id):
            raise HTTPException(400, "you cannot revoke the credential you are signed in with")
        if not auth.revoke_operator(OperatorId(operator_id)):
            raise HTTPException(404, "no active credential with that id")
        return {"ok": True}

    @app.post("/api/admin/nodes/{node_id}/revoke")
    def revoke_node(node_id: str, current: Caller = admin) -> dict[str, int]:
        return {"revoked_sessions": auth.revoke_node(NodeId(node_id))}

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    def unknown_api(path: str) -> None:
        raise HTTPException(404, "unknown API endpoint")

    static_dir = context.static_dir
    if static_dir is not None:
        root = static_dir.resolve()

        @app.get("/{path:path}", include_in_schema=False)
        def static(path: str) -> Response:
            index = root / "index.html"
            candidate = (root / path).resolve()
            if (
                path
                and path != "local.html"
                and candidate.is_file()
                and candidate.is_relative_to(root)
            ):
                headers = (
                    {"Cache-Control": "public, max-age=31536000, immutable"}
                    if path.startswith("assets/")
                    else {"Cache-Control": "no-cache"}
                )
                return FileResponse(candidate, headers=headers)
            if index.is_file():
                return FileResponse(index, headers={"Cache-Control": "no-cache"})
            return Response(
                "TableForge web UI is not built. The API is available under /api/.",
                media_type="text/plain",
            )

    return app


def _operator_token(credential: str, context: WebContext, now: datetime) -> str:
    """Turn what the user pasted into an operator credential token.

    A role-only invite is redeemed (single use) for a new credential. An invite that also
    enrolls a node is rejected before it is consumed, so it can still be used with
    ``tableforge join`` on the machine that should become a worker.
    """
    auth = context.services.repositories.auth
    if not credential.startswith("tfinv1_"):
        return credential
    code = InviteCode.decode(credential)
    if code.cluster_id != str(auth.cluster_id(now)):
        raise AuthenticationError("this invite belongs to another cluster")
    if code.scope.node:
        raise AuthenticationError(
            "this invite enrolls a worker node; run `tableforge join` with it, and ask the "
            "admin for a separate invite with only a viewer or operator role for the web"
        )
    redemption = auth.redeem_invite(
        code.secret, None, now, timedelta(seconds=1), context.operator_ttl
    )
    if redemption.operator is None:
        raise AuthenticationError("invite did not grant a web sign-in")
    return redemption.operator.token


def _online(last_heartbeat: datetime, now: datetime, services: Services) -> bool:
    return now - last_heartbeat <= timedelta(seconds=services.settings.coordinator.node_ttl_seconds)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _event_view(event: DomainEvent) -> dict[str, object]:
    return {
        "sequence": event.sequence,
        "type": event.event_type,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "version": event.aggregate_version,
        "payload": event.payload,
        "occurred_at": _iso(event.occurred_at),
    }


def _simulation_results(services: Services, jobs: Any) -> dict[str, object] | None:
    """Aggregate finished simulation summaries of an experiment."""
    totals: dict[str, Any] = {"hands": 0, "actions": 0, "showdowns": 0, "folds": 0}
    winnings: list[int] = []
    found = False
    for job in jobs:
        if job.spec.payload_type != "tableforge.v1.SimulationJobPayload":
            continue
        if job.status.value != "succeeded":
            continue
        outputs = services.coordinator.job_outputs(job.spec.job_id)
        if not outputs:
            continue
        content = services.coordinator.read_artifact(
            str(outputs[0].artifact.descriptor.artifact_id)
        )
        summary = json.loads(content)
        found = True
        for key in totals:
            totals[key] += int(summary.get(key, 0))
        values = [int(value) for value in summary.get("winnings", [])]
        if len(winnings) < len(values):
            winnings.extend([0] * (len(values) - len(winnings)))
        for index, value in enumerate(values):
            winnings[index] += value
    if not found:
        return None
    return {**totals, "winnings": winnings}
