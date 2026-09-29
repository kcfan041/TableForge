from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("torch")

from tableforge.cli.main import _learning_executors, _rule_executors  # noqa: E402
from tableforge.composition import build_services  # noqa: E402
from tableforge.coordinator.models import library  # noqa: E402
from tableforge.domain.auth import OperatorRole  # noqa: E402
from tableforge.learning.nfsp import NfspAgent, NfspConfig  # noqa: E402
from tableforge.learning.strategy import hand_classes, spots  # noqa: E402
from tableforge.settings import Settings  # noqa: E402
from tableforge.web.views import ClusterViews, ViewError  # noqa: E402
from tableforge.worker.loop import WorkerLoop  # noqa: E402
from tableforge.worker.runtime import WorkerRuntime  # noqa: E402
from tableforge.worker.simulation import SimulationExecutor  # noqa: E402
from tests.integration.test_experiment_workflow import CapturingLogger, capability  # noqa: E402

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class Clock:
    def now(self) -> datetime:
        return NOW


@pytest.fixture
def cluster(tmp_path: Path) -> tuple[object, ClusterViews, object]:
    services = build_services(Settings(data_dir=tmp_path))
    agent = NfspAgent(
        NfspConfig(hidden_size=16, batch_size=16, observation_schema="poker-hunlhe-vector-v2")
    )
    library(services).add("tiny-pko", agent.to_bundle(), "nfsp", "6max-pko-10bb")
    views = ClusterViews(services, Clock())
    admin = services.repositories.auth.authenticate_operator(
        services.repositories.auth.issue_operator(
            OperatorRole.ADMIN, "a", NOW, timedelta(days=1)
        ).token,
        NOW,
    )
    return services, views, admin


def run_worker(services: object) -> None:
    worker_capability = capability()
    runtime = WorkerRuntime(
        node_id=worker_capability.node_id,
        capability=replace(
            worker_capability,
            enabled_roles=("simulation", "evaluation", "nfsp_learner", "nfsp_actor"),
            logical_cpu_cores=8,
            total_memory_bytes=32 * 1024**3,
            available_memory_bytes=32 * 1024**3,
        ),
        coordinator=services.coordinator,  # type: ignore[attr-defined]
        clock=Clock(),
        lease_duration=timedelta(minutes=5),
        simulation_executor=SimulationExecutor(),
        extra_executors={
            **_rule_executors(),
            **_learning_executors(services.coordinator),  # type: ignore[attr-defined,arg-type]
        },
    )
    WorkerLoop(runtime, CapturingLogger(), idle_seconds=0).run(max_jobs=5)


def test_replay_records_every_hand_with_explanations(
    cluster: tuple[object, ClusterViews, object],
) -> None:
    services, views, admin = cluster

    requested = views.command(
        "request_replay",
        {"model_id": "tiny-pko", "seed": 7, "seat": 2, "opponents": "mixed"},
        admin,
    )
    again = views.command(
        "request_replay",
        {"model_id": "tiny-pko", "seed": 7, "seat": 2, "opponents": "mixed"},
        admin,
    )
    pending = views.query("analysis", requested["job_id"], admin)
    run_worker(services)
    done = views.query("analysis", requested["job_id"], admin)

    assert again == requested
    assert pending["status"] != "succeeded" and pending["result"] is None
    assert done["status"] == "succeeded" and done["kind"] == "replay"
    replay = done["result"]
    assert replay["labels"] == ["tight", "call", "tiny-pko", "random", "pushfold", "call"]
    assert sorted(replay["result"]["places"]) == [1, 2, 3, 4, 5, 6]
    hands = replay["hands"]
    assert hands and all(len(hand["hole_cards"]) == len(hand["players"]) for hand in hands)
    model_actions = [
        action for hand in hands for action in hand["actions"] if action["player"] == 2
    ]
    other_actions = [
        action for hand in hands for action in hand["actions"] if action["player"] != 2
    ]
    assert model_actions and all(action["explanation"] for action in model_actions)
    assert all(action["explanation"] is None for action in other_actions)
    first = model_actions[0]["explanation"]
    assert abs(sum(first["probabilities"].values()) - 1) < 1e-6
    assert first["details"][0]["unit"] == "buy-in"
    # Stacks carry over between hands.
    for before, after in zip(hands, hands[1:], strict=False):
        carried = dict(zip(before["players"], before["stacks_after"], strict=True))
        assert all(
            carried[player] == stack
            for player, stack in zip(after["players"], after["stacks_before"], strict=True)
        )


def test_strategy_table_covers_every_spot_and_hand(
    cluster: tuple[object, ClusterViews, object],
) -> None:
    services, views, admin = cluster

    requested = views.command(
        "request_strategy", {"model_id": "tiny-pko", "mode": "best_response"}, admin
    )
    run_worker(services)
    done = views.query("analysis", requested["job_id"], admin)

    assert done["status"] == "succeeded" and done["kind"] == "strategy"
    table = done["result"]
    assert table["mode"] == "best_response"
    assert table["hands"] == hand_classes()
    open_spots = sum(1 for scenario, _, _ in spots() if scenario == "open")
    call_spots = len(spots()) - open_spots
    stacks = len(table["stacks"])
    assert len(table["tables"]) == stacks * (open_spots + call_spots * 3)
    btn = table["tables"]["open|BTN||10|1"]
    assert set(btn) == set(hand_classes())
    for values in btn.values():
        probabilities = values[:4]
        assert abs(sum(probabilities) - 1) < 1e-3
        assert max(probabilities) == 1.0
    call = table["tables"]["call|BB|BTN|10|3"]
    assert all(values[2] == 0 and values[3] == 0 for values in call.values())


def test_analysis_requests_are_validated(cluster: tuple[object, ClusterViews, object]) -> None:
    _, views, admin = cluster
    for arguments in (
        {"model_id": "missing"},
        {"model_id": "tiny-pko", "mode": "nonsense"},
        {"model_id": "tiny-pko", "seat": 9},
        {"model_id": "tiny-pko", "opponents": "aliens"},
    ):
        with pytest.raises(ViewError):
            views.command("request_replay", arguments, admin)
    with pytest.raises(ViewError):
        views.query("analysis", "no-such-job", admin)
