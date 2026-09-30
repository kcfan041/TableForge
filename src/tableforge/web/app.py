"""Browser-facing HTTP API served by the coordinator (remote mode of the TableForge UI).

The cluster documents come from ``ClusterViews``, the same code the gRPC
``ControlService`` uses for ``tableforge ui`` on other machines, so both ways of
opening the UI show identical data and enforce identical roles. Every request is made on
behalf of an operator credential (viewer, operator or admin). Browsers sign in once with
an invite code or an operator credential and then use an HttpOnly, SameSite=Strict
session cookie; state-changing requests also need the per-session CSRF token. Scripts may
send ``Authorization: Bearer tfo_...``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from tableforge.application.ports import IdempotencyConflictError, JobNotFoundError
from tableforge.composition import Services
from tableforge.domain.auth import AuthenticationError, OperatorIdentity, OperatorRole, Scope
from tableforge.transport.invite import InviteCode, InviteFormatError
from tableforge.version import package_version
from tableforge.web.views import (
    COMMAND_SCOPES,
    QUERY_SCOPES,
    Clock,
    ClusterViews,
    ViewError,
    event_view,
    missing_scope,
)

SESSION_COOKIE = "tf_session"
CSRF_HEADER = "x-csrf-token"
STATIC_DIR = Path(__file__).parent / "static"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


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
    settings: dict[str, Any] = Field(default_factory=dict)
    """Training settings; ``tournament`` (a tournament spec object) and ``opponents``
    change the tournament of tournament presets."""
    seeds: list[int] | None = Field(default=None, max_length=16)


class CreateInviteRequest(BaseModel):
    address: str = Field(default="", max_length=300)
    node: bool = False
    role: OperatorRole | None = None
    scopes: list[str] | None = Field(default=None, max_length=20)
    ttl_hours: float = Field(default=24.0, gt=0, le=24 * 30)
    label: str = Field(default="", max_length=200)
    confirm_admin: bool = False


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
    views = ClusterViews(services, context.clock)

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

    @app.exception_handler(ViewError)
    async def view_error(request: Request, error: ViewError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=error.status)

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

    # Every endpoint authenticates; the scope each query or command needs is checked in
    # `query` / `command` (the same tables the gRPC ControlService uses).
    viewer = Depends(caller)

    def query(resource: str, argument: str, current: Caller) -> Any:
        _check(QUERY_SCOPES[resource], current.identity)
        return views.query(resource, argument, current.identity)

    def command(action: str, arguments: dict[str, Any], current: Caller) -> Any:
        _check(COMMAND_SCOPES[action], current.identity)
        return views.command(action, arguments, current.identity)

    def identity_view(identity: OperatorIdentity) -> dict[str, object]:
        return {
            "operator_id": str(identity.operator_id),
            "role": identity.role.value,
            "scopes": identity.scope_list(),
            "label": identity.label,
            "cluster_id": str(auth.cluster_id(context.clock.now())),
        }

    @app.get("/api/mode")
    def mode() -> dict[str, object]:
        return {"mode": "coordinator"}

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
    def overview(current: Caller = viewer) -> Any:
        return query("overview", "", current)

    @app.get("/api/nodes")
    def nodes(current: Caller = viewer) -> Any:
        return query("nodes", "", current)

    @app.get("/api/presets")
    def presets(current: Caller = viewer) -> Any:
        return query("presets", "", current)

    @app.get("/api/training-options")
    def training_options(current: Caller = viewer) -> Any:
        return query("training_options", "", current)

    @app.get("/api/experiments")
    def experiments(current: Caller = viewer) -> Any:
        return query("experiments", "", current)

    @app.get("/api/experiments/{experiment_id}")
    def experiment(experiment_id: str, current: Caller = viewer) -> Any:
        return query("experiment", experiment_id, current)

    @app.post("/api/experiments")
    def create_experiment(body: CreateExperimentRequest, current: Caller = viewer) -> Any:
        return command("create_experiment", body.model_dump(exclude_none=True), current)

    @app.post("/api/experiments/{experiment_id}/start")
    def start_experiment(experiment_id: str, current: Caller = viewer) -> Any:
        return command("start_experiment", {"experiment_id": experiment_id}, current)

    @app.post("/api/experiments/{experiment_id}/stop")
    def stop_experiment(experiment_id: str, current: Caller = viewer) -> Any:
        return command("stop_experiment", {"experiment_id": experiment_id}, current)

    @app.get("/api/experiments/{experiment_id}/matrix")
    def matrix(experiment_id: str, current: Caller = viewer) -> Any:
        return query("matrix", experiment_id, current)

    @app.get("/api/checkpoints")
    def checkpoints(agent: str = "", current: Caller = viewer) -> Any:
        return query("checkpoints", agent, current)

    @app.get("/api/query/{resource}")
    def generic_query(resource: str, argument: str = "", current: Caller = viewer) -> Any:
        """Any query from the role table (the same ones ControlService offers)."""
        if resource not in QUERY_SCOPES:
            raise HTTPException(404, f"unknown resource: {resource}")
        return query(resource, argument, current)

    @app.post("/api/command/{action}")
    async def generic_command(action: str, request: Request, current: Caller = viewer) -> Any:
        """Any command from the role table (the same ones ControlService offers)."""
        if action not in COMMAND_SCOPES:
            raise HTTPException(404, f"unknown action: {action}")
        try:
            arguments = json.loads(await request.body() or b"{}")
        except json.JSONDecodeError as error:
            raise HTTPException(400, "arguments must be JSON") from error
        if not isinstance(arguments, dict):
            raise HTTPException(400, "arguments must be an object")
        return await asyncio.to_thread(command, action, arguments, current)

    @app.get("/api/events")
    def events(limit: int = 100, current: Caller = viewer) -> Any:
        return query("events", str(limit), current)

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
                    data = json.dumps(event_view(item), sort_keys=True)
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
    def diagnostics(current: Caller = viewer) -> Any:
        return query("diagnostics", "", current)

    @app.get("/api/admin/invites")
    def invites(current: Caller = viewer) -> Any:
        return query("invites", "", current)

    @app.post("/api/admin/invites")
    def create_invite(body: CreateInviteRequest, current: Caller = viewer) -> Any:
        return command("create_invite", body.model_dump(mode="json", exclude_none=True), current)

    @app.delete("/api/admin/invites/{invite_id}")
    def revoke_invite(invite_id: str, current: Caller = viewer) -> Any:
        return command("revoke_invite", {"invite_id": invite_id}, current)

    @app.get("/api/admin/operators")
    def operators(current: Caller = viewer) -> Any:
        return query("operators", "", current)

    @app.delete("/api/admin/operators/{operator_id}")
    def revoke_operator(operator_id: str, current: Caller = viewer) -> Any:
        return command("revoke_operator", {"operator_id": operator_id}, current)

    @app.post("/api/admin/nodes/{node_id}/revoke")
    def revoke_node(node_id: str, current: Caller = viewer) -> Any:
        return command("revoke_node", {"node_id": node_id}, current)

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


def _check(required: Scope, identity: OperatorIdentity) -> None:
    problem = missing_scope(required, identity)
    if problem:
        raise HTTPException(403, problem)


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
