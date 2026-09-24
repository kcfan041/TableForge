from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("torch")

import torch  # noqa: E402

from tableforge.domain.learning import (  # noqa: E402
    NfspActorPayload,
    NfspInitPayload,
    NfspLearnerPayload,
)
from tableforge.learning.experience import DatasetSnapshot, ShardReference  # noqa: E402
from tableforge.learning.iterative import (  # noqa: E402
    NfspActorExecutor,
    NfspInitExecutor,
    NfspLearnerExecutor,
)
from tableforge.worker.runtime import (  # noqa: E402
    OutputSpec,
    RegisteredExecutor,
    _is_out_of_memory,
    _oom_summary,
)


class Store:
    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}

    def put(self, content: bytes) -> tuple[str, str]:
        digest = hashlib.sha256(content).hexdigest()
        self.items[digest[:16]] = content
        return digest[:16], digest

    def fetch(self, artifact_id: str) -> bytes:
        return self.items[artifact_id]


def learner_inputs(store: Store) -> bytes:
    base = store.put(
        NfspInitExecutor().execute(
            NfspInitPayload("1.0", agent_seed=3, hidden_size=64, batch_size=64).to_bytes()
        )
    )
    shard = store.put(
        NfspActorExecutor(store.fetch).execute(
            NfspActorPayload("1.0", base[0], base[1], 150, 0, 1).to_bytes()
        )
    )
    snapshot = store.put(DatasetSnapshot(base[1], 1, (ShardReference(*shard),)).to_bytes())
    return NfspLearnerPayload(
        "1.0", base[0], base[1], snapshot[0], snapshot[1], 60, 1
    ).to_bytes()


def test_cpu_plan_matches_legacy_execute() -> None:
    store = Store()
    payload = learner_inputs(store)
    executor = NfspLearnerExecutor(store.fetch)

    assert executor.execute(payload) == executor.execute_with_plan(payload, {"runtime": "cpu"})


def test_batch_limit_changes_training_but_not_saved_config() -> None:
    store = Store()
    payload = learner_inputs(store)
    executor = NfspLearnerExecutor(store.fetch)

    limited = executor.execute_with_plan(payload, {"runtime": "cpu", "batch_size": 16})
    manifest = json.loads(
        __import__("zipfile").ZipFile(__import__("io").BytesIO(limited)).read("manifest.json")
    )

    assert limited != executor.execute(payload)
    assert manifest["config"]["batch_size"] == 64


def test_registered_executor_routes_plan_to_plan_aware_executor() -> None:
    store = Store()
    payload = learner_inputs(store)
    executor = NfspLearnerExecutor(store.fetch)
    registered = RegisteredExecutor(
        executor, OutputSpec(executor.output_kind, executor.output_media_type, "x")
    )

    via_registry = registered.run(payload, json.dumps({"runtime": "cpu", "batch_size": 16}), None)

    assert via_registry == executor.execute_with_plan(
        payload, {"runtime": "cpu", "batch_size": 16}
    )


def test_out_of_memory_is_classified() -> None:
    assert _is_out_of_memory(torch.cuda.OutOfMemoryError("CUDA out of memory"))
    assert _is_out_of_memory(RuntimeError("CUDA error: out of memory"))
    assert not _is_out_of_memory(RuntimeError("shape mismatch"))
    summary = json.loads(_oom_summary(RuntimeError("CUDA out of memory"), '{"batch_size": 8}'))
    assert summary["execution_plan"] == {"batch_size": 8}
    assert "peak_gpu_memory_bytes" in summary


def run_probe(name: str) -> subprocess.CompletedProcess[str]:
    from tests.conftest import gpu_subprocess_env

    return subprocess.run(
        [sys.executable, str(Path(__file__).with_name("_gpu_batch_probe.py")), name],
        capture_output=True,
        text=True,
        timeout=600,
        env=gpu_subprocess_env(),
    )


@pytest.mark.requires_cuda
def test_gpu_learner_trains_on_device_and_checkpoint_loads_on_cpu() -> None:
    result = run_probe("learner-parity")

    assert result.returncode == 0, result.stdout + result.stderr[-3000:]


@pytest.mark.requires_cuda
@pytest.mark.vram_8gb
def test_planned_batch_trains_within_budget_on_this_gpu() -> None:
    result = run_probe("planned-batch")
    if result.returncode == 3:
        pytest.skip(result.stdout.strip())

    assert result.returncode == 0, result.stdout + result.stderr[-3000:]
