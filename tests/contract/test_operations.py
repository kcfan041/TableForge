from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tableforge.application.ports import LeaseFencedError
from tableforge.capability.workloads import BUILTIN_WORKLOADS
from tableforge.domain.auth import AuthenticationError
from tableforge.domain.ids import NodeId, new_experiment_id, new_job_id
from tableforge.domain.jobs import JobSpec, JobType
from tableforge.domain.simulation import SimulationJobPayload
from tableforge.infrastructure.artifact_gc import collect_garbage, referenced_hashes
from tableforge.infrastructure.local_artifacts import LocalArtifactStore
from tableforge.infrastructure.sqlite.auth import SQLiteAuthRepository
from tableforge.infrastructure.sqlite.checkpoints import SQLiteCheckpointRepository
from tableforge.infrastructure.sqlite.events import SQLiteEventLog
from tableforge.infrastructure.sqlite.jobs import SQLiteJobRepository
from tableforge.observability import Counters, render_metrics

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def spec(key: str = "k") -> JobSpec:
    profile = BUILTIN_WORKLOADS[0]
    return JobSpec(
        schema_version="1.0",
        job_id=new_job_id(),
        experiment_id=new_experiment_id(),
        type=JobType.SIMULATION,
        priority=1,
        resources=profile.minimum,
        payload=SimulationJobPayload("1.0", hands=10, seed_start=0).to_bytes(),
        payload_type=profile.payload_type,
        idempotency_key=key,
        created_at=NOW,
    )


def test_job_transitions_emit_ordered_outbox_events(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"
    jobs = SQLiteJobRepository(database)
    job = spec()
    jobs.create(job)
    lease = jobs.claim_next(NodeId("n"), NOW, timedelta(minutes=5))
    assert lease is not None
    jobs.complete(job.job_id, lease.token, NOW, "a" * 64)

    events = SQLiteEventLog(database).list_events()

    assert [event.event_type for event in events] == [
        "JOB_CREATED",
        "JOB_LEASED",
        "JOB_SUCCEEDED",
    ]
    assert [event.aggregate_version for event in events] == [0, 1, 2]
    assert all(event.aggregate_id == str(job.job_id) for event in events)


def test_rejected_transition_writes_no_event(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"
    jobs = SQLiteJobRepository(database)
    job = spec()
    jobs.create(job)
    jobs.claim_next(NodeId("n"), NOW, timedelta(minutes=5))

    with pytest.raises(LeaseFencedError):
        jobs.complete(job.job_id, "wrong-token", NOW, "a" * 64)

    log = SQLiteEventLog(database)
    assert [event.event_type for event in log.list_events()] == ["JOB_CREATED", "JOB_LEASED"]
    assert log.unpublished_count() == 2
    assert log.mark_published(2, NOW) == 2
    assert log.unpublished_count() == 0


def test_metrics_render_prometheus_text(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"
    jobs = SQLiteJobRepository(database)
    SQLiteCheckpointRepository(database)
    jobs.create(spec("a"))
    jobs.create(spec("b"))
    jobs.claim_next(NodeId("n"), NOW, timedelta(seconds=10))
    counters = Counters()
    counters.increment("tableforge_rpc_requests_total", method="ClaimJob", outcome="ok")

    text = render_metrics(database, (NOW + timedelta(minutes=1)).isoformat(), counters)

    assert 'tableforge_jobs{status="queued"} 1' in text
    assert 'tableforge_jobs{status="leased"} 1' in text
    assert "tableforge_expired_active_leases 1" in text
    assert "# TYPE tableforge_rpc_requests_total counter" in text
    assert 'tableforge_rpc_requests_total{method="ClaimJob",outcome="ok"} 1' in text


def _store_blob(store: LocalArtifactStore, content: bytes) -> str:
    import io

    from tableforge.domain.artifacts import ArtifactDescriptor, ArtifactKind
    from tableforge.domain.ids import new_artifact_id

    grant = store.begin_upload(
        ArtifactDescriptor(new_artifact_id(), ArtifactKind.DIAGNOSTIC, "text/plain", "1")
    )
    digest, size = store.write_upload(grant, io.BytesIO(content))
    return store.commit_upload(grant, digest, size).sha256


def test_gc_deletes_only_old_unreferenced_objects(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"
    jobs = SQLiteJobRepository(database)
    checkpoints = SQLiteCheckpointRepository(database)
    store = LocalArtifactStore(tmp_path / "artifacts")
    kept = _store_blob(store, b"referenced")
    orphan = _store_blob(store, b"orphan")
    fresh = _store_blob(store, b"fresh-orphan")
    jobs.create(
        replace(spec("ref"), payload=json.dumps({"checkpoint_sha256": kept}).encode())
    )
    old = time.time() - 7 * 24 * 3600
    for digest in (kept, orphan):
        path = tmp_path / "artifacts" / "sha256" / digest[:2] / digest[2:4] / digest
        os.utime(path, (old, old))
    referenced = referenced_hashes(jobs, checkpoints)

    dry = collect_garbage(tmp_path / "artifacts", referenced, 3600, dry_run=True)
    real = collect_garbage(tmp_path / "artifacts", referenced, 3600, dry_run=False)

    assert (dry.deleted_files, dry.kept_recent) == (1, 1)
    assert store.exists(orphan) is False
    assert store.exists(kept)
    assert store.exists(fresh)
    assert real.deleted_files == 1


def test_session_rotation_and_revocation(tmp_path: Path) -> None:
    auth = SQLiteAuthRepository(tmp_path / "db.sqlite")
    token = auth.create_enrollment_token(NOW, timedelta(hours=1))
    first = auth.enroll(token, NodeId("node-a"), NOW, timedelta(days=1))

    rotated = auth.rotate_session(first.token, NOW, timedelta(days=1))

    assert auth.authenticate(rotated.token, NOW) == NodeId("node-a")
    with pytest.raises(AuthenticationError):
        auth.authenticate(first.token, NOW)
    assert auth.revoke_node(NodeId("node-a")) == 1
    with pytest.raises(AuthenticationError):
        auth.authenticate(rotated.token, NOW)
