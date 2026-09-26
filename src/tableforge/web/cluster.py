"""Everything ``tableforge ui`` does to create and run a cluster on this machine.

Layout under the app home (``default_home()``)::

    coordinator/          coordinator data dir (database, TLS, artifacts)
    coordinator/service.json   how the background coordinator was started (pid, ports)
    coordinator/coordinator.log
    node/                 this machine's worker node (joined like any other node)
    operator/             operator credentials for the cluster(s) this UI manages

The coordinator runs as a detached background process so closing the UI does not
disconnect other people's workers. The UI finds it again through ``service.json`` and
restarts it (with the same settings) when it is not running.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tableforge.domain.auth import InviteScope, OperatorRole
from tableforge.settings import Settings
from tableforge.transport.tls import generate_self_signed

GRPC_PORT = 7443
WEB_LISTEN = "127.0.0.1:8080"
SERVICE_FILE = "service.json"
# `coordinator serve` exits cleanly when this file appears in its data directory; this
# works for detached background processes, which cannot receive console signals.
STOP_FILE = "stop-requested"


class ClusterSetupError(Exception):
    """A step of creating or starting the cluster failed; the message says what to do."""


def default_home() -> Path:
    override = os.environ.get("TABLEFORGE_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "TableForge"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "TableForge"
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "tableforge"


@dataclass(frozen=True, slots=True)
class Layout:
    home: Path

    @property
    def coordinator(self) -> Path:
        return self.home / "coordinator"

    @property
    def node(self) -> Path:
        return self.home / "node"

    @property
    def operators(self) -> Path:
        return self.home / "operator"

    @property
    def service_file(self) -> Path:
        return self.coordinator / SERVICE_FILE

    @property
    def log_file(self) -> Path:
        return self.coordinator / "coordinator.log"

    @property
    def hosts_cluster(self) -> bool:
        return self.service_file.is_file()


def tailscale_ipv4(timeout: float = 5.0) -> str | None:
    """This machine's Tailscale IPv4 address, or None when Tailscale is not available."""
    executable = shutil.which("tailscale")
    if executable is None and sys.platform == "win32":
        candidate = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Tailscale"
        if (candidate / "tailscale.exe").is_file():
            executable = str(candidate / "tailscale.exe")
    if executable is None:
        return None
    try:
        output = subprocess.run(
            [executable, "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("100."):
            return line
    return None


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    """How the background coordinator runs; written once when the cluster is created."""

    listen: str
    address: str
    web_listen: str = ""
    pid: int = 0
    started_at: str = ""

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), sort_keys=True, indent=2), "utf-8")
        os.replace(temporary, path)

    @classmethod
    def load(cls, path: Path) -> ServiceConfig:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            listen=str(data["listen"]),
            address=str(data["address"]),
            web_listen=str(data.get("web_listen", "")),
            pid=int(data.get("pid", 0)),
            started_at=str(data.get("started_at", "")),
        )


