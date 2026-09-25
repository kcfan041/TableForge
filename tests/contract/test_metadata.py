from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tableforge.application.ports import IdempotencyConflictError
from tableforge.domain.capabilities import (
    GpuCapability,
    NodeCapability,
    RuntimeCapability,
    StorageCapability,
)
from tableforge.domain.checkpoints import Checkpoint, CheckpointStatus, CheckpointTransitionError
from tableforge.domain.experiments import ExperimentConfig, ExperimentStatus
from tableforge.domain.ids import (
    AgentId,
    ArtifactId,
    NodeId,
    new_checkpoint_id,
    new_experiment_id,
)
from tableforge.infrastructure.sql import (
    Database,
    SqlCheckpointRepository,
    SqlExperimentRepository,
    SqlNodeRepository,
)
from tests.contract.conftest import DatabaseFactory

NOW = datetime(2026, 1, 1, tzinfo=UTC)
AGENT = AgentId("agent-a")


def capability(revision: int = 1) -> NodeCapability:
    return NodeCapability(
        schema_version="1.0",
        node_id=NodeId("node-a"),
        revision=revision,
        operating_system="test",
        architecture="x86_64",
        logical_cpu_cores=8,
        physical_cpu_cores=4,
        total_memory_bytes=16 * 1024**3,
        available_memory_bytes=12 * 1024**3,
        gpus=(
            GpuCapability("cuda:0", "nvidia", "test", 8 * 1024**3, 7 * 1024**3, runtimes=("cuda",)),
        ),
        storage=(StorageCapability("data", 100 * 1024**3),),
        runtime=RuntimeCapability("3.12", ("cpu", "cuda")),
        enabled_roles=("simulation",),
        captured_at=NOW,
    )


def test_register_and_heartbeat_node(database: Database) -> None:
    nodes = SqlNodeRepository(database)
    registered = nodes.register(capability(), NOW)

    heartbeat = nodes.heartbeat(
        registered.node_id,
        registered.capability.revision,
        NOW + timedelta(seconds=30),
        available_memory_bytes=10 * 1024**3,
    )

    assert heartbeat.registered_at == NOW
    assert heartbeat.last_heartbeat_at == NOW + timedelta(seconds=30)
    assert heartbeat.capability.available_memory_bytes == 10 * 1024**3
    assert heartbeat.capability.gpus[0].total_memory_bytes == 8 * 1024**3


def test_new_capability_revision_preserves_registration_time(database: Database) -> None:
    nodes = SqlNodeRepository(database)
    first = nodes.register(capability(), NOW)

    second = nodes.register(replace(capability(2), logical_cpu_cores=16), NOW + timedelta(hours=1))

    assert second.registered_at == first.registered_at
    assert nodes.get(NodeId("node-a")) == second
    assert second.capability.logical_cpu_cores == 16
    assert [node.node_id for node in nodes.list_nodes()] == [NodeId("node-a")]


def test_heartbeat_rejects_wrong_revision(database: Database) -> None:
    nodes = SqlNodeRepository(database)
    nodes.register(capability(), NOW)

    with pytest.raises(ValueError, match="capability revision"):
        nodes.heartbeat(NodeId("node-a"), 2, NOW + timedelta(seconds=1))
    with pytest.raises(ValueError, match="capability revision"):
        nodes.heartbeat(NodeId("missing"), 1, NOW)


def config() -> ExperimentConfig:
    return ExperimentConfig(
        schema_version="1.0",
        preset_key="test",
        preset_version="1",
        hands=100,
        hands_per_job=25,
        seed_start=0,
    )


def test_create_and_revisioned_status_update(database_factory: DatabaseFactory) -> None:
    repository = SqlExperimentRepository(database_factory())
    experiment_id = new_experiment_id()

    created = repository.create(experiment_id, config(), NOW)
    running = repository.update_status(
        experiment_id, created.revision, ExperimentStatus.RUNNING, NOW + timedelta(seconds=1)
    )

    assert (created.revision, running.revision) == (1, 2)
    assert running.status is ExperimentStatus.RUNNING
    assert running.config == created.config
    assert SqlExperimentRepository(database_factory()).get(experiment_id) == running
    assert repository.list_experiments() == (running,)


def test_duplicate_experiment_id_conflicts(database: Database) -> None:
    repository = SqlExperimentRepository(database)
    experiment_id = new_experiment_id()
    repository.create(experiment_id, config(), NOW)

    with pytest.raises(IdempotencyConflictError):
        repository.create(experiment_id, config(), NOW)


