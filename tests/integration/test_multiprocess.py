from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.multiprocess


def cli(data_dir: Path, *arguments: str, timeout: float = 60) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "tableforge.cli.main", "--data-dir", str(data_dir), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout.strip()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def coordinator(tmp_path: Path) -> Iterator[tuple[Path, str]]:
    data = tmp_path / "coordinator"
    cli(data, "coordinator", "init")
    port = free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tableforge.cli.main",
            "--data-dir",
            str(data),
            "coordinator",
            "serve",
            "--listen",
            f"127.0.0.1:{port}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.1)
    else:
        process.kill()
        raise RuntimeError("coordinator did not start")
    try:
        yield data, f"localhost:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def enroll_worker(coordinator_dir: Path, target: str, worker_dir: Path) -> None:
    token = cli(coordinator_dir, "coordinator", "enrollment-token")
    cli(
        worker_dir,
        "node",
        "enroll",
        "--coordinator",
        target,
        "--token",
        token,
        "--ca-file",
        str(coordinator_dir / "tls" / "server.crt"),
    )


def test_remote_worker_process_completes_experiment(
    coordinator: tuple[Path, str], tmp_path: Path
) -> None:
    coordinator_dir, target = coordinator
    experiment_id = cli(coordinator_dir, "experiment", "create", "--preset", "hu-rule-smoke")
    cli(coordinator_dir, "experiment", "start", experiment_id)
    worker_dir = tmp_path / "worker"
    enroll_worker(coordinator_dir, target, worker_dir)

    output = cli(
        worker_dir, "worker", "start", "--remote", "--max-jobs", "10", "--idle-seconds", "0"
    )
    status = json.loads(cli(coordinator_dir, "experiment", "status", experiment_id))

    assert output.count('"event": "job_finished"') == 4
    assert status["status"] == "completed"
    assert status["jobs"] == {"succeeded": 4}
    assert not (worker_dir / "tableforge.db").exists()


def test_killed_remote_worker_job_is_recovered(
    coordinator: tuple[Path, str], tmp_path: Path
) -> None:
    coordinator_dir, target = coordinator
    job_id = cli(
        coordinator_dir,
        "job",
        "submit-simulation",
        "--hands",
        "60000",
        "--idempotency-key",
        "kill-test",
    )
    victim_dir = tmp_path / "victim"
    enroll_worker(coordinator_dir, target, victim_dir)
    victim = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tableforge.cli.main",
            "--data-dir",
            str(victim_dir),
            "worker",
            "start",
            "--remote",
            "--max-jobs",
            "1",
            "--lease-seconds",
            "2",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status = json.loads(cli(coordinator_dir, "job", "status", job_id))
        if status["status"] == "leased":
            break
        time.sleep(0.2)
    victim.kill()
    victim.wait(timeout=10)
    assert json.loads(cli(coordinator_dir, "job", "status", job_id))["status"] == "leased"

    time.sleep(3)
    rescuer_dir = tmp_path / "rescuer"
    enroll_worker(coordinator_dir, target, rescuer_dir)
    output = cli(
        rescuer_dir,
        "worker",
        "start",
        "--remote",
        "--max-jobs",
        "1",
        "--idle-seconds",
        "0",
        timeout=300,
    )
    status = json.loads(cli(coordinator_dir, "job", "status", job_id))

    assert '"status": "succeeded"' in output
    assert status["status"] == "succeeded"
    assert len(status["outputs"]) == 1
