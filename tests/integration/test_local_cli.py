from __future__ import annotations

import json
import sys
from pathlib import Path

from tableforge.cli.main import main


def invoke(monkeypatch: object, capsys: object, arguments: list[str]) -> str:
    monkeypatch.setattr(sys, "argv", ["tableforge", *arguments])  # type: ignore[attr-defined]
    main()
    return capsys.readouterr().out.strip()  # type: ignore[attr-defined]


def test_cli_submit_run_and_status(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    base = ["--data-dir", str(tmp_path)]
    job_id = invoke(
        monkeypatch,
        capsys,
        [
            *base,
            "job",
            "submit-simulation",
            "--hands",
            "25",
            "--idempotency-key",
            "cli-test",
        ],
    )

    listed = invoke(monkeypatch, capsys, [*base, "job", "list"])
    assert f"{job_id}\tqueued" in listed

    worker_output = invoke(monkeypatch, capsys, [*base, "worker", "run-once"])
    assert worker_output == job_id

    status_output = invoke(monkeypatch, capsys, [*base, "job", "status", job_id])
    status = json.loads(status_output)
    assert status["status"] == "succeeded"
    assert len(status["outputs"]) == 1


def test_cli_experiment_create_start_run_and_status(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    base = ["--data-dir", str(tmp_path)]
    experiment_id = invoke(
        monkeypatch,
        capsys,
        [*base, "experiment", "create", "--preset", "hu-rule-smoke"],
    )
    started = invoke(
        monkeypatch, capsys, [*base, "experiment", "start", experiment_id]
    )
    worker_output = invoke(
        monkeypatch,
        capsys,
        [*base, "worker", "start", "--max-jobs", "10", "--idle-seconds", "0"],
    )
    status = json.loads(
        invoke(monkeypatch, capsys, [*base, "experiment", "status", experiment_id])
    )

    assert started == f"{experiment_id}\trunning"
    assert '"event": "job_finished"' in worker_output
    assert status["status"] == "completed"
    assert status["jobs"] == {"succeeded": 4}


def test_cli_node_init_and_status(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    base = ["--data-dir", str(tmp_path)]

    initialized = invoke(monkeypatch, capsys, [*base, "node", "init"])
    status = invoke(monkeypatch, capsys, [*base, "node", "status"])

    assert initialized == "local-worker\trevision=1"
    assert "local-worker\trevision=1\theartbeat=" in status


def test_cli_cancels_queued_job(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    base = ["--data-dir", str(tmp_path)]
    job_id = invoke(
        monkeypatch,
        capsys,
        [
            *base,
            "job",
            "submit-simulation",
            "--hands",
            "10",
            "--idempotency-key",
            "cancel-test",
        ],
    )

    cancelled = invoke(monkeypatch, capsys, [*base, "job", "cancel", job_id])
    status = json.loads(invoke(monkeypatch, capsys, [*base, "job", "status", job_id]))

    assert cancelled == f"{job_id}\tcancelled"
    assert status["status"] == "cancelled"
    assert status["errors"] == []
