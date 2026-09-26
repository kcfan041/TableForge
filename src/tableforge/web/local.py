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

from tableforge.transport.credentials import RemoteCredentials
from tableforge.transport.diagnostics import check_connection
from tableforge.transport.join import JoinError, OperatorCredentials, join_with_code
from tableforge.version import package_version

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
    ) -> None:
        self.data_dir = data_dir
        self.command_prefix = command_prefix
        self.use_gpu = use_gpu
        self.environment = environment
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
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
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
        worker = WorkerProcess(number, command, process)
        with self._lock:
            self._workers.append(worker)
            self._workers = self._workers[-20:]
        threading.Thread(target=self._pump, args=(worker,), daemon=True).start()
        return worker

    def stop(self, number: int, timeout: float = 15.0) -> WorkerProcess:
        worker = next((item for item in self.workers() if item.worker_number == number), None)
        if worker is None:
            raise HTTPException(404, "no such worker")
        if worker.process.poll() is None:
            worker.state = "stopping"
            _interrupt(worker.process)
            try:
                worker.process.wait(timeout)
            except subprocess.TimeoutExpired:
                worker.process.kill()
                worker.process.wait(5)
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


def _interrupt(process: subprocess.Popen[str]) -> None:
    """Ask the worker to finish its current step and exit, like Ctrl+C in a terminal."""
    try:
        if sys.platform == "win32":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGINT)
    except (OSError, ValueError):
        process.terminate()


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
        return {
            "version": package_version(),
            "data_dir": str(context.data_dir),
            "joined": node is not None,
            "node": node,
            "operator": operator,
            "use_gpu": context.manager.use_gpu,
            "workers": [worker.view() for worker in context.manager.workers()],
        }

    @app.get("/api/status")
    def get_status() -> dict[str, object]:
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


def wait_until_stopped(manager: WorkerManager, should_stop: Callable[[], bool]) -> None:
    while not should_stop():
        time.sleep(0.5)
    manager.stop_all()
