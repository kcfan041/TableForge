"""Local control panel for a machine that contributes a worker (``tableforge ui``).

It only listens on 127.0.0.1 and only manages this machine: join a cluster with an
invite, check the connection, start/stop worker processes and show their logs. Requests
must come from the page it served: every API call needs the per-process token embedded
in that page, and the Host header must be the loopback address (DNS-rebinding guard).
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from tableforge.capability.cpu_topology import child_processes, set_affinity, spread_masks
from tableforge.coordinator.play import ModelSeats
from tableforge.learning.play import GameStore, PlayError, PlayService, SeatChoice
from tableforge.models import ModelLibrary
from tableforge.processes import STOP_FILE_ENV, background_process_flags
from tableforge.transport.control_client import ControlError, RemoteControl
from tableforge.transport.credentials import RemoteCredentials
from tableforge.transport.diagnostics import check_connection
from tableforge.transport.join import JoinError, OperatorCredentials, join_with_code
from tableforge.version import package_version
from tableforge.web.cluster import (
    ClusterSetupError,
    CoordinatorSupervisor,
    create_cluster,
    tailscale_ipv4,
)

TOKEN_HEADER = "x-tableforge-ui"
STATIC_DIR = Path(__file__).parent / "static"
LOG_LINES = 500


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class WorkerProcess:
    """One ``tableforge worker start --remote`` child process and its recent log."""

    worker_number: int
    command: list[str]
    process: subprocess.Popen[str]
    started_at: str = field(default_factory=_now)
    lines: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=LOG_LINES))
    stopped_at: str | None = None
    exit_code: int | None = None
    jobs: int = 0
    state: str = "starting"
    last_error: str = ""
    stop_file: Path | None = None
    cpu_group: int | None = None
    """Index of the L3 cache group (CCD) the worker is pinned to, when spreading."""

    def view(self) -> dict[str, object]:
        return {
            "worker": self.worker_number,
            "pid": self.process.pid,
            "running": self.process.poll() is None,
            "state": self.state,
            "jobs": self.jobs,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "exit_code": self.exit_code,
            "last_error": self.last_error,
            "cpu_group": self.cpu_group,
        }


def _state_for(event: str, current: str) -> str:
    return {
        "worker_started": "running",
        "worker_idle": "idle",
        "job_finished": "running",
        "coordinator_unreachable": "reconnecting",
        "coordinator_reconnected": "running",
        "worker_stopping": "stopping",
        "worker_stopped": "stopped",
    }.get(event, current)


class WorkerManager:
    def __init__(
        self,
        data_dir: Path,
        command_prefix: list[str],
        use_gpu: bool = True,
        environment: dict[str, str] | None = None,
        cpu_groups: list[int] | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.command_prefix = command_prefix
        self.use_gpu = use_gpu
        self.environment = environment
        self.cpu_groups = spread_masks() if cpu_groups is None else cpu_groups
        """Affinity masks, one per CCD; empty when the CPU has a single L3 group."""
        self.spread = bool(self.cpu_groups) and _load_spread(data_dir)
        self._workers: list[WorkerProcess] = []
        self._lock = threading.Lock()
        self._counter = 0

    def workers(self) -> list[WorkerProcess]:
        with self._lock:
            return list(self._workers)

    def running(self) -> list[WorkerProcess]:
        return [worker for worker in self.workers() if worker.process.poll() is None]

    def start(self) -> WorkerProcess:
        if not (self.data_dir / "coordinator.json").is_file():
            raise HTTPException(409, "join a cluster first")
        with self._lock:
            self._counter += 1
            number = self._counter
        command = [
            *self.command_prefix,
            "--data-dir",
            str(self.data_dir),
            "worker",
            "start",
            "--remote",
        ]
        if not self.use_gpu:
            command.append("--no-gpu")
        environment = dict(self.environment or os.environ)
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        stop_file = self.data_dir / "worker-stop" / f"{os.getpid()}-{number}"
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        _remove_quietly(stop_file)
        if stop_file.exists():
            raise HTTPException(409, "the previous worker's stop file is still locked; try again")
        environment[STOP_FILE_ENV] = str(stop_file)
        flags = background_process_flags()
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=environment,
            creationflags=flags,
            start_new_session=os.name != "nt",
        )
        worker = WorkerProcess(number, command, process, stop_file=stop_file)
        if self.spread and self.cpu_groups:
            worker.cpu_group = self._least_used_group()
            threading.Thread(
                target=_pin, args=(process.pid, self.cpu_groups[worker.cpu_group]), daemon=True
            ).start()
        with self._lock:
            self._workers.append(worker)
            self._workers = self._workers[-20:]
        threading.Thread(target=self._pump, args=(worker,), daemon=True).start()
        return worker

    def _least_used_group(self) -> int:
        counts = [0] * len(self.cpu_groups)
        for worker in self.running():
            if worker.cpu_group is not None and worker.cpu_group < len(counts):
                counts[worker.cpu_group] += 1
        return counts.index(min(counts))

    def set_spread(self, enabled: bool) -> None:
        """Turn spreading on or off for workers started from now on (and remember it)."""
        if enabled and not self.cpu_groups:
            raise HTTPException(409, "this CPU has a single L3 cache group")
        self.spread = enabled
        _save_spread(self.data_dir, enabled)

    def cpu_view(self) -> dict[str, object]:
        return {
            "groups": len(self.cpu_groups),
            "spread": self.spread,
            "available": bool(self.cpu_groups),
        }

    def stop(self, number: int, timeout: float = 15.0) -> WorkerProcess:
        worker = next((item for item in self.workers() if item.worker_number == number), None)
        if worker is None:
            raise HTTPException(404, "no such worker")
        if worker.process.poll() is None:
            worker.state = "stopping"
            _interrupt(worker)
            try:
                worker.process.wait(timeout)
            except subprocess.TimeoutExpired:
                worker.process.kill()
                worker.process.wait(5)
        if worker.stop_file is not None:
            _remove_quietly(worker.stop_file)
        return worker

    def stop_all(self, timeout: float = 15.0) -> None:
        for worker in self.running():
            self.stop(worker.worker_number, timeout)

    def _pump(self, worker: WorkerProcess) -> None:
        stream = worker.process.stdout
        assert stream is not None
        for raw in stream:
            line = raw.rstrip("\n")
            if not line:
                continue
            entry: dict[str, Any]
            try:
                parsed = json.loads(line)
                entry = parsed if isinstance(parsed, dict) else {"message": line}
            except json.JSONDecodeError:
                entry = {"message": line}
            entry.setdefault("timestamp", _now())
            event = str(entry.get("event", ""))
            if event:
                worker.state = _state_for(event, worker.state)
            if event == "job_finished":
                worker.jobs += 1
            if event in ("worker_stopping", "coordinator_unreachable"):
                worker.last_error = str(entry.get("reason") or entry.get("detail") or "")
            if not event and ("error" in line.lower() or line.startswith("worker stopped")):
                worker.last_error = line[:300]
            worker.lines.append(entry)
        worker.exit_code = worker.process.wait()
        worker.stopped_at = _now()
        if worker.state not in ("stopped",):
            worker.state = "stopped" if worker.exit_code == 0 else "failed"


def _remove_quietly(path: Path, attempts: int = 10) -> None:
    """Delete a stop file; on Windows another process (antivirus, indexer, the exiting worker)
    may hold it for a moment, so retry briefly and leave it if it stays locked (a stale stop
    file only affects a worker started with the same number, which gets a fresh path)."""
    for attempt in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == attempts - 1:
                return
            time.sleep(0.1)


def _interrupt(worker: WorkerProcess) -> None:
    """Ask the worker to finish its current step and exit, like Ctrl+C in a terminal.

    On Windows the worker runs with a hidden console of its own, which a console control
    event from here cannot reach, so it is asked to stop through its stop file.
    """
    process = worker.process
    try:
        if sys.platform == "win32":
            if worker.stop_file is None:
                process.terminate()
                return
            worker.stop_file.write_text(_now(), encoding="utf-8")
        else:
            process.send_signal(signal.SIGINT)
    except (OSError, ValueError):
        process.terminate()


_SPREAD_FILE = "spread-workers.json"


def _load_spread(data_dir: Path) -> bool:
    path = data_dir / _SPREAD_FILE
    try:
        return bool(json.loads(path.read_text(encoding="utf-8")).get("enabled", True))
    except (OSError, ValueError, AttributeError):
        return True


def _save_spread(data_dir: Path, enabled: bool) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / _SPREAD_FILE).write_text(json.dumps({"enabled": enabled}), encoding="utf-8")


def _pin(pid: int, mask: int, wait: float = 15.0) -> None:
    """Pin a worker and, once it exists, the interpreter its launcher starts.

    ``.venv\\Scripts\\python.exe`` is a small launcher that starts the real Python as a
    child; the child takes the affinity the launcher has when it starts, so the launcher
    is pinned first and the child is pinned again when it appears.
    """
    set_affinity(pid, mask)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        found = child_processes(pid)
        if found:
            for child in found:
                set_affinity(child, mask)
            return
        time.sleep(0.2)


def default_command_prefix() -> list[str]:
    return [sys.executable, "-m", "tableforge.cli.main"]


class JoinRequest(BaseModel):
    invite: str = Field(min_length=10, max_length=4096)


@dataclass(frozen=True)
class LocalUiContext:
    data_dir: Path
    operator_dir: Path
    manager: WorkerManager
    node_id_for: Callable[[Path], str]
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    static_dir: Path | None = STATIC_DIR
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    supervisor: CoordinatorSupervisor | None = None
    detect_tailscale: Callable[[], str | None] = tailscale_ipv4
    models_dir: Path | None = None
    """Downloaded models for local play (None: local play is off)."""


class CreateClusterRequest(BaseModel):
    network: str = Field(pattern="^(tailscale|local)$")


class WebToggleRequest(BaseModel):
    enabled: bool


def create_local_app(context: LocalUiContext) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        # Runs on Ctrl+C before uvicorn re-raises the signal and the process ends, so
        # workers started from this panel never outlive it.
        await asyncio.to_thread(context.manager.stop_all)

    app = FastAPI(
        title="TableForge local",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    lock = threading.Lock()

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[..., Any]) -> Response:
        host = request.headers.get("host", "").rsplit(":", 1)[0].strip("[]")
        if host not in context.allowed_hosts:
            return Response("forbidden host", status_code=403)
        if request.url.path.startswith("/api/") and not secrets.compare_digest(
            request.headers.get(TOKEN_HEADER, ""), context.token
        ):
            return Response(
                '{"detail":"reload the page"}', status_code=403, media_type="application/json"
            )
        response: Response = await call_next(request)
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
        )
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    def status() -> dict[str, object]:
        path = context.data_dir / "coordinator.json"
        node: dict[str, object] | None = None
        if path.is_file():
            credentials = RemoteCredentials.load(path)
            node = {
                "coordinator": credentials.target,
                "cluster_id": credentials.cluster_id,
                "storage_endpoint": credentials.storage_endpoint or "default",
                "session_expires_at": credentials.expires_at.isoformat()
                if credentials.expires_at
                else None,
            }
        operator: dict[str, object] | None = None
        try:
            saved = OperatorCredentials.load(context.operator_dir)
            operator = {
                "cluster_id": saved.cluster_id,
                "role": saved.role,
                "address": saved.address,
            }
        except (JoinError, OSError, KeyError, ValueError):
            operator = None
        hosting: dict[str, object] = {"hosted": False}
        if context.supervisor is not None:
            hosting = context.supervisor.status()
        return {
            "version": package_version(),
            "data_dir": str(context.data_dir),
            "joined": node is not None,
            "node": node,
            "operator": operator,
            "hosting": hosting,
            "use_gpu": context.manager.use_gpu,
            "cpu": context.manager.cpu_view(),
            "workers": [worker.view() for worker in context.manager.workers()],
        }

    def supervisor() -> CoordinatorSupervisor:
        if context.supervisor is None:
            raise HTTPException(409, "this panel cannot host a cluster")
        return context.supervisor

    def control() -> RemoteControl:
        try:
            credentials = OperatorCredentials.load(context.operator_dir)
        except (JoinError, OSError, KeyError, ValueError) as error:
            raise HTTPException(403, "this computer has no cluster management access") from error
        return RemoteControl(credentials, timeout=20.0)

    def remote(call: Callable[[RemoteControl], Any]) -> Any:
        try:
            return call(control())
        except ControlError as error:
            raise HTTPException(_http_status(error.code), str(error)) from error

    @app.get("/api/cluster/session")
    def cluster_session() -> dict[str, object]:
        me = remote(lambda client: client.whoami())
        return {
            "operator_id": me.operator_id,
            "role": me.role,
            # Coordinators from before scopes send none; fall back to the role's scopes.
            "scopes": list(me.scopes) or _role_scopes(me.role),
            "label": me.label,
            "cluster_id": me.cluster_id,
            "csrf_token": None,
        }

    @app.get("/api/cluster/query/{resource}")
    def cluster_query(resource: str, argument: str = "") -> Any:
        return remote(lambda client: client.query(resource, argument))

    @app.post("/api/cluster/command/{action}")
    async def cluster_command(action: str, request: Request) -> Any:
        body = await request.body()
        try:
            arguments = json.loads(body or b"{}")
        except json.JSONDecodeError as error:
            raise HTTPException(400, "arguments must be JSON") from error
        if not isinstance(arguments, dict):
            raise HTTPException(400, "arguments must be an object")
        return await asyncio.to_thread(remote, lambda client: client.command(action, arguments))

    def local_library() -> ModelLibrary:
        if context.models_dir is None:
            raise HTTPException(409, "local play is not available in this panel")
        return ModelLibrary(context.models_dir)

    local_seats = ModelSeats(local_library)
    # Local games are saved next to the downloaded models, so they survive a restart.
    local_play = PlayService(
        local_seats,
        store=GameStore(context.models_dir.parent / "local-games")
        if context.models_dir is not None
        else None,
    )

    @app.get("/api/local/models")
    def local_models() -> list[dict[str, object]]:
        from tableforge.coordinator.models import model_view

        return [model_view(info) for info in local_library().list()]

    @app.post("/api/local/models/download")
    async def download(request: Request) -> dict[str, object]:
        from tableforge.coordinator.models import model_view
        from tableforge.transport.control_client import DownloadError, download_model

        body = await _json_object(request)
        model_id = str(body.get("model_id", "")).strip()
        target = local_library()

        def run() -> dict[str, object]:
            client = control()
            try:
                info = download_model(
                    client.command, model_id, target, replace=bool(body.get("replace", False))
                )
            except ControlError as error:
                raise HTTPException(_http_status(error.code), str(error)) from error
            except FileExistsError as error:
                raise HTTPException(409, f"model {model_id} is already on this computer") from error
            except (DownloadError, ValueError) as error:
                raise HTTPException(400, str(error)) from error
            return model_view(info)

        return await asyncio.to_thread(run)

    @app.delete("/api/local/models/{model_id}")
    def remove_local_model(model_id: str) -> dict[str, bool]:
        try:
            local_library().remove(model_id)
        except KeyError as error:
            raise HTTPException(404, "no such model on this computer") from error
        return {"ok": True}

    @app.post("/api/local/play/{action}")
    async def play(action: str, request: Request) -> Any:
        """Games against downloaded models, run on this computer (no cluster needed)."""
        from tableforge.coordinator.play import PLAY_ACTIONS, play_command
        from tableforge.learning.play import PlayError

        if action not in PLAY_ACTIONS:
            raise HTTPException(404, "unknown play action")
        body = await _json_object(request)
        local_library()

        def run() -> Any:
            try:
                return play_command(local_play, local_seats, "local", action, body)
            except KeyError as error:
                raise HTTPException(404, "no such game (it may have expired)") from error
            except ImportError as error:
                raise HTTPException(
                    409, "playing models needs PyTorch: install TableForge with --extra learning"
                ) from error
            except (PlayError, TypeError, ValueError) as error:
                raise HTTPException(400, str(error)) from error

        return await asyncio.to_thread(run)

    @app.post("/api/local/hands/import")
    async def import_hand_histories(request: Request) -> Any:
        """Read pasted GG/Natural8 hand histories on this computer (they never leave it);
        with ``coach`` (a downloaded model id), review the hero's decisions."""
        from tableforge.learning.hand_import import MAX_TEXT, Coach, coach_seats, import_hands
        from tableforge.poker.hand_history import HandHistoryError

        raw = await request.body()
        if len(raw) > MAX_TEXT + 4096:
            raise HTTPException(413, "the hand history is larger than 5 MB")
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError as error:
            raise HTTPException(400, "arguments must be JSON") from error
        if not isinstance(body, dict) or not isinstance(body.get("text"), str):
            raise HTTPException(400, "give the hand history text")
        coach_id = str(body.get("coach") or "").strip()

        def run() -> Any:
            coach = None
            if coach_id:
                from tableforge.coordinator.analysis import tournament_json
                from tableforge.poker.tournament import TournamentConfig

                info = local_seats.info(coach_id)
                mode = str(body.get("mode") or "average")
                choice = SeatChoice("model", coach_id, mode)

                def explainer_for() -> Any:
                    return local_seats(choice, 0)

                config = TournamentConfig.from_json(tournament_json(info))
                coach = Coach(explainer_for, mode, coach_id, config, coach_seats(info))
            try:
                return import_hands(str(body["text"]), coach)
            except HandHistoryError as error:
                raise HTTPException(400, str(error)) from error
            except ImportError as error:
                raise HTTPException(
                    409, "reviewing with a model needs PyTorch: install with --extra learning"
                ) from error

        try:
            return await asyncio.to_thread(run)
        except PlayError as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/api/status")
    def get_status() -> dict[str, object]:
        return status()

    @app.get("/api/setup")
    def get_setup() -> dict[str, object]:
        return {"tailscale_ip": context.detect_tailscale()}

    @app.post("/api/cluster")
    def post_cluster(body: CreateClusterRequest) -> dict[str, object]:
        hosting = supervisor()
        host: str | None = None
        if body.network == "tailscale":
            host = context.detect_tailscale()
            if host is None:
                raise HTTPException(
                    400,
                    "Tailscale is not running on this computer; start Tailscale and sign in, "
                    "or create a cluster only this computer can use",
                )

        def join_node(invite: str) -> dict[str, object]:
            return join_with_code(
                invite, context.data_dir, context.operator_dir, context.node_id_for
            )

        with lock:
            try:
                created = create_cluster(hosting.layout, host, hosting, join_node)
            except (ClusterSetupError, JoinError) as error:
                raise HTTPException(400, str(error)) from error
        return {"cluster_id": created.cluster_id, "address": created.address, "status": status()}

    @app.post("/api/cluster/service/start")
    def start_service() -> dict[str, object]:
        try:
            supervisor().ensure_running()
        except ClusterSetupError as error:
            raise HTTPException(400, str(error)) from error
        return status()

    @app.post("/api/cluster/service/stop")
    def stop_service() -> dict[str, object]:
        supervisor().stop()
        return status()

    @app.post("/api/cluster/service/web")
    def service_web(body: WebToggleRequest) -> dict[str, object]:
        try:
            supervisor().set_web(body.enabled)
        except ClusterSetupError as error:
            raise HTTPException(400, str(error)) from error
        return status()

    @app.post("/api/join")
    def post_join(body: JoinRequest) -> dict[str, object]:
        with lock:
            try:
                report = join_with_code(
                    body.invite.strip(),
                    context.data_dir,
                    context.operator_dir,
                    context.node_id_for,
                )
            except JoinError as error:
                raise HTTPException(400, str(error)) from error
        return {"report": report, "status": status()}

    @app.post("/api/doctor")
    def post_doctor() -> dict[str, object]:
        node_id = context.node_id_for(context.data_dir)
        return check_connection(context.data_dir, node_id).as_dict()

    @app.post("/api/workers")
    def start_worker() -> dict[str, object]:
        if len(context.manager.running()) >= 8:
            raise HTTPException(409, "at most 8 workers can run from this page")
        return context.manager.start().view()

    @app.post("/api/workers/spread")
    def spread_workers(body: WebToggleRequest) -> dict[str, object]:
        context.manager.set_spread(body.enabled)
        return status()

    @app.post("/api/workers/{number}/stop")
    def stop_worker(number: int) -> dict[str, object]:
        return context.manager.stop(number).view()

    @app.get("/api/workers/{number}/log")
    def worker_log(number: int, after: int = 0) -> dict[str, object]:
        worker = next(
            (item for item in context.manager.workers() if item.worker_number == number), None
        )
        if worker is None:
            raise HTTPException(404, "no such worker")
        lines = list(worker.lines)
        return {"worker": worker.view(), "lines": lines[-200:]}

    static_dir = context.static_dir
    if static_dir is not None:
        root = static_dir.resolve()

        @app.get("/{path:path}", include_in_schema=False)
        def static(path: str) -> Response:
            candidate = (root / path).resolve()
            if (
                path
                and candidate.is_file()
                and candidate.is_relative_to(root)
                and path != "local.html"
            ):
                return FileResponse(candidate)
            page = root / "local.html"
            if not page.is_file():
                return Response("local UI is not built", media_type="text/plain")
            html = page.read_text(encoding="utf-8").replace(
                "</head>",
                f'<meta name="tableforge-ui-token" content="{context.token}"></head>',
                1,
            )
            return HTMLResponse(html)

    return app


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        body = json.loads(await request.body() or b"{}")
    except json.JSONDecodeError as error:
        raise HTTPException(400, "arguments must be JSON") from error
    if not isinstance(body, dict):
        raise HTTPException(400, "arguments must be an object")
    return body


def _role_scopes(role: str) -> list[str]:
    from tableforge.domain.auth import ROLE_SCOPES, OperatorRole

    try:
        return sorted(scope.value for scope in ROLE_SCOPES[OperatorRole(role)])
    except ValueError:
        return []


def _http_status(code: str) -> int:
    if code.startswith("HTTP_") and code[5:].isdigit():
        return int(code[5:])
    return {
        "UNAUTHENTICATED": 401,
        "ROLE_REQUIRED": 403,
        "PERMISSION_DENIED": 403,
        "NOT_FOUND": 404,
        "CONFLICT": 409,
        "INVALID_STATE": 409,
        "UNAVAILABLE": 503,
        "DEADLINE_EXCEEDED": 504,
    }.get(code, 502)


def wait_until_stopped(manager: WorkerManager, should_stop: Callable[[], bool]) -> None:
    while not should_stop():
        time.sleep(0.5)
    manager.stop_all()
