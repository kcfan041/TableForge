from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("torch")

from fastapi.testclient import TestClient  # noqa: E402

from tableforge.learning.nfsp import NfspAgent, NfspConfig  # noqa: E402
from tableforge.models import ModelLibrary  # noqa: E402
from tableforge.web.local import (  # noqa: E402
    TOKEN_HEADER,
    LocalUiContext,
    WorkerManager,
    create_local_app,
)


@pytest.fixture
def panel(tmp_path: Path) -> Iterator[tuple[TestClient, dict[str, str], Path]]:
    models = tmp_path / "downloaded-models"
    agent = NfspAgent(
        NfspConfig(hidden_size=16, batch_size=16, observation_schema="poker-hunlhe-vector-v2")
    )
    ModelLibrary(models).add("tiny-pko", agent.to_bundle(), "nfsp", "6max-pko-10bb")
    context = LocalUiContext(
        data_dir=tmp_path / "node",
        operator_dir=tmp_path / "operators",
        manager=WorkerManager(tmp_path / "node", [sys.executable, "-c", "pass"], cpu_groups=[]),
        node_id_for=lambda _: "node-test",
        static_dir=None,
        models_dir=models,
    )
    with TestClient(create_local_app(context), base_url="http://127.0.0.1:8765") as client:
        yield client, {TOKEN_HEADER: context.token}, models


def test_play_against_a_downloaded_model_without_a_cluster(
    panel: tuple[TestClient, dict[str, str], Path],
) -> None:
    client, headers, _ = panel
    seats = [{"kind": "human"}, {"kind": "model", "name": "tiny-pko"}]
    listed = client.get("/api/local/models", headers=headers).json()

    started = client.post(
        "/api/local/play/play_start",
        json={"seats": seats, "tournament": {"players": 2}, "seed": 9, "hints": True},
        headers=headers,
    )
    assert started.status_code == 200, started.text
    view = started.json()
    for _ in range(3_000):
        if view["complete"]:
            break
        assert view["turn"] is not None and "hint" in view
        view = client.post(
            "/api/local/play/play_act",
            json={"session_id": view["session_id"], "action": "check_call"},
            headers=headers,
        ).json()
    replay = client.post(
        "/api/local/play/play_replay", json={"session_id": view["session_id"]}, headers=headers
    ).json()

    assert [item["model_id"] for item in listed] == ["tiny-pko"]
    assert view["complete"] and replay["labels"] == ["You", "tiny-pko"]
    assert client.post("/api/local/play/fly", json={}, headers=headers).status_code == 404


def test_local_models_can_be_removed_and_download_needs_access(
    panel: tuple[TestClient, dict[str, str], Path],
) -> None:
    client, headers, models = panel

    no_access = client.post("/api/local/models/download", json={"model_id": "x"}, headers=headers)
    removed = client.delete("/api/local/models/tiny-pko", headers=headers)
    missing = client.delete("/api/local/models/tiny-pko", headers=headers)

    assert no_access.status_code == 403
    assert removed.status_code == 200 and ModelLibrary(models).list() == []
    assert missing.status_code == 404
    assert client.get("/api/local/models").status_code == 403