def port_open(address: str, timeout: float = 1.0) -> bool:
    host, _, port = address.rpartition(":")
    try:
        with socket.create_connection((host.strip("[]"), int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class CoordinatorSupervisor:
    """Starts, finds and stops this machine's background coordinator process."""

    def __init__(self, layout: Layout, command_prefix: list[str]) -> None:
        self.layout = layout
        self.command_prefix = command_prefix

    def config(self) -> ServiceConfig | None:
        if not self.layout.service_file.is_file():
            return None
        return ServiceConfig.load(self.layout.service_file)

    def status(self) -> dict[str, object]:
        config = self.config()
        if config is None:
            return {"hosted": False}
        alive = process_alive(config.pid)
        return {
            "hosted": True,
            "running": alive and port_open(_local(config.listen)),
            "starting": alive and not port_open(_local(config.listen)),
            "address": config.address,
            "pid": config.pid if alive else None,
            "started_at": config.started_at,
            "log_file": str(self.layout.log_file),
            "web_listen": config.web_listen,
        }

    def set_web(self, enabled: bool, wait_seconds: float = 30.0) -> ServiceConfig:
        """Turn the browser-only web page on or off; restarts the service to apply it.

        The page only listens on this machine (127.0.0.1); sharing it with others goes
        through ``tailscale serve`` so it is always HTTPS.
        """
        config = self.config()
        if config is None:
            raise ClusterSetupError("this machine does not host a cluster")
        web_listen = WEB_LISTEN if enabled else ""
        if web_listen == config.web_listen and process_alive(config.pid):
            return config
        if enabled and port_open(WEB_LISTEN):
            raise ClusterSetupError(
                f"another program already uses {WEB_LISTEN}; close it and try again"
            )
        self.stop()
        updated = ServiceConfig(listen=config.listen, address=config.address, web_listen=web_listen)
        updated.save(self.layout.service_file)
        return self.start(updated, wait_seconds)

    def ensure_running(self, wait_seconds: float = 30.0) -> ServiceConfig:
        config = self.config()
        if config is None:
            raise ClusterSetupError("this machine does not host a cluster")
        if process_alive(config.pid) and port_open(_local(config.listen)):
            return config
        if process_alive(config.pid):
            return self._wait(config, wait_seconds)
        return self.start(config, wait_seconds)

    def start(self, config: ServiceConfig, wait_seconds: float = 30.0) -> ServiceConfig:
        if port_open(_local(config.listen)):
            raise ClusterSetupError(
                f"another program already uses {config.listen}; close it or restart the "
                "computer, then open TableForge again"
            )
        command = [
            *self.command_prefix,
            "--data-dir",
            str(self.layout.coordinator),
            "coordinator",
            "serve",
            "--listen",
            config.listen,
            "--http-listen",
            config.web_listen,
        ]
        environment = dict(os.environ)
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        self.layout.coordinator.mkdir(parents=True, exist_ok=True)
        log = self.layout.log_file.open("a", encoding="utf-8")
        log.write(f"--- starting coordinator {datetime.now(UTC).isoformat()} ---\n")
        log.flush()
        flags = 0
        if sys.platform == "win32":
            flags = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NO_WINDOW
            )
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=environment,
                close_fds=True,
                creationflags=flags,
                start_new_session=sys.platform != "win32",
            )
        finally:
            log.close()
        started = ServiceConfig(
            listen=config.listen,
            address=config.address,
            web_listen=config.web_listen,
            pid=process.pid,
            started_at=datetime.now(UTC).isoformat(),
        )
        started.save(self.layout.service_file)
        return self._wait(started, wait_seconds, process)

    def stop(self, timeout: float = 20.0) -> None:
        """Ask the coordinator to shut down cleanly (stop file), then force it if needed."""
        config = self.config()
        if config is None or not process_alive(config.pid):
            return
        stop_file = self.layout.coordinator / STOP_FILE
        stop_file.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and process_alive(config.pid):
                time.sleep(0.2)
            if process_alive(config.pid):
                _kill(config.pid)
        finally:
            stop_file.unlink(missing_ok=True)

    def _wait(
        self,
        config: ServiceConfig,
        wait_seconds: float,
        process: subprocess.Popen[bytes] | None = None,
    ) -> ServiceConfig:
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if port_open(_local(config.listen)):
                return config
            if process is not None and process.poll() is not None:
                break
            if process is None and not process_alive(config.pid):
                break
            time.sleep(0.2)
        tail = _tail(self.layout.log_file)
        raise ClusterSetupError(
            "the cluster service did not start" + (f"; last log lines:\n{tail}" if tail else "")
        )


def _local(listen: str) -> str:
    host, _, port = listen.rpartition(":")
    if host in ("0.0.0.0", "", "::", "[::]"):
        host = "127.0.0.1"
    return f"{host}:{port}"


def _tail(path: Path, lines: int = 15) -> str:
    if not path.is_file():
        return ""
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:])


def _kill(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
        return
    import signal

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return


@dataclass(frozen=True, slots=True)
class CreatedCluster:
    cluster_id: str
    address: str
    admin_operator_id: str


def create_cluster(
    layout: Layout,
    reachable_host: str | None,
    supervisor: CoordinatorSupervisor,
    join_node: Callable[[str], dict[str, object]],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CreatedCluster:
    """Create a cluster on this machine and make this machine its first worker.

    ``reachable_host`` is the address other machines use (the Tailscale IP); ``None``
    creates a cluster only this machine can use. The first admin credential is saved
    with the other operator credentials; ``join_node(invite)`` enrolls this machine.
    """
    from tableforge.composition import build_services
    from tableforge.transport.invite_issue import issue_invite
    from tableforge.transport.join import OperatorCredentials

    if layout.hosts_cluster:
        raise ClusterSetupError("this machine already hosts a cluster")
    if (layout.node / "coordinator.json").is_file():
        raise ClusterSetupError(
            "this machine already joined another cluster; leave it before creating one"
        )
    host = reachable_host or "127.0.0.1"
    listen = f"{host}:{GRPC_PORT}"
    if port_open(f"{host}:{GRPC_PORT}"):
        raise ClusterSetupError(
            f"port {GRPC_PORT} is already in use on {host}; another TableForge coordinator "
            "may be running"
        )
    hosts = tuple(item for item in (reachable_host,) if item)
    material = generate_self_signed(layout.coordinator / "tls", hosts)
    settings = Settings(data_dir=layout.coordinator)
    services = build_services(settings)
    auth = services.repositories.auth
    cluster_id = str(auth.cluster_id(now()))
    admin = auth.issue_operator(OperatorRole.ADMIN, "creator", now(), timedelta(days=3650))
    address = f"{host}:{GRPC_PORT}"
    OperatorCredentials(
        cluster_id=cluster_id,
        address=address,
        ca_certificate=material.certificate_path.read_text(encoding="utf-8"),
        operator_id=str(admin.operator_id),
        token=admin.token,
        role=admin.role.value,
    ).save(layout.operators)
    config = ServiceConfig(listen=listen, address=address)
    config.save(layout.service_file)
    supervisor.start(config)
    invite = issue_invite(
        auth,
        settings,
        address,
        InviteScope(node=True, role=None),
        timedelta(minutes=10),
        "this machine",
        now(),
    )
    join_node(invite.code)
    return CreatedCluster(cluster_id, address, str(admin.operator_id))
