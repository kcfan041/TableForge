from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tableforge.composition import Services, build_services
from tableforge.domain.auth import InviteScope, OperatorRole
from tableforge.settings import Settings
from tableforge.transport.invite import InviteCode
from tableforge.transport.tls import generate_self_signed
from tableforge.web.app import CSRF_HEADER, SESSION_COOKIE, LoginLimiter, WebContext, create_app
from tableforge.web.server import start_web_server


@dataclass
class Clock:
    offset: timedelta = timedelta(0)

    def now(self) -> datetime:
        return datetime.now(UTC) + self.offset


@dataclass
class Web:
    services: Services
    clock: Clock
    client: TestClient
    context: WebContext

    def credential(self, role: OperatorRole) -> str:
        return self.services.repositories.auth.issue_operator(
            role, role.value, self.clock.now(), timedelta(days=1)
        ).token

    def sign_in(self, role: OperatorRole) -> str:
        response = self.client.post("/api/session", json={"credential": self.credential(role)})
        assert response.status_code == 200, response.text
        return str(response.json()["csrf_token"])

    def invite(self, scope: InviteScope) -> str:
        now = self.clock.now()
        auth = self.services.repositories.auth
        _, secret = auth.create_invite(scope, now, timedelta(hours=1), "web")
        return InviteCode(
            cluster_id=str(auth.cluster_id(now)),
            address="127.0.0.1:7443",
            fingerprint="0" * 64,
            secret=secret,
            scope=scope,
        ).encode()


@pytest.fixture
def web(tmp_path: Path) -> Iterator[Web]:
    services = build_services(Settings(data_dir=tmp_path))
    generate_self_signed(tmp_path / "tls", ())
    clock = Clock()
    context = WebContext(services, clock, static_dir=None, stream_seconds=1.0, poll_seconds=0.05)
    with TestClient(create_app(context), base_url="https://testserver") as client:
        yield Web(services, clock, client, context)


def test_everything_requires_sign_in(web: Web) -> None:
    for path in ("/api/overview", "/api/nodes", "/api/experiments", "/api/admin/invites"):
        assert web.client.get(path).status_code == 401


def test_sign_in_with_credential_sets_hardened_cookie(web: Web) -> None:
    response = web.client.post(
        "/api/session", json={"credential": web.credential(OperatorRole.VIEWER)}
    )

    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert f"{SESSION_COOKIE}=" in cookie
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" in cookie
    assert response.json()["role"] == "viewer"
    assert web.client.get("/api/session").json()["role"] == "viewer"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_sign_in_with_operator_invite_consumes_it(web: Web) -> None:
    code = web.invite(InviteScope(node=False, role=OperatorRole.OPERATOR))

    first = web.client.post("/api/session", json={"credential": code})
    second = web.client.post("/api/session", json={"credential": code})

    assert first.status_code == 200 and first.json()["role"] == "operator"
    assert second.status_code == 401


@pytest.mark.parametrize("role", [None, OperatorRole.OPERATOR])
def test_node_invites_are_refused_without_being_consumed(
    web: Web, role: OperatorRole | None
) -> None:
    code = web.invite(InviteScope(node=True, role=role))

    response = web.client.post("/api/session", json={"credential": code})

    assert response.status_code == 401
    assert "tableforge join" in response.json()["detail"]
    invite = web.services.repositories.auth.list_invites()[0]
    assert invite.redeemed_at is None


def test_bad_credentials_are_rate_limited(web: Web) -> None:
    web.context.limiter.limit = 3
    for _ in range(3):
        assert web.client.post("/api/session", json={"credential": "tfo_wrong"}).status_code == 401

    assert web.client.post("/api/session", json={"credential": "tfo_wrong"}).status_code == 429


def test_state_changes_need_csrf_token(web: Web) -> None:
    csrf = web.sign_in(OperatorRole.OPERATOR)

    missing = web.client.post("/api/experiments", json={"preset": "hu-rule-smoke"})
    created = web.client.post(
        "/api/experiments", json={"preset": "hu-rule-smoke"}, headers={CSRF_HEADER: csrf}
    )

    assert missing.status_code == 403
    assert created.status_code == 200
    assert created.json()["status"] == "created"


