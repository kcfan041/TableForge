from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tableforge.domain.checkpoints import (
    Checkpoint,
    CheckpointStatus,
    CheckpointTransitionError,
)
from tableforge.domain.ids import (
    AgentId,
    ArtifactId,
    new_checkpoint_id,
    new_experiment_id,
)
from tableforge.infrastructure.sqlite.checkpoints import SQLiteCheckpointRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)
AGENT = AgentId("agent-a")


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


def test_promotion_retires_previous_active(tmp_path: Path) -> None:
    repository = SQLiteCheckpointRepository(tmp_path / "db.sqlite")
    first = repository.register_candidate(candidate(0))
    second = repository.register_candidate(candidate(1, "b"))

    repository.decide(first.checkpoint_id, True, "initial", NOW)
    promoted = repository.decide(second.checkpoint_id, True, "ok", NOW + timedelta(seconds=1))

    assert promoted.status is CheckpointStatus.ACTIVE
    assert repository.active(AGENT) == promoted
    old = repository.get(first.checkpoint_id)
    assert old is not None and old.status is CheckpointStatus.RETIRED


def test_rejection_keeps_existing_active(tmp_path: Path) -> None:
    repository = SQLiteCheckpointRepository(tmp_path / "db.sqlite")
    first = repository.register_candidate(candidate(0))
    repository.decide(first.checkpoint_id, True, "initial", NOW)
    second = repository.register_candidate(candidate(1, "b"))

    rejected = repository.decide(second.checkpoint_id, False, "worse", NOW)

    assert rejected.status is CheckpointStatus.REJECTED
    assert rejected.decision_reason == "worse"
    active = repository.active(AGENT)
    assert active is not None and active.checkpoint_id == first.checkpoint_id


def test_decision_is_idempotent_and_final(tmp_path: Path) -> None:
    repository = SQLiteCheckpointRepository(tmp_path / "db.sqlite")
    first = repository.register_candidate(candidate(0))
    promoted = repository.decide(first.checkpoint_id, True, "initial", NOW)

    assert repository.decide(first.checkpoint_id, True, "again", NOW) == promoted
    with pytest.raises(CheckpointTransitionError):
        repository.decide(first.checkpoint_id, False, "flip", NOW)


def test_register_is_idempotent_per_round(tmp_path: Path) -> None:
    repository = SQLiteCheckpointRepository(tmp_path / "db.sqlite")
    first = repository.register_candidate(candidate(0))

    assert repository.register_candidate(candidate(0)) == first
    with pytest.raises(CheckpointTransitionError):
        repository.register_candidate(candidate(0, "c"))
