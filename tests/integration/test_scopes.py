from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tableforge.composition import Services, build_services
from tableforge.domain.auth import (
    ALL_SCOPES,
    ROLE_SCOPES,
    InviteScope,
    OperatorRole,
    Scope,
    check_invite_request,
    role_for,
)
from tableforge.domain.ids import OperatorId
from tableforge.settings import Settings
from tableforge.transport.tls import generate_self_signed
from tableforge.web.app import WebContext, create_app
from tableforge.web.views import COMMAND_SCOPES, QUERY_SCOPES


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@pytest.fixture
def services(tmp_path: Path) -> Services:
    generate_self_signed(tmp_path / "tls", ())
    return build_services(Settings(data_dir=tmp_path))


@pytest.fixture
def client(services: Services) -> Iterator[TestClient]:
    context = WebContext(services, Clock(), static_dir=None)
    with TestClient(create_app(context), base_url="https://testserver") as client:
        yield client


def credential(services: Services, scopes: frozenset[Scope]) -> str:
    return services.repositories.auth.issue_operator(
        OperatorRole.CUSTOM, "test", datetime.now(UTC), timedelta(days=1), scopes=scopes
    ).token


def call(client: TestClient, token: str, kind: str, name: str) -> int:
    headers = {"Authorization": f"Bearer {token}"}
    if kind == "query":
        return client.get(f"/api/query/{name}", headers=headers).status_code
    return client.post(f"/api/command/{name}", json={}, headers=headers).status_code


def test_roles_are_scope_presets() -> None:
    viewer = {Scope.EXPERIMENT_READ, Scope.MODEL_READ}
    assert ROLE_SCOPES[OperatorRole.VIEWER] == viewer
    assert ROLE_SCOPES[OperatorRole.MODEL_RECIPIENT] == viewer | {Scope.MODEL_DOWNLOAD}
    assert {Scope.EXPERIMENT_CONTROL, Scope.PLAY_REMOTE} <= ROLE_SCOPES[OperatorRole.OPERATOR]
    assert Scope.MODEL_DOWNLOAD not in ROLE_SCOPES[OperatorRole.OPERATOR]
    assert ROLE_SCOPES[OperatorRole.ADMIN] == ALL_SCOPES
    assert role_for(frozenset(viewer)) is OperatorRole.VIEWER
    assert role_for(frozenset({Scope.MODEL_READ})) is OperatorRole.CUSTOM


@pytest.mark.parametrize(
    "kind,name,scope",
    [
        *(("query", name, scope) for name, scope in QUERY_SCOPES.items()),
        *(("command", name, scope) for name, scope in COMMAND_SCOPES.items()),
    ],
)
def test_every_query_and_command_needs_exactly_its_scope(
    services: Services, client: TestClient, kind: str, name: str, scope: Scope
) -> None:
    without = credential(services, ALL_SCOPES - {scope})
    only = credential(services, frozenset({scope}))

    assert call(client, without, kind, name) == 403
    # With the scope the request passes the permission check (it may still be invalid).
    assert call(client, only, kind, name) != 403


def test_download_without_control_and_control_without_download(services: Services) -> None:
    auth = services.repositories.auth
    now = datetime.now(UTC)
    reader = auth.authenticate_operator(
        credential(services, ROLE_SCOPES[OperatorRole.MODEL_RECIPIENT]), now
    )
    operator = auth.authenticate_operator(
        credential(services, ROLE_SCOPES[OperatorRole.OPERATOR]), now
    )

    assert reader.has(Scope.MODEL_DOWNLOAD) and reader.has(Scope.MODEL_READ)
    assert not reader.has(Scope.EXPERIMENT_CONTROL) and not reader.has(Scope.PLAY_REMOTE)
    assert operator.has(Scope.EXPERIMENT_CONTROL) and operator.has(Scope.PLAY_REMOTE)
    assert not operator.has(Scope.MODEL_DOWNLOAD)
    assert reader.role is OperatorRole.MODEL_RECIPIENT and operator.role is OperatorRole.OPERATOR


