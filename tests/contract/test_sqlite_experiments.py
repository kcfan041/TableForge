from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tableforge.application.ports import IdempotencyConflictError
from tableforge.domain.experiments import ExperimentConfig, ExperimentStatus
from tableforge.domain.ids import new_experiment_id
from tableforge.infrastructure.sqlite.experiments import SQLiteExperimentRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def config() -> ExperimentConfig:
    return ExperimentConfig(
        schema_version="1.0",
        preset_key="test",
        preset_version="1",
        hands=100,
        hands_per_job=25,
        seed_start=0,
    )


def test_create_and_revisioned_status_update(tmp_path: Path) -> None:
    repository = SQLiteExperimentRepository(tmp_path / "tableforge.db")
    experiment_id = new_experiment_id()

    created = repository.create(experiment_id, config(), NOW)
    running = repository.update_status(
        experiment_id, created.revision, ExperimentStatus.RUNNING, NOW + timedelta(seconds=1)
    )

    assert created.revision == 1
    assert running.revision == 2
    assert running.status is ExperimentStatus.RUNNING
    assert running.config == created.config
    assert SQLiteExperimentRepository(tmp_path / "tableforge.db").get(experiment_id) == running


def test_stale_revision_is_rejected(tmp_path: Path) -> None:
    repository = SQLiteExperimentRepository(tmp_path / "tableforge.db")
    experiment_id = new_experiment_id()
    repository.create(experiment_id, config(), NOW)
    repository.update_status(experiment_id, 1, ExperimentStatus.RUNNING, NOW)

    with pytest.raises(IdempotencyConflictError):
        repository.update_status(experiment_id, 1, ExperimentStatus.STOPPED, NOW)


def test_concurrent_transition_to_same_status_is_idempotent(tmp_path: Path) -> None:
    repository = SQLiteExperimentRepository(tmp_path / "tableforge.db")
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
