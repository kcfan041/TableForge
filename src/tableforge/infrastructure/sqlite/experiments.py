from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path

from tableforge.application.ports import IdempotencyConflictError, JobNotFoundError
from tableforge.domain.experiments import (
    Experiment,
    ExperimentConfig,
    ExperimentKind,
    ExperimentStatus,
)
from tableforge.domain.ids import ExperimentId
from tableforge.infrastructure.sqlite.events import ensure_events_table, record_event

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    current_revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiment_revisions (
    experiment_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (experiment_id, revision),
    FOREIGN KEY (experiment_id) REFERENCES experiments(experiment_id)
);
"""


class SQLiteExperimentRepository:
    def __init__(self, database: Path) -> None:
        self._database = database
        self._database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            ensure_events_table(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    def create(
        self, experiment_id: ExperimentId, config: ExperimentConfig, now: datetime
    ) -> Experiment:
        _require_aware(now)
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO experiments (
                        experiment_id, current_revision, created_at, updated_at
                    ) VALUES (?, 1, ?, ?)
                    """,
                    (str(experiment_id), now.isoformat(), now.isoformat()),
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
                        now.isoformat(),
                    ),
                )
                record_event(
                    connection,
                    "EXPERIMENT_CREATED",
                    "experiment",
                    str(experiment_id),
                    1,
                    {"preset": config.preset_key, "status": ExperimentStatus.CREATED.value},
                    now,
                )
            except sqlite3.IntegrityError as error:
                raise IdempotencyConflictError(str(experiment_id)) from error
        return Experiment(
            experiment_id,
            1,
            ExperimentStatus.CREATED,
            config,
            now,
            now,
        )

    def get(self, experiment_id: ExperimentId) -> Experiment:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT e.*, r.status, r.config_json
                FROM experiments e
                JOIN experiment_revisions r
                  ON r.experiment_id = e.experiment_id
                 AND r.revision = e.current_revision
                WHERE e.experiment_id = ?
                """,
                (str(experiment_id),),
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
        _require_aware(now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT e.*, r.status, r.config_json
                FROM experiments e
                JOIN experiment_revisions r
                  ON r.experiment_id = e.experiment_id
                 AND r.revision = e.current_revision
                WHERE e.experiment_id = ?
                """,
                (str(experiment_id),),
            ).fetchone()
            if row is None:
                raise JobNotFoundError(str(experiment_id))
            if row["current_revision"] != expected_revision:
                raise IdempotencyConflictError(str(experiment_id))
            if ExperimentStatus(row["status"]) is status:
                return _row_to_experiment(row)
            revision = expected_revision + 1
            connection.execute(
                """
                INSERT INTO experiment_revisions (
                    experiment_id, revision, status, config_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (str(experiment_id), revision, status.value, row["config_json"], now.isoformat()),
            )
            connection.execute(
                """
                UPDATE experiments
                SET current_revision = ?, updated_at = ?
                WHERE experiment_id = ?
                """,
                (revision, now.isoformat(), str(experiment_id)),
            )
            record_event(
                connection,
                f"EXPERIMENT_{status.value.upper()}",
                "experiment",
                str(experiment_id),
                revision,
                {"status": status.value},
                now,
            )
        return self.get(experiment_id)

    def list_experiments(self) -> tuple[Experiment, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT e.*, r.status, r.config_json
                FROM experiments e
                JOIN experiment_revisions r
                  ON r.experiment_id = e.experiment_id
                 AND r.revision = e.current_revision
                ORDER BY e.created_at, e.experiment_id
                """
            ).fetchall()
        return tuple(_row_to_experiment(row) for row in rows)


def _config_json(config: ExperimentConfig) -> str:
    data = asdict(config)
    data["kind"] = config.kind.value
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _row_to_experiment(row: sqlite3.Row) -> Experiment:
    data = json.loads(row["config_json"])
    data["kind"] = ExperimentKind(data.get("kind", ExperimentKind.RULE_SIMULATION.value))
    known = {field.name for field in fields(ExperimentConfig)}
    data = {key: value for key, value in data.items() if key in known}
    return Experiment(
        experiment_id=ExperimentId(row["experiment_id"]),
        revision=row["current_revision"],
        status=ExperimentStatus(row["status"]),
        config=ExperimentConfig(**data),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
