"""End-to-end check of the coordinator web UI against a real HTTP server.

Starts coordinator services, the web server and an in-process worker in a temporary
directory, drives the API the way the browser does (cookie session + CSRF), prints one
line per step, and stops everything. A watchdog aborts the run after --timeout seconds
and reports the step that was running.

    uv run python scripts/web_e2e.py [--keep-seconds N]

With --keep-seconds the server stays up for N seconds after the checks so a browser can
be pointed at it; the admin credential is printed.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
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
from tableforge.domain.auth import OperatorRole
from tableforge.settings import Settings
from tableforge.transport.tls import generate_self_signed
from tableforge.web.app import WebContext
from tableforge.web.server import start_web_server

CURRENT_STEP = "startup"


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class Browser:
    """Cookie-keeping JSON client that behaves like the web UI."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        self.csrf: str | None = None

    def call(self, method: str, path: str, body: object | None = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if method != "GET" and self.csrf:
            request.add_header("X-CSRF-Token", self.csrf)
        try:
            with self.opener.open(request, timeout=20) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            raw = error.read()
            status = error.code
        text = raw.decode()
        try:
            return status, json.loads(text) if text else None
        except json.JSONDecodeError:
            return status, text

    def text(self, path: str) -> tuple[int, str, str]:
        with self.opener.open(self.base + path, timeout=20) as response:
            return (
                response.status,
                response.headers.get("content-type", ""),
                response.read().decode(),
            )


def step(name: str) -> None:
    global CURRENT_STEP
    CURRENT_STEP = name
    print(f"[....] {name}", flush=True)


def ok(detail: str = "") -> None:
    print(f"[ OK ] {CURRENT_STEP}{': ' + detail if detail else ''}", flush=True)


def expect(condition: bool, detail: str) -> None:
    if not condition:
        print(f"[FAIL] {CURRENT_STEP}: {detail}", flush=True)
        os._exit(1)


def watchdog(seconds: float) -> None:
    def fire() -> None:
        print(f"[FAIL] timed out after {seconds:.0f}s during: {CURRENT_STEP}", flush=True)
        os._exit(2)

    timer = threading.Timer(seconds, fire)
    timer.daemon = True
    timer.start()


def run_worker(services: Any, stop: threading.Event) -> None:
    from tableforge.cli.main import _discover_local_worker
    from tableforge.worker.loop import WorkerLoop
    from tableforge.worker.runtime import WorkerRuntime
    from tableforge.worker.simulation import SimulationExecutor

    capability = _discover_local_worker(services.settings.data_dir, use_gpu=False)
    services.coordinator.register_node(capability, Clock().now())

    class Quiet:
        def emit(self, event: str, **fields: object) -> None:
            return None

    runtime = WorkerRuntime(
        node_id=capability.node_id,
        capability=capability,
        coordinator=services.coordinator,
        clock=Clock(),
        lease_duration=timedelta(minutes=5),
        simulation_executor=SimulationExecutor(),
    )
    WorkerLoop(runtime, Quiet(), idle_seconds=0.2).run(should_stop=stop.is_set)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--keep-seconds", type=float, default=0.0)
    args = parser.parse_args()
    watchdog(args.timeout + args.keep_seconds)
    started = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="tableforge-web-e2e-") as directory:
        data_dir = Path(directory)
        step("start coordinator services and web server")
        services = build_services(Settings(data_dir=data_dir))
        generate_self_signed(data_dir / "tls", ("100.64.0.9",))
        clock = Clock()
        web = start_web_server(WebContext(services, clock), "127.0.0.1:0")
        base = f"http://127.0.0.1:{web.port}"
        admin_token = services.repositories.auth.issue_operator(
            OperatorRole.ADMIN, "e2e-admin", clock.now(), timedelta(hours=1)
        ).token
        ok(base)

        stop_worker = threading.Event()
        worker: threading.Thread | None = None
        try:
            step("serve the built single-page app")
            status, content_type, html = Browser(base).text("/")
            expect(status == 200 and "text/html" in content_type, f"{status} {content_type}")
            expect(
                '<div id="root">' in html and "/assets/" in html, "index.html is not the built app"
            )
            asset = html.split('src="', 1)[1].split('"', 1)[0]
            status, content_type, _ = Browser(base).text(asset)
            expect(status == 200 and "javascript" in content_type, f"asset {asset}: {content_type}")
            status, _, deep = Browser(base).text("/experiments/some-id")
            expect(status == 200 and deep == html, "deep link does not fall back to index.html")
            ok(asset)

            step("reject unauthenticated API calls")
            anonymous = Browser(base)
            expect(anonymous.call("GET", "/api/overview")[0] == 401, "overview without session")
            expect(
                anonymous.call("POST", "/api/session", {"credential": "tfo_nope"})[0] == 401,
                "bad credential accepted",
            )
            ok()

            step("admin signs in and creates an operator invite")
            admin = Browser(base)
            status, session = admin.call("POST", "/api/session", {"credential": admin_token})
            expect(status == 200 and session["role"] == "admin", f"{status} {session}")
            admin.csrf = session["csrf_token"]
            status, invite = admin.call(
                "POST",
                "/api/admin/invites",
                {"address": "100.64.0.9:7443", "node": False, "role": "operator", "label": "bob"},
            )
            expect(status == 200 and invite["invite"].startswith("tfinv1_"), f"{status} {invite}")
            status, refused = admin.call(
                "POST", "/api/admin/invites", {"address": "10.1.2.3:7443", "node": True}
            )
            expect(status == 400, f"uncovered address accepted: {status} {refused}")
            ok(invite["invite_id"])

            step("operator signs in with the invite (single use)")
            operator = Browser(base)
            status, session = operator.call(
                "POST", "/api/session", {"credential": invite["invite"]}
            )
            expect(status == 200 and session["role"] == "operator", f"{status} {session}")
            operator.csrf = session["csrf_token"]
            status, _ = Browser(base).call("POST", "/api/session", {"credential": invite["invite"]})
            expect(status == 401, "invite could be used twice")
            expect(operator.call("GET", "/api/admin/invites")[0] == 403, "operator sees admin page")
            ok()

            step("CSRF token is required for changes")
            no_csrf = operator.csrf
            operator.csrf = None
            status, _ = operator.call("POST", "/api/experiments", {"preset": "6max-rule-smoke"})
            expect(status == 403, f"change without CSRF returned {status}")
            operator.csrf = no_csrf
            ok()

            step("event stream is open")
            stream_request = urllib.request.Request(base + "/api/events/stream?after=0")
            stream_request.add_header("Authorization", f"Bearer {admin_token}")
            stream = urllib.request.urlopen(stream_request, timeout=20)
            expect(
                stream.headers.get("content-type", "").startswith("text/event-stream"),
                stream.headers.get("content-type", ""),
            )
            ok()

            step("operator creates and starts a 6-max experiment")
            status, created = operator.call(
                "POST", "/api/experiments", {"preset": "6max-rule-smoke"}
            )
            expect(status == 200, f"{status} {created}")
            experiment_id = created["experiment_id"]
            status, running = operator.call("POST", f"/api/experiments/{experiment_id}/start")
            expect(status == 200 and running["status"] == "running", f"{status} {running}")
            ok(experiment_id)

            step("live event arrives for the new experiment")
            seen: list[str] = []
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                line = stream.readline().decode().strip()
                if line.startswith("data: "):
                    event = json.loads(line.removeprefix("data: "))
                    seen.append(event["type"])
                    if event["type"] == "EXPERIMENT_RUNNING":
                        break
            stream.close()
            expect("EXPERIMENT_RUNNING" in seen, f"events seen: {seen}")
            ok(", ".join(seen))

            step("worker runs the jobs and the experiment completes")
            worker = threading.Thread(target=run_worker, args=(services, stop_worker), daemon=True)
            worker.start()
            deadline = time.monotonic() + 90
            detail: dict[str, Any] = {}
            while time.monotonic() < deadline:
                services.experiments.reconcile_all(clock.now())
                _, detail = operator.call("GET", f"/api/experiments/{experiment_id}")
                if detail["status"] in ("completed", "failed"):
                    break
                time.sleep(0.5)
            expect(detail.get("status") == "completed", f"status {detail.get('status')}")
            results = detail["results"]
            expect(results["hands"] == 1200, f"hands {results['hands']}")
            expect(len(results["winnings"]) == 6 and sum(results["winnings"]) == 0, "winnings")
            ok(f"jobs {detail['job_counts']}, winnings {results['winnings']}")

            step("overview, nodes, diagnostics, events, checkpoints")
            _, overview = operator.call("GET", "/api/overview")
            expect(overview["nodes"]["online"] == 1, f"overview nodes {overview['nodes']}")
            _, nodes = operator.call("GET", "/api/nodes")
            expect(len(nodes) == 1 and nodes[0]["online"], "nodes list")
            _, diagnostics = operator.call("GET", "/api/diagnostics")
            expect("100.64.0.9" in diagnostics["certificate"]["hosts"], "certificate hosts")
            _, events = operator.call("GET", "/api/events?limit=500")
            types = {event["type"] for event in events}
            expect({"EXPERIMENT_CREATED", "EXPERIMENT_COMPLETED"} <= types, f"events {types}")
            expect(operator.call("GET", "/api/checkpoints")[0] == 200, "checkpoints")
            ok(f"{len(events)} events")

            step("admin revokes the operator; its browser session ends")
            _, operators = admin.call("GET", "/api/admin/operators")
            bob = next(item for item in operators if item["label"] == "bob")
            status, _ = admin.call("DELETE", f"/api/admin/operators/{bob['operator_id']}")
            expect(status == 200, f"revoke returned {status}")
            expect(operator.call("GET", "/api/overview")[0] == 401, "revoked session still works")
            ok()

            step("sign out")
            expect(admin.call("DELETE", "/api/session")[0] == 200, "logout")
            expect(admin.call("GET", "/api/overview")[0] == 401, "session survives logout")
            ok()

            if args.keep_seconds > 0:
                keeper = services.repositories.auth.issue_operator(
                    OperatorRole.ADMIN, "browser", clock.now(), timedelta(hours=1)
                ).token
                step(f"keep server up for {args.keep_seconds:.0f}s")
                print(f"       url:        {base}/", flush=True)
                print(f"       credential: {keeper}", flush=True)
                time.sleep(args.keep_seconds)
                ok()
        finally:
            stop_worker.set()
            if worker is not None:
                worker.join(10)
            web.stop()

    print(f"all web checks passed in {time.monotonic() - started:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