def test_stale_revision_is_rejected(database: Database) -> None:
    repository = SqlExperimentRepository(database)
    experiment_id = new_experiment_id()
    repository.create(experiment_id, config(), NOW)
    repository.update_status(experiment_id, 1, ExperimentStatus.RUNNING, NOW)

    with pytest.raises(IdempotencyConflictError):
        repository.update_status(experiment_id, 1, ExperimentStatus.STOPPED, NOW)


def test_concurrent_transition_to_same_status_is_idempotent(database: Database) -> None:
    repository = SqlExperimentRepository(database)
    experiment_id = new_experiment_id()
    repository.create(experiment_id, config(), NOW)
    running = repository.update_status(experiment_id, 1, ExperimentStatus.RUNNING, NOW)
    completed = repository.update_status(
        experiment_id, running.revision, ExperimentStatus.COMPLETED, NOW
    )

    again = repository.update_status(
        experiment_id, running.revision, ExperimentStatus.COMPLETED, NOW
    )

    assert again == completed
    with pytest.raises(IdempotencyConflictError):
        repository.update_status(experiment_id, running.revision, ExperimentStatus.STOPPED, NOW)


def test_racing_status_updates_create_one_revision(database: Database) -> None:
    repository = SqlExperimentRepository(database)
    experiment_id = new_experiment_id()
    repository.create(experiment_id, config(), NOW)

    def advance(index: int) -> int:
        try:
            return repository.update_status(
                experiment_id, 1, ExperimentStatus.RUNNING, NOW
            ).revision
        except IdempotencyConflictError:
            return -1

    with ThreadPoolExecutor(max_workers=8) as executor:
        revisions = list(executor.map(advance, range(16)))

    assert set(revisions) == {2}
    assert repository.get(experiment_id).revision == 2


def candidate(round_index: int, sha: str = "a") -> Checkpoint:
    return Checkpoint(
        checkpoint_id=new_checkpoint_id(),
        agent_id=AGENT,
        experiment_id=new_experiment_id(),
        round_index=round_index,
        parent_id=None,
        artifact_id=ArtifactId(f"artifact-{round_index}"),
        sha256=sha * 64,
        status=CheckpointStatus.CANDIDATE,
        decision_reason="",
        created_at=NOW,
        updated_at=NOW,
    )


def test_promotion_retires_previous_active(database: Database) -> None:
    repository = SqlCheckpointRepository(database)
    first = repository.register_candidate(candidate(0))
    second = repository.register_candidate(candidate(1, "b"))

    repository.decide(first.checkpoint_id, True, "initial", NOW)
    promoted = repository.decide(second.checkpoint_id, True, "ok", NOW + timedelta(seconds=1))

    assert promoted.status is CheckpointStatus.ACTIVE
    assert repository.active(AGENT) == promoted
    old = repository.get(first.checkpoint_id)
    assert old is not None and old.status is CheckpointStatus.RETIRED
    assert [item.round_index for item in repository.list_checkpoints(AGENT)] == [0, 1]


def test_rejection_keeps_existing_active(database: Database) -> None:
    repository = SqlCheckpointRepository(database)
    first = repository.register_candidate(candidate(0))
    repository.decide(first.checkpoint_id, True, "initial", NOW)
    second = repository.register_candidate(candidate(1, "b"))

    rejected = repository.decide(second.checkpoint_id, False, "worse", NOW)

    assert rejected.status is CheckpointStatus.REJECTED
    assert rejected.decision_reason == "worse"
    active = repository.active(AGENT)
    assert active is not None and active.checkpoint_id == first.checkpoint_id


def test_decision_is_idempotent_and_final(database: Database) -> None:
    repository = SqlCheckpointRepository(database)
    first = repository.register_candidate(candidate(0))
    promoted = repository.decide(first.checkpoint_id, True, "initial", NOW)

    assert repository.decide(first.checkpoint_id, True, "again", NOW) == promoted
    with pytest.raises(CheckpointTransitionError):
        repository.decide(first.checkpoint_id, False, "flip", NOW)


def test_register_is_idempotent_per_round(database: Database) -> None:
    repository = SqlCheckpointRepository(database)
    first = repository.register_candidate(candidate(0))

    assert repository.register_candidate(candidate(0)) == first
    with pytest.raises(CheckpointTransitionError):
        repository.register_candidate(candidate(0, "c"))


def test_concurrent_promotions_leave_one_active(database: Database) -> None:
    repository = SqlCheckpointRepository(database)
    candidates = [
        repository.register_candidate(candidate(index, "abcdef"[index])) for index in range(6)
    ]

    def promote(item: Checkpoint) -> None:
        repository.decide(item.checkpoint_id, True, "race", NOW)

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(promote, candidates))

    statuses = [item.status for item in repository.list_checkpoints(AGENT)]
    assert statuses.count(CheckpointStatus.ACTIVE) == 1
    assert statuses.count(CheckpointStatus.RETIRED) == 5
