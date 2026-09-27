from __future__ import annotations

import sys
import textwrap
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tableforge.transport.credentials import RemoteCredentials
from tableforge.transport.join import JoinError
from tableforge.web.local import (
    TOKEN_HEADER,
    LocalUiContext,
    WorkerManager,
    create_local_app,
)

FAKE_WORKER = textwrap.dedent(
    """
    import json, os, pathlib, signal, sys, time
    stop = False
    stop_file = pathlib.Path(os.environ["TABLEFORGE_STOP_FILE"])
    def handler(*_):
        global stop
        stop = True
    signal.signal(signal.SIGINT, handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, handler)
    print(json.dumps({"event": "worker_started", "argv": sys.argv[1:]}), flush=True)
    print(json.dumps({"event": "job_finished", "status": "succeeded"}), flush=True)
    print("plain text line", flush=True)
    print(json.dumps({"event": "worker_idle"}), flush=True)
    deadline = time.time() + 30
    while not stop and not stop_file.exists() and time.time() < deadline:
        time.sleep(0.05)
    print(json.dumps({"event": "worker_stopped", "jobs": 1}), flush=True)
    """
)


@dataclass
class Panel:
    client: TestClient
    context: LocalUiContext
    tmp: Path

    def get(self, path: str) -> object:
        response = self.client.get(path, headers={TOKEN_HEADER: self.context.token})
        assert response.status_code == 200, response.text
        return response.json()

    def post(self, path: str, body: object | None = None) -> tuple[int, object]:
        response = self.client.post(path, json=body, headers={TOKEN_HEADER: self.context.token})
        return response.status_code, response.json()


@pytest.fixture
def panel(tmp_path: Path) -> Iterator[Panel]:
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE_WORKER, encoding="utf-8")
    static = tmp_path / "static"
    static.mkdir()
    (static / "local.html").write_text("<html><head></head><body>local</body></html>", "utf-8")
    (static / "index.html").write_text("<html>coordinator</html>", "utf-8")
    manager = WorkerManager(tmp_path / "node", [sys.executable, str(script)])
    context = LocalUiContext(
        data_dir=tmp_path / "node",
        operator_dir=tmp_path / "operators",
        manager=manager,
        node_id_for=lambda _: "node-test",
        static_dir=static,
    )
    with TestClient(create_local_app(context), base_url="http://127.0.0.1:8765") as client:
        try:
            yield Panel(client, context, tmp_path)
        finally:
            manager.stop_all(timeout=10)


def joined(panel: Panel) -> None:
    RemoteCredentials(
        "100.64.0.9:7443", "session", "cert", cluster_id="cluster-a", storage_endpoint="tailnet"
    ).save(panel.context.data_dir / "coordinator.json")


def test_page_embeds_token_and_api_requires_it(panel: Panel) -> None:
    page = panel.client.get("/")

    assert page.status_code == 200
    assert f'content="{panel.context.token}"' in page.text
    assert panel.client.get("/api/status").status_code == 403
    assert panel.client.get("/api/status", headers={TOKEN_HEADER: "wrong"}).status_code == 403
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]


def test_other_host_headers_are_rejected(panel: Panel) -> None:
    client = TestClient(panel.client.app, base_url="http://evil.example:8765")

    assert client.get("/").status_code == 403
    assert client.get("/api/status", headers={TOKEN_HEADER: panel.context.token}).status_code == 403


def test_status_before_and_after_joining(panel: Panel) -> None:
    before = panel.get("/api/status")
    assert isinstance(before, dict) and before["joined"] is False

    joined(panel)
    after = panel.get("/api/status")

    assert isinstance(after, dict) and after["joined"] is True
    assert after["node"] == {
        "coordinator": "100.64.0.9:7443",
        "cluster_id": "cluster-a",
        "storage_endpoint": "tailnet",
        "session_expires_at": None,
    }


def test_join_reports_errors(panel: Panel, monkeypatch: pytest.MonkeyPatch) -> None:
    status, body = panel.post("/api/join", {"invite": "not-an-invite-code"})

    assert status == 400
    assert isinstance(body, dict) and "tfinv1_" in str(body["detail"])

    def fake_join(invite: str, data_dir: Path, operator_dir: Path, node_id_for: object) -> dict:
        raise JoinError("cannot reach the coordinator")

    monkeypatch.setattr("tableforge.web.local.join_with_code", fake_join)
    status, body = panel.post("/api/join", {"invite": "tfinv1_" + "x" * 20})
    assert status == 400 and "cannot reach" in str(body)


def test_workers_need_a_joined_node(panel: Panel) -> None:
    status, body = panel.post("/api/workers")

    assert status == 409 and "join" in str(body)


def test_start_log_and_stop_worker(panel: Panel) -> None:
    joined(panel)

    status, started = panel.post("/api/workers")
    assert status == 200 and isinstance(started, dict)
    number = started["worker"]
    deadline = time.monotonic() + 20
    log: dict = {}
    while time.monotonic() < deadline:
        log = panel.get(f"/api/workers/{number}/log")  # type: ignore[assignment]
        if log["worker"]["state"] == "idle":
            break
        time.sleep(0.1)

    events = [line.get("event") for line in log["lines"]]
    assert events[:2] == ["worker_started", "job_finished"]
    assert {"message": "plain text line"}.items() <= next(
        line for line in log["lines"] if "message" in line
    ).items()
    argv = log["lines"][0]["argv"]
    assert argv == ["--data-dir", str(panel.context.data_dir), "worker", "start", "--remote"]
    assert log["worker"]["jobs"] == 1 and log["worker"]["running"] is True

    status, stopped = panel.post(f"/api/workers/{number}/stop")
    assert status == 200 and isinstance(stopped, dict)
    assert stopped["running"] is False
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        final = panel.get(f"/api/workers/{number}/log")
        assert isinstance(final, dict)
        if final["worker"]["stopped_at"]:
            break
        time.sleep(0.1)
    assert final["worker"]["exit_code"] == 0, "worker should exit cleanly on the stop signal"
    assert final["lines"][-1]["event"] == "worker_stopped"
    assert final["worker"]["state"] == "stopped"


def test_stop_all_on_shutdown(panel: Panel) -> None:
    joined(panel)
    panel.post("/api/workers")
    panel.post("/api/workers")
    assert len(panel.context.manager.running()) == 2

    panel.context.manager.stop_all(timeout=10)

    assert panel.context.manager.running() == []


def test_unknown_worker(panel: Panel) -> None:
    status, _ = panel.post("/api/workers/99/stop")

    assert status == 404
