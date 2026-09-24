from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("torch")

from tableforge.capability.workloads import BUILTIN_WORKLOADS  # noqa: E402
from tableforge.coordinator.experiments import ExperimentService  # noqa: E402
from tableforge.coordinator.nfsp_iterative import agent_id_for  # noqa: E402
from tableforge.coordinator.service import CoordinatorService  # noqa: E402
from tableforge.domain.capabilities import (  # noqa: E402
    NodeCapability,
    RuntimeCapability,
    StorageCapability,
)
from tableforge.domain.checkpoints import CheckpointStatus  # noqa: E402
from tableforge.domain.experiments import (  # noqa: E402
    ExperimentConfig,
    ExperimentKind,
    ExperimentStatus,
)
from tableforge.domain.ids import NodeId, new_experiment_id  # noqa: E402
from tableforge.domain.learning import ACTOR_PAYLOAD_TYPE, MATCH_PAYLOAD_TYPE  # noqa: E402
from tableforge.infrastructure.local_artifacts import LocalArtifactStore  # noqa: E402
from tableforge.infrastructure.sqlite.checkpoints import (  # noqa: E402
    SQLiteCheckpointRepository,
)
from tableforge.infrastructure.sqlite.experiments import (  # noqa: E402
    SQLiteExperimentRepository,
)
from tableforge.infrastructure.sqlite.jobs import SQLiteJobRepository  # noqa: E402
from tableforge.infrastructure.sqlite.nodes import SQLiteNodeRepository  # noqa: E402
from tableforge.learning.registry import learning_executors  # noqa: E402
from tableforge.worker.runtime import WorkerRuntime  # noqa: E402
from tableforge.worker.simulation import SimulationExecutor  # noqa: E402

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class Clock:
    def now(self) -> datetime:
        return NOW


def build(tmp_path: Path) -> tuple[CoordinatorService, ExperimentService]:
    database = tmp_path / "tableforge.db"
    coordinator = CoordinatorService(
        jobs=SQLiteJobRepository(database),
        artifacts=LocalArtifactStore(tmp_path / "artifacts"),
        workloads=BUILTIN_WORKLOADS,
        nodes=SQLiteNodeRepository(database),
    )
    return coordinator, ExperimentService(
        SQLiteExperimentRepository(database),
        coordinator,
        SQLiteCheckpointRepository(database),
    )


def worker(coordinator: CoordinatorService, name: str) -> WorkerRuntime:
    node_id = NodeId(name)
    return WorkerRuntime(
        node_id=node_id,
        capability=NodeCapability(
            schema_version="1.0",
            node_id=node_id,
            revision=1,
            operating_system="test",
            architecture="x86_64",
            logical_cpu_cores=16,
            physical_cpu_cores=8,
            total_memory_bytes=64 * 1024**3,
            available_memory_bytes=64 * 1024**3,
            gpus=(),
            storage=(StorageCapability("data", 500 * 1024**3),),
            runtime=RuntimeCapability("3.12", ("cpu",)),
            enabled_roles=("simulation", "evaluation", "nfsp_learner", "nfsp_actor"),
            captured_at=NOW,
        ),
        coordinator=coordinator,
        clock=Clock(),
        lease_duration=timedelta(minutes=30),
        simulation_executor=SimulationExecutor(),
        extra_executors=learning_executors(coordinator.read_artifact),
    )


def config(rounds: int = 2) -> ExperimentConfig:
    return ExperimentConfig(
        schema_version="1.0",
        preset_key="test-iterative",
        preset_version="1",
        hands=200,
        hands_per_job=150,
        seed_start=11,
        kind=ExperimentKind.NFSP_ITERATIVE,
        evaluation_deals=20,
        rounds=rounds,
        actors_per_round=2,
        train_steps_per_round=60,
        max_evaluation_deals=60,
        target_ci_width=1_000_000.0,
    )


def drive(
    coordinator: CoordinatorService, experiments: ExperimentService, experiment_id: object
) -> list[str]:
    runtimes = [worker(coordinator, "node-a"), worker(coordinator, "node-b")]
    executed: list[str] = []
    for _ in range(200):
        progressed = False
        for runtime in runtimes:
            lease = runtime.run_once()
            if lease is not None:
                executed.append(f"{runtime.node_id}:{lease.job.spec.payload_type}")
                progressed = True
        current = experiments.status(experiment_id, NOW)  # type: ignore[arg-type]
        if current.status is not ExperimentStatus.RUNNING:
            return executed
        if not progressed and experiments.reconcile_all(NOW) == 0:
            raise AssertionError("pipeline stalled")
    raise AssertionError("pipeline did not finish")


def run_experiment(tmp_path: Path, rounds: int = 2) -> tuple[
    CoordinatorService, ExperimentService, object, list[str]
]:
    coordinator, experiments = build(tmp_path)
    created = SQLiteExperimentRepository(tmp_path / "tableforge.db").create(
        new_experiment_id(), config(rounds), NOW
    )
    experiments.start(created.experiment_id, NOW)
    executed = drive(coordinator, experiments, created.experiment_id)
    return coordinator, experiments, created, executed


