from __future__ import annotations

from datetime import timedelta

import pytest

from tableforge.composition import repositories_for
from tableforge.domain.auth import OperatorRole
from tableforge.domain.experiments import ExperimentStatus
from tableforge.domain.ids import NodeId, new_experiment_id
from tableforge.infrastructure.sql import SQLiteDatabase
from tableforge.infrastructure.sql.copy import TABLES, TargetNotEmptyError, copy_metadata
from tests.contract.conftest import DatabaseFactory
from tests.contract.test_metadata import candidate, capability, config
from tests.contract.test_operations import NOW, spec


def test_copy_preserves_all_metadata_and_continues_sequences(
    database_factory: DatabaseFactory, tmp_path_factory: pytest.TempPathFactory
) -> None:
    source = repositories_for(SQLiteDatabase(tmp_path_factory.mktemp("src") / "source.db"))
    experiment_id = new_experiment_id()
    source.experiments.create(experiment_id, config(), NOW)
    source.experiments.update_status(experiment_id, 1, ExperimentStatus.RUNNING, NOW)
    job = spec("copied")
    source.jobs.create(job)
    lease = source.jobs.claim_next(NodeId("node-a"), NOW, timedelta(minutes=5))
    assert lease is not None
    source.jobs.fail(job.job_id, lease.token, NOW, "TEMPORARY", "retry me", True)
    source.nodes.register(capability(), NOW)
    checkpoint = source.checkpoints.register_candidate(candidate(0))
    source.checkpoints.decide(checkpoint.checkpoint_id, True, "initial", NOW)
    token = source.auth.create_enrollment_token(NOW, timedelta(hours=1))
    session = source.auth.enroll(token, NodeId("node-a"), NOW, timedelta(days=1))
    admin = source.auth.issue_operator(OperatorRole.ADMIN, "admin", NOW, timedelta(days=1))
    web = source.auth.create_web_session(admin.token, NOW, timedelta(hours=1))
    source_events = source.events.list_events(limit=1000)

    target = repositories_for(database_factory())
    report = copy_metadata(source.database, target.database)

    assert set(report.rows) == set(TABLES)
    assert report.rows["events"] == len(source_events)
    assert target.experiments.get(experiment_id) == source.experiments.get(experiment_id)
    assert target.jobs.get(job.job_id) == source.jobs.get(job.job_id)
    assert target.jobs.attempt_errors(job.job_id) == source.jobs.attempt_errors(job.job_id)
    assert target.nodes.list_nodes() == source.nodes.list_nodes()
    assert target.checkpoints.list_checkpoints() == source.checkpoints.list_checkpoints()
    assert target.auth.authenticate(session.token, NOW) == NodeId("node-a")
    assert target.auth.web_session(web.token, NOW).identity.role is OperatorRole.ADMIN
    assert target.events.list_events(limit=1000) == source_events

    retry = target.jobs.claim_next(NodeId("node-b"), NOW, timedelta(minutes=5))
    assert retry is not None and retry.attempt_number == 2
    new_events = target.events.list_events(after=source_events[-1].sequence)
    assert [event.event_type for event in new_events] == ["JOB_LEASED"]


def test_copy_refuses_non_empty_target_without_partial_writes(
    database_factory: DatabaseFactory, tmp_path_factory: pytest.TempPathFactory
) -> None:
    source = repositories_for(SQLiteDatabase(tmp_path_factory.mktemp("src") / "source.db"))
    source.jobs.create(spec("from-source"))
    target = repositories_for(database_factory())
    existing = spec("already-there")
    target.jobs.create(existing)

    with pytest.raises(TargetNotEmptyError):
        copy_metadata(source.database, target.database)

    assert [job.spec.job_id for job in target.jobs.list_jobs()] == [existing.job_id]
