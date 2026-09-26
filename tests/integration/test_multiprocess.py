from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.multiprocess

CPU_ONLY_ENV = {**os.environ, "TABLEFORGE_WORKER__USE_GPU": "false"}


def cli(data_dir: Path, *arguments: str, timeout: float = 60) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "tableforge.cli.main", "--data-dir", str(data_dir), *arguments],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=CPU_ONLY_ENV,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"tableforge {' '.join(arguments)} exited {result.returncode}\n"
            f"stdout:\n{result.stdout[-2000:]}\nstderr:\n{result.stderr[-4000:]}"
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
        env=CPU_ONLY_ENV,
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
        env=CPU_ONLY_ENV,
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


def test_background_reconciler_drives_iterative_nfsp_with_two_workers(
    coordinator: tuple[Path, str], tmp_path: Path
) -> None:
    pytest.importorskip("torch")
    coordinator_dir, target = coordinator
    experiment_id = cli(
        coordinator_dir, "experiment", "create", "--preset", "hu-nfsp-cpu-iterative"
    )
    cli(coordinator_dir, "experiment", "start", experiment_id)
    workers = []
    for name in ("worker-a", "worker-b"):
        worker_dir = tmp_path / name
        enroll_worker(coordinator_dir, target, worker_dir)
        workers.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "tableforge.cli.main",
                    "--data-dir",
                    str(worker_dir),
                    "worker",
                    "start",
                    "--remote",
                    "--idle-seconds",
                    "0.5",
                ],
                env=CPU_ONLY_ENV,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
    deadline = time.monotonic() + 600
    status: dict[str, object] = {}
    while time.monotonic() < deadline:
        status = json.loads(cli(coordinator_dir, "experiment", "status", experiment_id))
        if status["status"] != "running":
            break
        time.sleep(2)
    for process in workers:
        process.kill()
        process.wait(timeout=30)

    assert status["status"] == "completed", status
    assert status["jobs"] == {"succeeded": 13}
    checkpoints = cli(coordinator_dir, "checkpoint", "list").splitlines()
    rounds = [line.split("\t")[2] for line in checkpoints]
    assert rounds == ["round=0", "round=1", "round=2", "round=3"]
    assert sum(line.endswith("\tactive") for line in checkpoints) == 1
    nodes = cli(coordinator_dir, "node", "status").splitlines()
    assert len(nodes) == 2


def start_coordinator(data: Path, port: int) -> subprocess.Popen[str]:
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
            "--reconcile-seconds",
            "0.5",
        ],
        env=CPU_ONLY_ENV,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return process
        time.sleep(0.1)
    process.kill()
    raise RuntimeError("coordinator did not start")


def stop(process: subprocess.Popen[str]) -> None:
    process.kill()
    process.wait(timeout=30)


def test_worker_survives_coordinator_restart_and_outage(tmp_path: Path) -> None:
    """A running worker keeps going through a coordinator restart and a 30 s outage."""
    data = tmp_path / "coordinator"
    cli(data, "coordinator", "init")
    port = free_port()
    target = f"localhost:{port}"
    server = start_coordinator(data, port)
    worker_dir = tmp_path / "worker"
    log = tmp_path / "worker.log"
    worker: subprocess.Popen[str] | None = None
    try:
        enroll_worker(data, target, worker_dir)
        job_ids = [
            cli(
                data,
                "job",
                "submit-simulation",
                "--hands",
                "8000",
                "--seed-start",
                str(index * 8000),
                "--idempotency-key",
                f"outage-{index}",
            )
            for index in range(12)
        ]
        with log.open("w", encoding="utf-8") as sink:
            worker = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "tableforge.cli.main",
                    "--data-dir",
                    str(worker_dir),
                    "worker",
                    "start",
                    "--remote",
                    "--idle-seconds",
                    "0.2",
                    "--lease-seconds",
                    "10",
                ],
                env=CPU_ONLY_ENV,
                stdout=sink,
                stderr=subprocess.STDOUT,
                text=True,
            )
        time.sleep(4)
        stop(server)
        time.sleep(1)
        server = start_coordinator(data, port)
        time.sleep(4)
        stop(server)
        time.sleep(30)
        assert worker.poll() is None, log.read_text(encoding="utf-8")[-3000:]
        server = start_coordinator(data, port)

        deadline = time.monotonic() + 300
        statuses: list[str] = []
        while time.monotonic() < deadline:
            statuses = [
                json.loads(cli(data, "job", "status", job_id))["status"] for job_id in job_ids
            ]
            if all(status == "succeeded" for status in statuses):
                break
            time.sleep(2)

        output = log.read_text(encoding="utf-8")
        assert statuses == ["succeeded"] * len(job_ids), (statuses, output[-3000:])
        assert worker.poll() is None
        assert output.count('"event": "coordinator_unreachable"') >= 1
        assert output.count('"event": "coordinator_reconnected"') >= 1
        assert "Traceback" not in output
    finally:
        if worker is not None:
            worker.kill()
            worker.wait(timeout=30)
        stop(server)


def test_revoked_worker_exits_with_instructions(tmp_path: Path) -> None:
    data = tmp_path / "coordinator"
    cli(data, "coordinator", "init")
    port = free_port()
    server = start_coordinator(data, port)
    worker_dir = tmp_path / "worker"
    try:
        enroll_worker(data, f"localhost:{port}", worker_dir)
        node_id = (worker_dir / "node-id").read_text(encoding="utf-8").strip()
        cli(data, "node", "revoke", node_id)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tableforge.cli.main",
                "--data-dir",
                str(worker_dir),
                "worker",
                "start",
                "--remote",
            ],
            env=CPU_ONLY_ENV,
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode != 0
        assert "new invite" in result.stderr
        assert "Traceback" not in result.stderr
    finally:
        stop(server)