def test_credentials_from_before_scopes_keep_their_permissions(
    services: Services, tmp_path: Path
) -> None:
    auth = services.repositories.auth
    token = credential(services, ROLE_SCOPES[OperatorRole.VIEWER])
    identity = auth.authenticate_operator(token, datetime.now(UTC))
    # A row written before migration 6 has an empty scopes column.
    with sqlite3.connect(tmp_path / "tableforge.db") as connection:
        connection.execute(
            "UPDATE operators SET scopes = '', role = 'operator' WHERE operator_id = ?",
            (str(identity.operator_id),),
        )

    upgraded = auth.authenticate_operator(token, datetime.now(UTC))

    assert upgraded.role is OperatorRole.OPERATOR
    assert upgraded.scopes == ROLE_SCOPES[OperatorRole.OPERATOR]


def test_admin_changes_scopes_and_the_last_admin_is_kept(services: Services) -> None:
    auth = services.repositories.auth
    now = datetime.now(UTC)
    admin = auth.authenticate_operator(credential(services, ALL_SCOPES), now)
    member = auth.authenticate_operator(credential(services, ROLE_SCOPES[OperatorRole.VIEWER]), now)

    changed = auth.set_operator_scopes(
        member.operator_id, frozenset({Scope.MODEL_READ, Scope.MODEL_DOWNLOAD}), now
    )

    assert changed.role is OperatorRole.CUSTOM
    assert auth.list_operators()[1].scopes == {Scope.MODEL_READ, Scope.MODEL_DOWNLOAD}
    with pytest.raises(ValueError, match="at least one"):
        auth.set_operator_scopes(admin.operator_id, ROLE_SCOPES[OperatorRole.VIEWER], now)
    with pytest.raises(ValueError, match="at least one"):
        auth.revoke_operator(admin.operator_id)
    with pytest.raises(KeyError):
        auth.set_operator_scopes(OperatorId("missing"), frozenset({Scope.MODEL_READ}), now)
    events = services.repositories.events.recent_events(100)
    assert any(event.event_type == "OPERATOR_SCOPES_CHANGED" for event in events)


def test_invites_with_explicit_scopes(services: Services) -> None:
    auth = services.repositories.auth
    now = datetime.now(UTC)
    scope = InviteScope(
        node=False, role=None, custom_scopes=frozenset({Scope.MODEL_READ, Scope.MODEL_DOWNLOAD})
    )

    assert InviteScope.decode(scope.encode()) == scope
    assert InviteScope.decode("operator").scopes == ROLE_SCOPES[OperatorRole.OPERATOR]
    _, secret = auth.create_invite(scope, now, timedelta(hours=1), "reader")
    redemption = auth.redeem_invite(secret, None, now, timedelta(days=1), timedelta(days=1))
    assert redemption.operator is not None
    identity = auth.authenticate_operator(redemption.operator.token, now)
    assert identity.scopes == {Scope.MODEL_READ, Scope.MODEL_DOWNLOAD}
    for bad in ("scope:model:fly", "custom", "viewer,operator"):
        with pytest.raises(ValueError):
            InviteScope.decode(bad)
    admin_like = InviteScope(node=False, role=None, custom_scopes=frozenset({Scope.ADMIN_MANAGE}))
    with pytest.raises(ValueError):
        check_invite_request(admin_like, timedelta(hours=2), confirm_admin=True)


def test_admin_api_sets_scopes_and_lists_them(services: Services, client: TestClient) -> None:
    admin = credential(services, ALL_SCOPES)
    target = services.repositories.auth.authenticate_operator(
        credential(services, ROLE_SCOPES[OperatorRole.VIEWER]), datetime.now(UTC)
    )
    headers = {"Authorization": f"Bearer {admin}"}

    response = client.post(
        "/api/command/set_operator_scopes",
        json={"operator_id": str(target.operator_id), "role": "model_recipient"},
        headers=headers,
    )
    listed = client.get("/api/query/operators", headers=headers).json()
    catalog = client.get("/api/query/scopes", headers=headers).json()
    session = client.get("/api/session", headers=headers).json()
    bad = client.post(
        "/api/command/set_operator_scopes",
        json={"operator_id": str(target.operator_id), "scopes": ["model:fly"]},
        headers=headers,
    )

    assert response.status_code == 200 and response.json()["role"] == "model_recipient"
    assert (
        "model:download"
        in next(item for item in listed if item["operator_id"] == str(target.operator_id))["scopes"]
    )
    assert set(catalog["scopes"]) == {scope.value for scope in Scope}
    assert "model_recipient" in catalog["roles"]
    assert set(session["scopes"]) == {scope.value for scope in Scope}
    assert bad.status_code == 400
