"""End-to-end check of creating a cluster from `tableforge ui` (U1).

Starts the real `tableforge ui` with an empty home directory and drives it like the page:
create a cluster (local network), check that this machine became a worker, run a 6-max
experiment on it, close the UI and confirm the cluster service keeps running, reopen the
UI (it reattaches), stop the service, reopen the UI (it restarts the service), and stop
it again. Prints one line per step; a watchdog aborts after --timeout seconds.

    uv run python scripts/create_cluster_e2e.py
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import local_ui_e2e  # noqa: E402
from local_ui_e2e import Panel, expect, fail, free_port, ok, step  # noqa: E402
from web_e2e import Browser as WebBrowser  # noqa: E402

from tableforge.transport.control_client import RemoteControl  # noqa: E402
from tableforge.transport.join import OperatorCredentials  # noqa: E402
from tableforge.web.cluster import Layout, ServiceConfig, port_open, process_alive  # noqa: E402

STARTED_UIS: list[subprocess.Popen[str]] = []
STARTED_HOMES: list[Path] = []


def cleanup_started() -> None:
    kill_everything(STARTED_UIS, STARTED_HOMES)


local_ui_e2e.CLEANUPS.append(cleanup_started)


def start_ui(
    home: Path, port: int, environment: dict[str, str]
) -> tuple[subprocess.Popen[str], Panel]:
    STARTED_HOMES.append(home)
    ui = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tableforge.cli.main",
            "ui",
            "--home",
            str(home),
            "--port",
            str(port),
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
    STARTED_UIS.append(ui)
    panel = Panel(port)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            panel.load_token()
            return ui, panel
        except (urllib.error.URLError, OSError):
            if ui.poll() is not None:
                fail(f"ui exited: {ui.stdout.read() if ui.stdout else ''}")
            time.sleep(0.3)
    fail("ui did not come up")
    raise AssertionError


def kill_everything(uis: list[subprocess.Popen[str]], homes: list[Path]) -> None:
    """Kill leftover UIs and any background cluster service they started."""
    for ui in uis:
        if ui.poll() is None:
            ui.kill()
    for home in homes:
        service = Layout(home).service_file
        if not service.is_file():
            continue
        pid = ServiceConfig.load(service).pid
        if process_alive(pid):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"]
                if sys.platform == "win32"
                else ["kill", "-9", str(pid)],
                capture_output=True,
                check=False,
            )
    time.sleep(0.5)


def close_ui(ui: subprocess.Popen[str]) -> str:
    ui.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT)
    try:
        ui.wait(40)
    except subprocess.TimeoutExpired:
        ui.kill()
        fail("ui did not exit after Ctrl+C")
    output = ui.stdout.read() if ui.stdout else ""
    expect(ui.returncode == 0, f"ui exit {ui.returncode}: {output[-600:]}")
    expect("Traceback" not in output, f"ui traceback: {output[-800:]}")
    return output


def main() -> int:
    timeout = float(sys.argv[sys.argv.index("--timeout") + 1]) if "--timeout" in sys.argv else 300
    local_ui_e2e.watchdog(timeout)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="tableforge-create-e2e-") as directory:
        home = Path(directory) / "home"
        layout = Layout(home)
        environment = dict(os.environ, PYTHONIOENCODING="utf-8")
        environment.pop("TABLEFORGE_HOME", None)
        ui: subprocess.Popen[str] | None = None
        try:
            step("open `tableforge ui` for the first time")
            ui, panel = start_ui(home, free_port(), environment)
            _, status = panel.call("GET", "/api/status")
            expect(not status["joined"] and not status["hosting"]["hosted"], f"{status}")
            ok("setup screen: not joined, not hosting")

            step("create a cluster (this computer only)")
            status_code, created = panel.call(
                "POST", "/api/cluster", {"network": "local"}, timeout=120
            )
            expect(status_code == 200, f"{status_code} {created}")
            status = created["status"]
            expect(status["hosting"]["running"] is True, f"hosting {status['hosting']}")
            expect(status["joined"] is True, "this machine did not join as a worker")
            expect(status["operator"]["role"] == "admin", f"operator {status['operator']}")
            status_code, again = panel.call("POST", "/api/cluster", {"network": "local"})
            expect(status_code == 400, f"second create: {status_code} {again}")
            ok(f"cluster {created['cluster_id']} at {created['address']}")

            step("run a 6-max experiment on this machine's worker")
            credentials = OperatorCredentials.load(layout.operators)
            control = RemoteControl(credentials)
            status_code, worker = panel.call("POST", "/api/workers")
            expect(status_code == 200, f"{status_code} {worker}")
            experiment = control.create("6max-rule-smoke")
            control.start(experiment.experiment_id)
            deadline = time.monotonic() + 120
            view = control.get(experiment.experiment_id)
            while time.monotonic() < deadline and view.status not in ("completed", "failed"):
                time.sleep(0.5)
                view = control.get(experiment.experiment_id)
            expect(view.status == "completed", f"experiment {view.status} {dict(view.job_counts)}")
            ok(f"jobs {dict(view.job_counts)}")

            step("close the UI; the cluster service keeps running")
            pid = ServiceConfig.load(layout.service_file).pid
            close_ui(ui)
            ui = None
            time.sleep(1)
            expect(process_alive(pid), "cluster service stopped with the UI")
            expect(port_open("127.0.0.1:7443"), "cluster service does not answer")
            ok(f"service pid {pid} still running")

            step("reopen the UI; it reattaches without restarting the service")
            ui, panel = start_ui(home, free_port(), environment)
            _, status = panel.call("GET", "/api/status")
            expect(status["hosting"]["running"] is True, f"{status['hosting']}")
            expect(status["hosting"]["pid"] == pid, "service was restarted unnecessarily")
            expect(status["joined"] is True, "node lost")
            ok()

            step("stop the cluster service from the UI")
            status_code, status = panel.call("POST", "/api/cluster/service/stop", timeout=60)
            expect(status_code == 200 and not status["hosting"]["running"], f"{status['hosting']}")
            expect(not process_alive(pid), "service process still alive")
            log = layout.log_file.read_text(encoding="utf-8", errors="replace")
            expect(
                "stop_requested" in log and "coordinator_stopped" in log, "no clean shutdown in log"
            )
            ok("clean shutdown")

            step("reopening the UI restarts a stopped service")
            close_ui(ui)
            ui = None
            ui, panel = start_ui(home, free_port(), environment)
            _, status = panel.call("GET", "/api/status")
            expect(status["hosting"]["running"] is True, f"{status['hosting']}")
            new_pid = status["hosting"]["pid"]
            expect(new_pid != pid, "expected a new service process")
            ok(f"new service pid {new_pid}")

            step("the restarted cluster keeps its data and accepts work")
            control = RemoteControl(OperatorCredentials.load(layout.operators))
            listed = [item.experiment_id for item in control.list_experiments()]
            expect(experiment.experiment_id in listed, "experiment history lost")
            status_code, doctor = panel.call("POST", "/api/doctor", timeout=90)
            expect(doctor["ok"], f"doctor: {[c for c in doctor['checks'] if not c['ok']]}")
            ok("history kept, connection check passed")

            step("the browser-only web page is off by default and can be turned on")
            expect(not port_open("127.0.0.1:8080"), "web page is on by default")
            status_code, status = panel.call(
                "POST", "/api/cluster/service/web", {"enabled": True}, timeout=90
            )
            expect(status_code == 200, f"{status_code} {status}")
            expect(status["hosting"]["web_listen"] == "127.0.0.1:8080", f"{status['hosting']}")
            expect(port_open("127.0.0.1:8080"), "web page not listening")
            browser = WebBrowser("http://127.0.0.1:8080")
            code, mode = browser.call("GET", "/api/mode")
            expect(code == 200 and mode == {"mode": "coordinator"}, f"{code} {mode}")
            invite = RemoteControl(OperatorCredentials.load(layout.operators)).command(
                "create_invite", {"node": False, "role": "viewer", "ttl_hours": 1}
            )
            code, session = browser.call("POST", "/api/session", {"credential": invite["invite"]})
            expect(code == 200 and session["role"] == "viewer", f"{code} {session}")
            code, experiments = browser.call("GET", "/api/experiments")
            expect(code == 200 and len(experiments) == 1, f"{code} {experiments}")
            status_code, status = panel.call("GET", "/api/status")
            expect(status["joined"], "this machine's node lost after restart")
            ok("viewer signed in with a browser and saw the experiment")

            step("turn the browser page off again")
            status_code, status = panel.call(
                "POST", "/api/cluster/service/web", {"enabled": False}, timeout=90
            )
            expect(status_code == 200 and not status["hosting"]["web_listen"], f"{status}")
            expect(not port_open("127.0.0.1:8080"), "web page still listening")
            new_pid = status["hosting"]["pid"]
            ok()

            step("stop the service and close the UI")
            panel.call("POST", "/api/cluster/service/stop", timeout=60)
            close_ui(ui)
            ui = None
            expect(not process_alive(new_pid), "service still running")
            ok()
        finally:
            cleanup_started()
    print(f"all create-cluster checks passed in {time.monotonic() - started:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(local_ui_e2e.run(main))
