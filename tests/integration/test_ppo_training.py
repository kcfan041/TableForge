"""PPO (A1) on the platform: the round pipeline, the model library and analysis tools."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("torch")

from tableforge.cli.main import _learning_executors, _rule_executors  # noqa: E402
from tableforge.composition import build_services  # noqa: E402
from tableforge.coordinator.nfsp_iterative import agent_id_for  # noqa: E402
from tableforge.domain.experiments import ExperimentStatus  # noqa: E402
from tableforge.domain.learning import (  # noqa: E402
    PPO_ACTOR_PAYLOAD_TYPE,
    PPO_INIT_PAYLOAD_TYPE,
    PPO_LEARNER_PAYLOAD_TYPE,
    PPO_MATCH_PAYLOAD_TYPE,
)
from tableforge.learning.algorithms import algorithm  # noqa: E402
from tableforge.learning.experience import ExperienceShard  # noqa: E402
from tableforge.learning.ppo import PpoAgent  # noqa: E402
from tableforge.settings import Settings  # noqa: E402
from tableforge.worker.loop import WorkerLoop  # noqa: E402
from tableforge.worker.runtime import WorkerRuntime  # noqa: E402
from tableforge.worker.simulation import SimulationExecutor  # noqa: E402
from tests.integration.test_experiment_workflow import CapturingLogger, capability  # noqa: E402

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class Clock:
    def now(self) -> datetime:
        return NOW


def run_experiment(tmp_path: Path, preset: str, settings: dict[str, object] | None = None):  # type: ignore[no-untyped-def]
    services = build_services(Settings(data_dir=tmp_path))
    experiments = services.experiments
    created = experiments.create(preset, NOW, settings)
    experiments.start(created.experiment_id, NOW)
    worker = capability()
    runtime = WorkerRuntime(
        node_id=worker.node_id,
        capability=replace(
            worker,
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
    return services, created


def test_ppo_smoke_trains_on_the_platform(tmp_path: Path) -> None:
    services, created = run_experiment(tmp_path, "6max-pko-10bb-ppo-smoke")
    experiments = services.experiments

    assert experiments.experiments.get(created.experiment_id).status is ExperimentStatus.COMPLETED
    jobs = services.coordinator.jobs.list_jobs(created.experiment_id)
    types = [job.spec.payload_type for job in jobs]
    assert types.count(PPO_INIT_PAYLOAD_TYPE) == 1
    assert types.count(PPO_ACTOR_PAYLOAD_TYPE) == 4
    assert types.count(PPO_LEARNER_PAYLOAD_TYPE) == 2
    assert types.count(PPO_MATCH_PAYLOAD_TYPE) == 2
    actor = next(job for job in jobs if job.spec.payload_type == PPO_ACTOR_PAYLOAD_TYPE)
    output = services.coordinator.job_outputs(actor.spec.job_id)[0]
    shard = ExperienceShard.from_bytes(
        services.coordinator.read_artifact(str(output.artifact.descriptor.artifact_id))
    )
    assert shard.transitions and not shard.policy_samples
    assert shard.transitions[-1].next_state is None

    curve = experiments.training_curve(created.experiment_id)
    assert [point["round"] for point in curve] == [1, 2]
    assert set(curve[-1]["benchmark"]) == {"sample", "greedy"}  # type: ignore[call-overload]
    assert curve[-1]["head_to_head"]["unit"] == "roi_points"  # type: ignore[index]

    experiment = experiments.experiments.get(created.experiment_id)
    assert str(agent_id_for(experiment)).startswith("ppo:")
    active = services.repositories.checkpoints.active(agent_id_for(experiment))
    assert active is not None and active.round_index >= 1
    content = services.coordinator.read_artifact(str(active.artifact_id))
    agent = PpoAgent.from_bundle(content)
    assert agent.train_steps > 0

    from tableforge.coordinator.models import library, save_experiment_model

    info = save_experiment_model(services, str(created.experiment_id), "ppo-smoke", "test")
    assert info.algorithm == "ppo"
    assert library(services).checkpoint("ppo-smoke") == content


def test_ppo_models_explain_and_play_in_both_modes() -> None:
    from tableforge.learning.ppo import PpoConfig
    from tableforge.poker.tournament import BountyConfig, Tournament, preset_config
    from tableforge.worker.tournament import rule_bot

    spec = algorithm("ppo")
    bundle = PpoAgent(PpoConfig(seed=1, hidden_size=16)).to_bundle()
    config = preset_config(10, bounty=BountyConfig())
    tournament = Tournament(config, 3)
    started = tournament.begin_hand([rule_bot("call", seat) for seat in range(6)])
    hand = started.hand
    actor = hand.betting.current_actor
    assert actor is not None
    observation = hand.observe(actor)
    legal = hand.legal_actions(actor)
    info = started.infos[actor]

    explainer = spec.explainer(bundle)
    explainer.begin_hand(info)
    sample = explainer.explain(observation, legal, "sample")
    greedy = explainer.explain(observation, legal, "greedy")
    assert sum(sample.probabilities.values()) == pytest.approx(1.0)
    assert max(greedy.probabilities.values()) == pytest.approx(1.0)
    assert sample.details[0].value is not None
    assert [mode.key for mode in explainer.play_modes] == ["sample", "greedy"]
    for mode in ("sample", "greedy"):
        player = spec.player(bundle, spec.mode(mode))()
        player.begin_hand(info)  # type: ignore[attr-defined]
        assert player.act(observation, legal) is not None
    with pytest.raises(ValueError, match="play mode"):
        spec.mode("average")


def test_ppo_settings_are_checked(tmp_path: Path) -> None:
    experiments = build_services(Settings(data_dir=tmp_path)).experiments

    with pytest.raises(ValueError, match="between"):
        experiments.create("6max-pko-10bb-ppo-smoke", NOW, {"clip": 2})
    created = experiments.create(
        "6max-pko-10bb-ppo-smoke", NOW, {"entropy_coef": 0.02, "gae_lambda": 0.9}
    )
    assert created.config.algorithm == "ppo"
    assert (created.config.entropy_coef, created.config.gae_lambda) == (0.02, 0.9)