def test_bearer_credentials_work_without_csrf(web: Web) -> None:
    token = web.credential(OperatorRole.OPERATOR)
    client = TestClient(web.client.app, base_url="https://testserver")

    response = client.post(
        "/api/experiments",
        json={"preset": "6max-rule-smoke"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["players"] == 6


def test_viewer_cannot_change_anything(web: Web) -> None:
    csrf = web.sign_in(OperatorRole.VIEWER)

    response = web.client.post(
        "/api/experiments", json={"preset": "hu-rule-smoke"}, headers={CSRF_HEADER: csrf}
    )

    assert response.status_code == 403
    assert "operator" in response.json()["detail"]
    assert web.client.get("/api/admin/invites").status_code == 403


def test_experiment_lifecycle_and_results(web: Web) -> None:
    csrf = web.sign_in(OperatorRole.OPERATOR)
    headers = {CSRF_HEADER: csrf}
    created = web.client.post(
        "/api/experiments", json={"preset": "hu-rule-smoke"}, headers=headers
    ).json()
    experiment_id = created["experiment_id"]

    started = web.client.post(f"/api/experiments/{experiment_id}/start", headers=headers)
    detail = web.client.get(f"/api/experiments/{experiment_id}").json()
    stopped = web.client.post(f"/api/experiments/{experiment_id}/stop", headers=headers)

    assert started.json()["status"] == "running"
    assert detail["job_counts"] == {"queued": 4}
    assert len(detail["jobs"]) == 4
    assert detail["config"]["hands"] == 1000
    assert stopped.json()["status"] == "stopped"
    assert [item["experiment_id"] for item in web.client.get("/api/experiments").json()] == [
        experiment_id
    ]


def test_errors_are_reported_as_json(web: Web) -> None:
    csrf = web.sign_in(OperatorRole.OPERATOR)

    unknown_preset = web.client.post(
        "/api/experiments", json={"preset": "nope"}, headers={CSRF_HEADER: csrf}
    )
    missing = web.client.get("/api/experiments/01a0dd5a-0000-7000-8000-000000000000")
    unknown_path = web.client.get("/api/does-not-exist")

    assert unknown_preset.status_code == 409 and "unknown preset" in unknown_preset.json()["detail"]
    assert missing.status_code == 404
    assert unknown_path.status_code == 404


def test_read_only_pages(web: Web) -> None:
    web.sign_in(OperatorRole.VIEWER)

    overview = web.client.get("/api/overview").json()
    presets = {item["key"]: item for item in web.client.get("/api/presets").json()}
    diagnostics = web.client.get("/api/diagnostics").json()

    assert overview["nodes"] == {"total": 0, "online": 0}
    assert presets["6max-rule-smoke"]["players"] == 6
    assert diagnostics["certificate"]["present"] is True
    assert diagnostics["schema_version"] == 5
    assert web.client.get("/api/nodes").json() == []
    assert web.client.get("/api/checkpoints").json() == []
    assert isinstance(web.client.get("/api/events").json(), list)


def test_admin_manages_invites_and_credentials(web: Web) -> None:
    csrf = web.sign_in(OperatorRole.ADMIN)
    headers = {CSRF_HEADER: csrf}

    created = web.client.post(
        "/api/admin/invites",
        json={"address": "127.0.0.1:7443", "node": True, "role": "operator", "label": "bob"},
        headers=headers,
    )
    unconfirmed_admin = web.client.post(
        "/api/admin/invites",
        json={"address": "127.0.0.1:7443", "role": "admin", "ttl_hours": 1},
        headers=headers,
    )
    long_admin = web.client.post(
        "/api/admin/invites",
        json={"role": "admin", "ttl_hours": 2, "confirm_admin": True},
        headers=headers,
    )
    admin_invite = web.client.post(
        "/api/admin/invites",
        json={"role": "admin", "ttl_hours": 1, "confirm_admin": True, "label": "deputy"},
        headers=headers,
    )
    uncovered = web.client.post(
        "/api/admin/invites", json={"address": "10.9.9.9:7443", "node": True}, headers=headers
    )

    assert created.status_code == 200
    code = InviteCode.decode(created.json()["invite"])
    assert code.scope == InviteScope(node=True, role=OperatorRole.OPERATOR)
    assert unconfirmed_admin.status_code == 400 and "confirm" in unconfirmed_admin.json()["detail"]
    assert long_admin.status_code == 400 and "1 hour" in long_admin.json()["detail"]
    assert admin_invite.status_code == 200
    assert InviteCode.decode(admin_invite.json()["invite"]).address == "127.0.0.1:7443"
    assert uncovered.status_code == 400 and "does not cover" in uncovered.json()["detail"]
    listed = web.client.get("/api/admin/invites").json()
    assert sorted(item["state"] for item in listed) == ["open", "open"]
    invite_id = next(item["invite_id"] for item in listed if item["label"] == "bob")
    assert web.client.delete(f"/api/admin/invites/{invite_id}", headers=headers).status_code == 200
    states = {item["label"]: item["state"] for item in web.client.get("/api/admin/invites").json()}
    assert states == {"bob": "revoked", "deputy": "open"}

    viewer = web.credential(OperatorRole.VIEWER)
    operators = web.client.get("/api/admin/operators").json()
    me = next(item for item in operators if item["current"])
    other = next(item for item in operators if item["role"] == "viewer")
    assert (
        web.client.delete(f"/api/admin/operators/{me['operator_id']}", headers=headers).status_code
        == 400
    )
    assert (
        web.client.delete(
            f"/api/admin/operators/{other['operator_id']}", headers=headers
        ).status_code
        == 200
    )
    other_client = TestClient(web.client.app, base_url="https://testserver")
    assert (
        other_client.get("/api/overview", headers={"Authorization": f"Bearer {viewer}"}).status_code
        == 401
    )


def test_revoking_a_credential_ends_its_browser_sessions(web: Web) -> None:
    token = web.credential(OperatorRole.VIEWER)
    web.client.post("/api/session", json={"credential": token})
    operator_id = web.services.repositories.auth.authenticate_operator(
        token, web.clock.now()
    ).operator_id

    web.services.repositories.auth.revoke_operator(operator_id)

    assert web.client.get("/api/overview").status_code == 401


def test_session_expires_with_its_credential(web: Web) -> None:
    web.sign_in(OperatorRole.VIEWER)

    web.clock.offset = timedelta(days=2)

    assert web.client.get("/api/overview").status_code == 401


def test_logout(web: Web) -> None:
    csrf = web.sign_in(OperatorRole.VIEWER)

    assert (
        web.client.request("DELETE", "/api/session", headers={CSRF_HEADER: csrf}).status_code == 200
    )
    assert web.client.get("/api/overview").status_code == 401


def test_event_stream_delivers_new_events(web: Web) -> None:
    web.sign_in(OperatorRole.OPERATOR)
    web.services.experiments.create("hu-rule-smoke", web.clock.now())

    with web.client.stream("GET", "/api/events/stream?after=0") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())

    events = [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    assert any(event["type"] == "EXPERIMENT_CREATED" for event in events)


def test_static_files_and_spa_fallback(tmp_path: Path, web: Web) -> None:
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    client = TestClient(create_app(replace(web.context, static_dir=static)))

    assert client.get("/").text == "<html>app</html>"
    assert client.get("/experiments/abc").text == "<html>app</html>"
    asset = client.get("/assets/app.js")
    assert asset.text == "console.log(1)" and "immutable" in asset.headers["cache-control"]
    assert "secret" not in client.get("/../secret.txt").text
    assert "secret" not in client.get("/%2e%2e/secret.txt").text
    assert client.get("/api/overview").status_code == 401


def test_real_server_starts_and_stops(web: Web) -> None:
    import urllib.request

    running = start_web_server(replace(web.context, limiter=LoginLimiter()), "127.0.0.1:0")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{running.port}/api/session") as response:
            raise AssertionError(f"expected 401, got {response.status}")
    except urllib.error.HTTPError as error:
        assert error.code == 401
    finally:
        running.stop()
    assert not running.thread.is_alive()