def test_iterative_nfsp_runs_rounds_and_promotes(tmp_path: Path) -> None:
    coordinator, experiments, created, executed = run_experiment(tmp_path)
    experiment = experiments.experiments.get(created.experiment_id)  # type: ignore[attr-defined]
    checkpoints = experiments.checkpoints
    assert checkpoints is not None
    history = checkpoints.list_checkpoints(agent_id_for(experiment))

    assert experiment.status is ExperimentStatus.COMPLETED
    assert [item.round_index for item in history] == [0, 1, 2]
    assert history[1].parent_id == history[0].checkpoint_id
    assert all(item.status is not CheckpointStatus.CANDIDATE for item in history)
    assert sum(item.status is CheckpointStatus.ACTIVE for item in history) == 1
    actor_nodes = {entry.split(":")[0] for entry in executed if ACTOR_PAYLOAD_TYPE in entry}
    assert actor_nodes == {"node-a", "node-b"}
    matches = [
        job
        for job in coordinator.jobs.list_jobs(created.experiment_id)  # type: ignore[attr-defined]
        if job.spec.payload_type == MATCH_PAYLOAD_TYPE
    ]
    assert len(matches) == 2
    result = json.loads(
        coordinator.read_artifact(
            str(coordinator.job_outputs(matches[0].spec.job_id)[0].artifact.descriptor.artifact_id)
        )
    )
    assert {"head_to_head", "references"} <= set(result)


def test_iterative_run_is_reproducible(tmp_path: Path) -> None:
    first = run_experiment(tmp_path / "first")
    second = run_experiment(tmp_path / "second")

    def hashes(run: tuple[CoordinatorService, ExperimentService, object, list[str]]) -> list[str]:
        experiments = run[1]
        experiment = experiments.experiments.get(run[2].experiment_id)  # type: ignore[attr-defined]
        assert experiments.checkpoints is not None
        return [
            item.sha256
            for item in experiments.checkpoints.list_checkpoints(agent_id_for(experiment))
        ]

    first_hashes = hashes(first)
    second_hashes = hashes(second)
    assert first_hashes[0] == second_hashes[0]
    assert len(set(first_hashes)) == 3
    assert first_hashes[1:] == second_hashes[1:]


def test_reconcile_is_idempotent_after_restart(tmp_path: Path) -> None:
    coordinator, experiments = build(tmp_path)
    created = SQLiteExperimentRepository(tmp_path / "tableforge.db").create(
        new_experiment_id(), config(1), NOW
    )
    experiments.start(created.experiment_id, NOW)
    worker(coordinator, "node-a").run_once()

    for _ in range(3):
        _, restarted = build(tmp_path)
        restarted.reconcile_all(NOW)

    keys = [job.spec.idempotency_key for job in coordinator.jobs.list_jobs(created.experiment_id)]
    assert len(keys) == len(set(keys))
    assert sum(":actor:" in key for key in keys) == 2


def test_rejected_candidate_keeps_base_for_next_round(tmp_path: Path) -> None:
    from tableforge.coordinator import nfsp_iterative

    original = nfsp_iterative.promotion_decision
    nfsp_iterative.promotion_decision = lambda result: (False, "forced rejection")
    try:
        _, experiments, created, _ = run_experiment(tmp_path)
    finally:
        nfsp_iterative.promotion_decision = original
    experiment = experiments.experiments.get(created.experiment_id)  # type: ignore[attr-defined]
    assert experiments.checkpoints is not None
    history = experiments.checkpoints.list_checkpoints(agent_id_for(experiment))

    assert [item.status for item in history] == [
        CheckpointStatus.ACTIVE,
        CheckpointStatus.REJECTED,
        CheckpointStatus.REJECTED,
    ]
    assert history[2].parent_id == history[0].checkpoint_id
    assert history[1].decision_reason == "forced rejection"


def test_failed_job_fails_experiment(tmp_path: Path) -> None:
    coordinator, experiments = build(tmp_path)
    created = SQLiteExperimentRepository(tmp_path / "tableforge.db").create(
        new_experiment_id(), replace(config(1)), NOW
    )
    experiments.start(created.experiment_id, NOW)
    runtime = worker(coordinator, "node-a")
    lease = coordinator.claim_job(runtime.node_id, runtime.capability, NOW, timedelta(minutes=5))
    if lease is None:
        coordinator.register_node(runtime.capability, NOW)
        coordinator.heartbeat_node(runtime.node_id, 1, NOW)
        lease = coordinator.claim_job(
            runtime.node_id, runtime.capability, NOW, timedelta(minutes=5)
        )
    assert lease is not None
    coordinator.fail_job(lease.job.spec.job_id, lease.token, NOW, "BROKEN", "broken", False)

    assert experiments.status(created.experiment_id, NOW).status is ExperimentStatus.FAILED
