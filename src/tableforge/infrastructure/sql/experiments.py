from __future__ import annotations

import json
from dataclasses import asdict, fields
from datetime import datetime
from typing import Any

from tableforge.application.ports import IdempotencyConflictError, JobNotFoundError
from tableforge.domain.experiments import (
    Experiment,
    ExperimentConfig,
    ExperimentKind,
    ExperimentStatus,
)
from tableforge.domain.ids import ExperimentId
from tableforge.infrastructure.sql.database import Database, parse_timestamp, require_aware
from tableforge.infrastructure.sql.events import record_event

_SELECT = """
SELECT e.*, r.status, r.config_json
FROM experiments e
JOIN experiment_revisions r
  ON r.experiment_id = e.experiment_id
 AND r.revision = e.current_revision
"""


class SqlExperimentRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    def create(
        self, experiment_id: ExperimentId, config: ExperimentConfig, now: datetime
    ) -> Experiment:
        require_aware(now)
        try:
            with self._db.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO experiments (
                        experiment_id, current_revision, created_at, updated_at
                    ) VALUES (?, 1, ?, ?)
                    """,
                    (str(experiment_id), self._db.timestamp(now), self._db.timestamp(now)),
                )
                connection.execute(
                    """
                    INSERT INTO experiment_revisions (
                        experiment_id, revision, status, config_json, created_at
                    ) VALUES (?, 1, ?, ?, ?)
                    """,
                    (
                        str(experiment_id),
                        ExperimentStatus.CREATED.value,
                        _config_json(config),
                        self._db.timestamp(now),
                    ),
                )
                record_event(
                    self._db,
                    connection,
                    "EXPERIMENT_CREATED",
                    "experiment",
                    str(experiment_id),
                    1,
                    {"preset": config.preset_key, "status": ExperimentStatus.CREATED.value},
                    now,
                )
        except self._db.integrity_errors as error:
            raise IdempotencyConflictError(str(experiment_id)) from error
        return Experiment(experiment_id, 1, ExperimentStatus.CREATED, config, now, now)

    def get(self, experiment_id: ExperimentId) -> Experiment:
        with self._db.read() as connection:
            row = connection.execute(
                f"{_SELECT} WHERE e.experiment_id = ?", (str(experiment_id),)
            ).fetchone()
        if row is None:
            raise JobNotFoundError(str(experiment_id))
        return _row_to_experiment(row)

    def update_status(
        self,
        experiment_id: ExperimentId,
        expected_revision: int,
        status: ExperimentStatus,
        now: datetime,
    ) -> Experiment:
        require_aware(now)
        with self._db.transaction() as connection:
            locked = connection.execute(
                f"SELECT current_revision FROM experiments WHERE experiment_id = ?"
                f"{self._db.lock_for_update}",
                (str(experiment_id),),
            ).fetchone()
            if locked is None:
                raise JobNotFoundError(str(experiment_id))
            row = connection.execute(
                f"{_SELECT} WHERE e.experiment_id = ?", (str(experiment_id),)
            ).fetchone()
            if ExperimentStatus(row["status"]) is status:
                return _row_to_experiment(row)
            if int(row["current_revision"]) != expected_revision:
                raise IdempotencyConflictError(str(experiment_id))
            revision = expected_revision + 1
            connection.execute(
                """
                INSERT INTO experiment_revisions (
                    experiment_id, revision, status, config_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(experiment_id),
                    revision,
                    status.value,
                    row["config_json"],
                    self._db.timestamp(now),
                ),
            )
            connection.execute(
                """
                UPDATE experiments SET current_revision = ?, updated_at = ?
                WHERE experiment_id = ?
                """,
                (revision, self._db.timestamp(now), str(experiment_id)),
            )
            record_event(
                self._db,
                connection,
                f"EXPERIMENT_{status.value.upper()}",
                "experiment",
                str(experiment_id),
                revision,
                {"status": status.value},
                now,
            )
            updated = connection.execute(
                f"{_SELECT} WHERE e.experiment_id = ?", (str(experiment_id),)
            ).fetchone()
        return _row_to_experiment(updated)

    def list_experiments(self) -> tuple[Experiment, ...]:
        with self._db.read() as connection:
            rows = connection.execute(
                f"{_SELECT} ORDER BY e.created_at, e.experiment_id"
            ).fetchall()
        return tuple(_row_to_experiment(row) for row in rows)


def _config_json(config: ExperimentConfig) -> str:
    data = asdict(config)
    data["kind"] = config.kind.value
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _row_to_experiment(row: Any) -> Experiment:
    data = json.loads(row["config_json"])
    data["kind"] = ExperimentKind(data.get("kind", ExperimentKind.RULE_SIMULATION.value))
    known = {field.name for field in fields(ExperimentConfig)}
    data = {key: value for key, value in data.items() if key in known}
    return Experiment(
        experiment_id=ExperimentId(row["experiment_id"]),
        revision=int(row["current_revision"]),
        status=ExperimentStatus(row["status"]),
        config=ExperimentConfig(**data),
        created_at=parse_timestamp(row["created_at"]),
        updated_at=parse_timestamp(row["updated_at"]),
    )
