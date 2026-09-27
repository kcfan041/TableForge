from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tableforge.cli.main import _rule_executors
from tableforge.domain.experiments import ExperimentStatus
from tableforge.domain.jobs import JobStatus
from tableforge.domain.tournament import TournamentJobPayload
from tableforge.poker.tournament import preset_config
from tableforge.poker.tournament_benchmark import TournamentSamples
from tableforge.worker.loop import WorkerLoop
from tableforge.worker.runtime import WorkerRuntime
from tableforge.worker.simulation import SimulationExecutor
from tableforge.worker.tournament import TournamentExecutor
from tests.integration.test_experiment_workflow import (
    CapturingLogger,
    FixedClock,
    capability,
    services,
)


def test_payload_round_trip_and_validation() -> None:
    config = preset_config(25, "50-30-20").to_json()
    payload = TournamentJobPayload("1.0", config, 5, 100, "tight", "pushfold")

    assert TournamentJobPayload.from_bytes(payload.to_bytes()) == payload
    with pytest.raises(ValueError):
        TournamentJobPayload("1.0", config, 5, 0, "shark")
    with pytest.raises(ValueError):
        TournamentJobPayload("1.0", config, 5, 0, "tight", "sharks")
    with pytest.raises(ValueError):
        TournamentJobPayload("1.0", "{}", 5, 0)


def test_samples_from_separate_jobs_merge() -> None:
    executor = TournamentExecutor()
    config = preset_config(10, "65-35").to_json()
    first = TournamentSamples.from_json(
        executor.execute(TournamentJobPayload("1.0", config, 3, 0).to_bytes())
    )
    second = TournamentSamples.from_json(
        executor.execute(TournamentJobPayload("1.0", config, 2, 3).to_bytes())
    )
    together = TournamentSamples.from_json(
        executor.execute(TournamentJobPayload("1.0", config, 5, 0).to_bytes())
    )

    merged = first.merge(second)

    assert merged == together
    assert merged.tournaments == 30


def test_tournament_experiment_runs_to_a_merged_summary(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator, experiments = services(tmp_path)
    created = experiments.create("6max-sng-10bb", now)
    experiments.start(created.experiment_id, now)
    jobs = coordinator.jobs.list_jobs(created.experiment_id)
    assert len(jobs) == 10
    payloads = [TournamentJobPayload.from_bytes(job.spec.payload) for job in jobs]
    assert sorted(payload.seed_start for payload in payloads) == list(range(0, 500, 50))

    worker_capability = capability()
    runtime = WorkerRuntime(
        node_id=worker_capability.node_id,
        capability=worker_capability,
        coordinator=coordinator,
        clock=FixedClock(now),
        lease_duration=timedelta(minutes=5),
        simulation_executor=SimulationExecutor(),
        extra_executors=_rule_executors(),
    )
    # Two jobs are enough to check merging without running all 3,000 tournaments.
    for job in jobs[2:]:
        coordinator.cancel_job(job.spec.job_id, now)
    processed = WorkerLoop(runtime, CapturingLogger(), idle_seconds=0).run(max_jobs=5)

    summary = experiments.tournament_result(created.experiment_id)

    assert processed == 2
    assert summary is not None
    assert summary["jobs_finished"] == 2
    assert summary["tournaments"] == 2 * 50 * 6
    assert summary["candidate"] == "pushfold"
    assert summary["starting_stack_bb"] == 10
    for key in ("prize_ev", "bounty_ev", "total_ev", "roi", "itm", "average_place"):
        assert set(summary[key]) == {"mean", "low", "high"}  # type: ignore[arg-type]
    assert sum(summary["place_share"]) == pytest.approx(1.0)  # type: ignore[arg-type]
    json.dumps(summary)
    assert all(
        job.status in (JobStatus.SUCCEEDED, JobStatus.CANCELLED)
        for job in coordinator.jobs.list_jobs(created.experiment_id)
    )
    assert experiments.experiments.get(created.experiment_id).status is ExperimentStatus.RUNNING
