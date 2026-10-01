"""v3 training (S2 bet sizes, S3 many structures, T4 up to 9 players) on the platform."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("torch")

from tableforge.cli.main import _learning_executors, _rule_executors  # noqa: E402
from tableforge.composition import build_services  # noqa: E402
from tableforge.domain.experiments import ExperimentStatus  # noqa: E402
from tableforge.domain.learning import ACTOR_PAYLOAD_TYPE, NfspActorPayload  # noqa: E402
from tableforge.learning.experience import ExperienceShard  # noqa: E402
from tableforge.learning.nfsp import NfspAgent, NfspConfig  # noqa: E402
from tableforge.learning.tournament_training import play_training_tournament  # noqa: E402
from tableforge.poker.tournament_sampler import TournamentSampler  # noqa: E402
from tableforge.settings import Settings  # noqa: E402
from tableforge.worker.loop import WorkerLoop  # noqa: E402
from tableforge.worker.runtime import WorkerRuntime  # noqa: E402
from tableforge.worker.simulation import SimulationExecutor  # noqa: E402
from tests.integration.test_experiment_workflow import CapturingLogger, capability  # noqa: E402

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class Clock:
    def now(self) -> datetime:
        return NOW


def v3_agent(seed: int = 0) -> NfspAgent:
    return NfspAgent(
        NfspConfig(
            hidden_size=32,
            batch_size=16,
            observation_schema="poker-nlhe-vector-v3",
            action_schema="nlhe-discrete-v2",
            seed=seed,
        )
    )


def test_v3_agent_plays_every_table_size_and_records_v2_actions() -> None:
    agent = v3_agent()
    sampler = TournamentSampler(players=(2, 3, 4, 5, 6, 7, 8, 9))
    transitions = []
    sizes = set()
    for seed in range(40):
        episode = play_training_tournament(agent, sampler, seed, transitions.append, 0.5)
        sizes.add(len(episode.lineup))
    assert sizes == set(range(2, 10))
    assert transitions and all(len(item.mask) == 10 for item in transitions)
    chosen = {item.action for item in transitions}
    assert chosen - {0, 1, 2}, "raise sizes are used"


def test_v3_checkpoints_round_trip_and_v1_checkpoints_keep_loading() -> None:
    agent = v3_agent(3)
    bundle = agent.to_bundle()
    restored = NfspAgent.from_bundle(bundle)
    old = NfspAgent(NfspConfig(hidden_size=16, batch_size=16)).to_bundle()

    assert restored.actions.schema == "nlhe-discrete-v2" and restored.actions.count == 10
    assert restored.encoder.schema == "poker-nlhe-vector-v3"
    assert NfspAgent.from_bundle(old).actions.schema == "nlhe-discrete-v1"


def test_mixed_structure_training_runs_on_the_platform(tmp_path: Path) -> None:
    services = build_services(Settings(data_dir=tmp_path))
    experiments = services.experiments
    created = experiments.create("mixed-pko-v3-smoke", NOW)
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

    assert experiments.experiments.get(created.experiment_id).status is ExperimentStatus.COMPLETED
    jobs = services.coordinator.jobs.list_jobs(created.experiment_id)
    actor_job = next(job for job in jobs if job.spec.payload_type == ACTOR_PAYLOAD_TYPE)
    actor = NfspActorPayload.from_bytes(actor_job.spec.payload)
    assert actor.sampler_json
    output = services.coordinator.job_outputs(actor_job.spec.job_id)[0]
    shard = ExperienceShard.from_bytes(
        services.coordinator.read_artifact(str(output.artifact.descriptor.artifact_id))
    )
    assert shard.action_schema == "nlhe-discrete-v2"
    assert shard.observation_schema == "poker-nlhe-vector-v3"
    curve = experiments.training_curve(created.experiment_id)
    assert [point["round"] for point in curve] == [1, 2]
    checkpoint = services.repositories.checkpoints.list_checkpoints()[-1]
    content = services.coordinator.read_artifact(str(checkpoint.artifact_id))
    assert NfspAgent.from_bundle(content).actions.schema == "nlhe-discrete-v2"


def test_v3_settings_are_checked(tmp_path: Path) -> None:
    experiments = build_services(Settings(data_dir=tmp_path)).experiments

    with pytest.raises(ValueError, match="v3"):
        experiments.create("6max-pko-10bb-smoke", NOW, {"sampler": {"players": [6]}})
    with pytest.raises(ValueError, match="seats"):
        experiments.create(
            "6max-pko-10bb-smoke",
            NOW,
            {"tournament": {"players": 9, "starting_stack_bb": 10}},
        )
    nine = experiments.create(
        "mixed-pko-v3-smoke", NOW, {"tournament": {"players": 9, "starting_stack_bb": 30}}
    )
    custom = experiments.create(
        "mixed-pko-v3-smoke", NOW, {"sampler": {"players": [8, 9], "starting_stack_bb": [20, 40]}}
    )
    assert nine.config.players == 9
    assert TournamentSampler.from_json(custom.config.sampler_json).players == (8, 9)
    with pytest.raises(ValueError, match="unknown sampler settings"):
        experiments.create("mixed-pko-v3-smoke", NOW, {"sampler": {"speed": "turbo"}})
