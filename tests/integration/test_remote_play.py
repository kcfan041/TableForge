from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("torch")

from fastapi.testclient import TestClient  # noqa: E402

from tableforge.composition import Services, build_services  # noqa: E402
from tableforge.coordinator.models import library  # noqa: E402
from tableforge.domain.auth import ROLE_SCOPES, OperatorRole, Scope  # noqa: E402
from tableforge.learning.nfsp import NfspAgent, NfspConfig  # noqa: E402
from tableforge.settings import Settings  # noqa: E402
from tableforge.transport.tls import generate_self_signed  # noqa: E402
from tableforge.web.app import WebContext, create_app  # noqa: E402


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@pytest.fixture
def services(tmp_path: Path) -> Services:
    generate_self_signed(tmp_path / "tls", ())
    services = build_services(Settings(data_dir=tmp_path))
    agent = NfspAgent(
        NfspConfig(hidden_size=16, batch_size=16, observation_schema="poker-hunlhe-vector-v2")
    )
    library(services).add("tiny-pko", agent.to_bundle(), "nfsp", "6max-pko-10bb")
    return services


@pytest.fixture
def client(services: Services) -> Iterator[TestClient]:
    with TestClient(
        create_app(WebContext(services, Clock(), static_dir=None)), base_url="https://testserver"
    ) as client:
        yield client


def token(services: Services, scopes: frozenset[Scope]) -> dict[str, str]:
    issued = services.repositories.auth.issue_operator(
        OperatorRole.CUSTOM, "p", datetime.now(UTC), timedelta(days=1), scopes=scopes
    )
    return {"Authorization": f"Bearer {issued.token}"}


SEATS = [
    {"kind": "human"},
    {"kind": "model", "name": "tiny-pko"},
    {"kind": "rule", "name": "call"},
    {"kind": "model", "name": "tiny-pko", "mode": "best_response"},
    {"kind": "rule", "name": "tight"},
    {"kind": "rule", "name": "aggro"},
]


def test_play_a_game_against_a_model_on_the_cluster_host(
    services: Services, client: TestClient
) -> None:
    headers = token(services, ROLE_SCOPES[OperatorRole.OPERATOR])

    started = client.post(
        "/api/command/play_start", json={"seats": SEATS, "seed": 4}, headers=headers
    )
    assert started.status_code == 200, started.text
    view = started.json()
    session_id = view["session_id"]
    assert view["players"] == 6 and view["labels"][1] == "tiny-pko"
    for _ in range(3_000):
        if view["complete"]:
            break
        turn = view["turn"]
        assert turn is not None and view["hand"]["current_player"] == 0
        # Only the person's own two cards are sent while the hand is running.
        assert len(view["hand"]["hole_cards"]) == 2
        assert all(len(card) == 2 for card in view["hand"]["hole_cards"])
        response = client.post(
            "/api/command/play_act",
            json={
                "session_id": session_id,
                "action": "all_in" if turn["max_raise_to"] else "check_call",
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        view = response.json()
    assert view["complete"]

    replay = client.post(
        "/api/command/play_replay", json={"session_id": session_id}, headers=headers
    ).json()
    history = client.post(
        "/api/command/play_history", json={"session_id": session_id}, headers=headers
    ).json()
    model_moves = [
        entry
        for hand in replay["hands"]
        for entry in hand["actions"]
        if entry["player"] == 1 and entry["explanation"]
    ]
    assert replay["labels"][0] == "You" and len(replay["hands"]) == view["hands_played"]
    assert model_moves, "the replay keeps the model's explanations"
    assert "Hand #1 " in history["text"]


def test_hints_and_the_tournament_come_from_the_model(
    services: Services, client: TestClient
) -> None:
    headers = token(services, frozenset({Scope.PLAY_REMOTE}))
    seats = [{"kind": "human"}, {"kind": "model", "name": "tiny-pko"}]

    heads_up = client.post(
        "/api/command/play_start",
        json={"seats": seats, "tournament": {"players": 2, "starting_stack_bb": 25}, "hints": True},
        headers=headers,
    ).json()
    wrong_size = client.post("/api/command/play_start", json={"seats": seats}, headers=headers)

    assert heads_up["players"] == 2 and heads_up["config"]["starting_stack_bb"] == 25
    if heads_up["turn"] is not None:
        assert set(heads_up["hint"]["probabilities"]) == {"fold", "check_call", "raise", "all_in"}
    assert wrong_size.status_code == 400 and "6 seats" in wrong_size.json()["detail"]


def test_games_are_private_and_need_play_remote(services: Services, client: TestClient) -> None:
    alice = token(services, frozenset({Scope.PLAY_REMOTE}))
    bob = token(services, frozenset({Scope.PLAY_REMOTE}))
    viewer = token(services, ROLE_SCOPES[OperatorRole.MODEL_RECIPIENT])
    started = client.post(
        "/api/command/play_start", json={"seats": SEATS, "seed": 1}, headers=alice
    ).json()

    peek = client.post(
        "/api/command/play_state", json={"session_id": started["session_id"]}, headers=bob
    )
    denied = client.post("/api/command/play_start", json={"seats": SEATS}, headers=viewer)
    missing = client.post(
        "/api/command/play_start",
        json={"seats": [{"kind": "human"}, {"kind": "model", "name": "nope"}, *SEATS[2:]]},
        headers=alice,
    )

    assert peek.status_code == 404
    assert denied.status_code == 403
    assert missing.status_code == 400 and "no such model" in missing.json()["detail"]
    listed = client.post("/api/command/play_list", json={}, headers=alice).json()
    assert [item["session_id"] for item in listed] == [started["session_id"]]
