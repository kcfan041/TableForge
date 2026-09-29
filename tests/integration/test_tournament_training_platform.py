from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("torch")

from tableforge.cli.main import _learning_executors, _rule_executors  # noqa: E402
from tableforge.composition import build_services  # noqa: E402
from tableforge.domain.auth import OperatorRole  # noqa: E402
from tableforge.domain.experiments import ExperimentStatus  # noqa: E402
from tableforge.domain.learning import (  # noqa: E402
    ACTOR_PAYLOAD_TYPE,
    MATCH_PAYLOAD_TYPE,
    NfspActorPayload,
    NfspInitPayload,
    NfspMatchPayload,
)
from tableforge.learning.explain import ACTIONS, RuleBotExplainer  # noqa: E402
from tableforge.learning.nfsp import NfspAgent, NfspConfig  # noqa: E402
from tableforge.learning.nfsp_explain import NfspExplainer  # noqa: E402
from tableforge.poker.table import TableHand  # noqa: E402
from tableforge.poker.tournament import BountyConfig, Tournament, preset_config  # noqa: E402
from tableforge.settings import Settings  # noqa: E402
from tableforge.web.views import ClusterViews, ViewError  # noqa: E402
from tableforge.worker.loop import WorkerLoop  # noqa: E402
from tableforge.worker.runtime import WorkerRuntime  # noqa: E402
from tableforge.worker.simulation import SimulationExecutor  # noqa: E402
from tableforge.worker.tournament import rule_bot  # noqa: E402
from tests.integration.test_experiment_workflow import CapturingLogger, capability  # noqa: E402

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class Clock:
    def now(self) -> datetime:
        return NOW


def test_payloads_without_tournament_fields_keep_their_bytes() -> None:
    sha = "a" * 64
    actor = NfspActorPayload("1.0", "artifact", sha, 100, 1, 1)
    match = NfspMatchPayload("1.0", "c", sha, "b", sha, 0, 10, 10, 10, 0.0)
    init = NfspInitPayload("1.0", 0)

    assert set(json.loads(actor.to_bytes())) == {
        "actor_seed",
        "checkpoint_artifact_id",
        "checkpoint_sha256",
        "hands",
        "population_artifact_id",
        "population_sha256",
        "schema_version",
        "seed_start",
    }
    assert "tournament_json" not in json.loads(match.to_bytes())
    assert set(json.loads(init.to_bytes())) == {
        "agent_seed",
        "batch_size",
        "hidden_size",
        "schema_version",
    }
    config = preset_config(10, bounty=BountyConfig()).to_json()
    tournament_actor = replace(actor, tournament_json=config, self_play_share=0.75)
    assert NfspActorPayload.from_bytes(tournament_actor.to_bytes()) == tournament_actor


def test_tournament_training_runs_on_the_platform(tmp_path: Path) -> None:
    services = build_services(Settings(data_dir=tmp_path))
    experiments = services.experiments
    created = experiments.create("6max-pko-10bb-smoke", NOW)
    experiments.start(created.experiment_id, NOW)
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
        coordinator=services.coordinator,
        clock=Clock(),
        lease_duration=timedelta(minutes=5),
        simulation_executor=SimulationExecutor(),
        extra_executors={
            **_rule_executors(),
            **_learning_executors(services.coordinator),  # type: ignore[arg-type]
        },
    )

    WorkerLoop(
        runtime,
        CapturingLogger(),
        idle_seconds=0,
        on_idle=lambda: experiments.reconcile_all(NOW) > 0,
    ).run(max_jobs=40)
    experiments.reconcile_all(NOW)

    finished = experiments.experiments.get(created.experiment_id)
    assert finished.status is ExperimentStatus.COMPLETED
    jobs = services.coordinator.jobs.list_jobs(created.experiment_id)
    actor = next(job for job in jobs if job.spec.payload_type == ACTOR_PAYLOAD_TYPE)
    assert NfspActorPayload.from_bytes(actor.spec.payload).tournament_json
    matches = [job for job in jobs if job.spec.payload_type == MATCH_PAYLOAD_TYPE]
    seeds = {NfspMatchPayload.from_bytes(job.spec.payload).seed_start for job in matches}
    assert len(matches) == 2 and len(seeds) == 1
    views = ClusterViews(services, Clock())
    admin = services.repositories.auth.authenticate_operator(
        services.repositories.auth.issue_operator(
            OperatorRole.ADMIN, "a", NOW, timedelta(days=1)
        ).token,
        NOW,
    )
    saved = views.command(
        "save_model",
        {"experiment_id": str(created.experiment_id), "model_id": "pko-smoke", "description": "x"},
        admin,
    )
    assert saved["game"] == "6max-pko-10bb-smoke"
    assert [mode["key"] for mode in saved["play_modes"]] == ["average", "best_response"]
    listed = views.query("models", "", admin)
    assert [item["model_id"] for item in listed] == ["pko-smoke"]
    with pytest.raises(ViewError):
        views.command(
            "save_model",
            {"experiment_id": str(created.experiment_id), "model_id": "pko-smoke"},
            admin,
        )
    assert views.command("remove_model", {"model_id": "pko-smoke"}, admin) == {"ok": True}
    assert views.query("models", "", admin) == []

    curve = experiments.training_curve(created.experiment_id)
    assert [point["round"] for point in curve] == [1, 2]
    for point in curve:
        assert point["unit"] == "roi_percent"
        assert set(point["benchmark"]) == {"average", "best_response"}  # type: ignore[arg-type]
        tournament = point["tournament"]
        assert isinstance(tournament, dict)
        assert set(tournament) == {"average", "best_response", "baseline"}
        assert tournament["average"]["tournaments"] == 60
        assert tournament["average"]["bounty_ev"]["mean"] > 0


def explainers() -> list[tuple[object, str]]:
    agent = NfspAgent(
        NfspConfig(hidden_size=16, batch_size=16, observation_schema="poker-hunlhe-vector-v2")
    )
    nfsp = NfspExplainer(agent.to_bundle())
    return [
        (nfsp, "average"),
        (nfsp, "best_response"),
        (RuleBotExplainer(rule_bot("tight", 0)), "rule"),
    ]


def test_every_explainer_reports_the_same_format() -> None:
    config = preset_config(10, bounty=BountyConfig())
    tournament = Tournament(config, 3)
    hand = TableHand(tournament.stacks, 0, 10, 20, seed=5, big_blind_ante=20)
    seat = hand.betting.current_actor
    assert seat is not None
    observation = hand.observe(seat)
    legal = hand.legal_actions(seat)
    for explainer, mode in explainers():
        explanation = explainer.explain(observation, legal, mode)  # type: ignore[attr-defined]
        data = explanation.as_dict()
        assert set(data["probabilities"]) == set(ACTIONS)
        assert abs(sum(data["probabilities"].values()) - 1) < 1e-6
        if mode == "best_response":
            assert max(data["probabilities"].values()) == 1.0
        if mode != "rule":
            assert data["details"] and data["details"][0]["per_action"]
    assert [mode.key for mode in explainers()[0][0].play_modes] == ["average", "best_response"]  # type: ignore[attr-defined]
