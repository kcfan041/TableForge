"""End-to-end check of the local control panel (``tableforge ui``) against a real cluster.

Runs a coordinator (gRPC) in this process, creates a node invite, starts the real
``tableforge ui`` command as a subprocess and drives its API like the page does: join,
connection check, start a worker, see it finish a 6-max experiment, stop it, and shut the
panel down. Prints one line per step; a watchdog aborts after --timeout seconds.

    uv run python scripts/local_ui_e2e.py
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tableforge.composition import build_services
from tableforge.domain.auth import InviteScope
from tableforge.settings import Settings
from tableforge.transport.control import ControlServicer
from tableforge.transport.invite_issue import issue_invite
from tableforge.transport.server import ServerConfig, start_server
from tableforge.transport.tls import generate_self_signed

STEP = "startup"


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def step(name: str) -> None:
    global STEP
    STEP = name
    print(f"[....] {name}", flush=True)


def ok(detail: str = "") -> None:
    print(f"[ OK ] {STEP}{': ' + detail if detail else ''}", flush=True)


def fail(detail: str) -> None:
    print(f"[FAIL] {STEP}: {detail}", flush=True)
    os._exit(1)


def expect(condition: bool, detail: str) -> None:
    if not condition:
        fail(detail)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Panel:
    def __init__(self, port: int) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.token = ""

    def load_token(self) -> None:
        with urllib.request.urlopen(self.base + "/", timeout=10) as response:
            html = response.read().decode()
        match = re.search(r'name="tableforge-ui-token" content="([^"]+)"', html)
        expect(match is not None, "page has no token")
        assert match is not None
        self.token = match.group(1)

    def call(self, method: str, path: str, body: object | None = None, timeout: float = 60) -> Any:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("X-TableForge-UI", self.token)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"null")


def main() -> int:
    timeout = float(sys.argv[sys.argv.index("--timeout") + 1]) if "--timeout" in sys.argv else 240
    timer = threading.Timer(
        timeout, lambda: (print(f"[FAIL] timed out during: {STEP}", flush=True), os._exit(2))
    )
    timer.daemon = True
    timer.start()
    started = time.monotonic()
    ui: subprocess.Popen[str] | None = None

    with tempfile.TemporaryDirectory(prefix="tableforge-local-e2e-") as directory:
        root = Path(directory)
        coordinator_dir = root / "coordinator"
        node_dir = root / "node"
        step("start coordinator")
        settings = Settings(data_dir=coordinator_dir)
        services = build_services(settings)
        generate_self_signed(coordinator_dir / "tls", ())
        clock = Clock()
        grpc_port = free_port()
        server = start_server(
            services.coordinator,
            services.repositories.auth,
            clock,
            f"127.0.0.1:{grpc_port}",
            __import__("tableforge.transport.tls", fromlist=["TlsMaterial"]).TlsMaterial(
                coordinator_dir / "tls" / "server.crt", coordinator_dir / "tls" / "server.key"
            ),
            config=ServerConfig(),
            control=ControlServicer(
                services.experiments,
                services.repositories.checkpoints,
                services.repositories.auth,
                clock,
            ),
        )
        stop_reconcile = threading.Event()

        def reconcile() -> None:
            while not stop_reconcile.wait(0.5):
                services.experiments.reconcile_all(clock.now())

        threading.Thread(target=reconcile, daemon=True).start()
        invite = issue_invite(
            services.repositories.auth,
            settings,
            f"127.0.0.1:{grpc_port}",
            InviteScope(node=True, role=None),
            timedelta(hours=1),
            "local-e2e",
            clock.now(),
        ).code
        ok(f"127.0.0.1:{grpc_port}")

        try:
            step("start `tableforge ui`")
            ui_port = free_port()
            environment = dict(os.environ)
            environment["APPDATA"] = str(root / "appdata")
            environment["XDG_CONFIG_HOME"] = str(root / "config")
            environment["PYTHONIOENCODING"] = "utf-8"
            ui = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "tableforge.cli.main",
                    "ui",
                    "--home",
                    str(node_dir.parent / "home"),
                    "--port",
                    str(ui_port),
                    "--no-browser",
                    "--no-gpu",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                env=environment,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            )
            panel = Panel(ui_port)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    panel.load_token()
                    break
                except (urllib.error.URLError, OSError):
                    if ui.poll() is not None:
                        fail(f"ui exited: {ui.stdout.read() if ui.stdout else ''}")
                    time.sleep(0.3)
            expect(bool(panel.token), "panel did not come up")
            status, body = panel.call("GET", "/api/status")
            expect(status == 200 and body["joined"] is False, f"{status} {body}")
            ok(panel.base)

            step("join with the invite")
            status, body = panel.call("POST", "/api/join", {"invite": invite})
            expect(status == 200, f"{status} {body}")
            expect(body["status"]["joined"] is True, "not joined after join")
            status, again = panel.call("POST", "/api/join", {"invite": invite})
            expect(status == 400 and "already belongs" in again["detail"], f"rejoin: {again}")
            ok(f"node {body['report']['node_id']}")

            step("connection check")
            status, report = panel.call("POST", "/api/doctor", timeout=90)
            expect(status == 200, f"{status} {report}")
            names = [check["name"] for check in report["checks"]]
            failed = [check for check in report["checks"] if not check["ok"]]
            expect(report["ok"], f"failed checks: {failed}")
            ok(", ".join(names))

            step("start a worker and run a 6-max experiment")
            status, worker = panel.call("POST", "/api/workers")
            expect(status == 200, f"{status} {worker}")
            number = worker["worker"]
            experiment = services.experiments.create("6max-rule-smoke", clock.now())
            services.experiments.start(experiment.experiment_id, clock.now())
            deadline = time.monotonic() + 120
            final_status = ""
            while time.monotonic() < deadline:
                final_status = services.experiments.experiments.get(
                    experiment.experiment_id
                ).status.value
                if final_status in ("completed", "failed"):
                    break
                time.sleep(0.5)
            _, log = panel.call("GET", f"/api/workers/{number}/log")
            expect(
                final_status == "completed", f"experiment {final_status}; log {log['lines'][-5:]}"
            )
            expect(log["worker"]["jobs"] == 4, f"worker jobs {log['worker']['jobs']}")
            ok(f"worker {number}: state {log['worker']['state']}, {log['worker']['jobs']} jobs")

            step("stop the worker cleanly")
            status, stopped = panel.call("POST", f"/api/workers/{number}/stop", timeout=40)
            expect(status == 200 and stopped["running"] is False, f"{status} {stopped}")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                _, log = panel.call("GET", f"/api/workers/{number}/log")
                if log["worker"]["stopped_at"]:
                    break
                time.sleep(0.2)
            events = [line.get("event") for line in log["lines"]]
            expect(log["worker"]["exit_code"] == 0, f"exit {log['worker']['exit_code']}")
            expect("worker_stopped" in events, f"events {events[-5:]}")
            ok(f"exit 0, last events {events[-2:]}")

            step("closing the panel stops its workers")
            status, second = panel.call("POST", "/api/workers")
            expect(status == 200, f"{status} {second}")
            time.sleep(3)
            _, body = panel.call("GET", "/api/status")
            pids = [item["pid"] for item in body["workers"] if item["running"]]
            expect(len(pids) == 1, f"running {pids}")
            if sys.platform == "win32":
                ui.send_signal(__import__("signal").CTRL_BREAK_EVENT)
            else:
                ui.send_signal(__import__("signal").SIGINT)
            try:
                ui.wait(30)
            except subprocess.TimeoutExpired:
                fail("panel did not exit after Ctrl+C")
            alive = [pid for pid in pids if _alive(pid)]
            expect(not alive, f"worker processes still running: {alive}")
            output = ui.stdout.read() if ui.stdout else ""
            expect(ui.returncode == 0, f"panel exit {ui.returncode}: {output[-500:]}")
            expect("Traceback" not in output, f"panel printed a traceback: {output[-800:]}")
            ok(f"panel exit {ui.returncode}")
            ui = None
        finally:
            if ui is not None and ui.poll() is None:
                ui.kill()
            stop_reconcile.set()
            server.stop(2)
    print(f"all local panel checks passed in {time.monotonic() - started:.1f}s", flush=True)
    return 0


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        output = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
        ).stdout
        return str(pid) in output
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


if __name__ == "__main__":
    sys.exit(main())
