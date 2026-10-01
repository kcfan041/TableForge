"""Fingerprint of small NFSP experiments, to check that refactors keep them byte-identical.

Runs a few tiny iterative NFSP experiments end to end (in-process coordinator and worker)
and prints, for every job in creation order, its payload type, a sha256 of its payload and
of its output. Saving the output before a refactor and comparing it afterwards shows that
job payloads (and so idempotency keys) and results did not change.

    uv run python scripts/algorithm_baseline.py > before.json
    ... refactor ...
    uv run python scripts/algorithm_baseline.py > after.json
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch  # noqa: E402

from tableforge.cli.main import _learning_executors, _rule_executors  # noqa: E402
from tableforge.composition import build_services  # noqa: E402
from tableforge.settings import Settings  # noqa: E402
from tableforge.worker.loop import WorkerLoop  # noqa: E402
from tableforge.worker.runtime import WorkerRuntime  # noqa: E402
from tableforge.worker.simulation import SimulationExecutor  # noqa: E402
from tests.integration.test_experiment_workflow import CapturingLogger, capability  # noqa: E402

NOW = datetime(2026, 1, 1, tzinfo=UTC)
EXPERIMENTS = (
    ("6max-pko-10bb-smoke", {}),
    ("6max-pko-10bb-smoke", {"pool_history": 1, "pool_eval_seeds": 2}),
    ("hu-nfsp-cpu-smoke", {}),
)


class Clock:
    def now(self) -> datetime:
        return NOW


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def run(preset: str, settings: dict[str, object], root: Path) -> list[dict[str, object]]:
    services = build_services(Settings(data_dir=root))
    experiments = services.experiments
    created = experiments.create(preset, NOW, settings)
    experiments.start(created.experiment_id, NOW)
    worker = capability()
    runtime = WorkerRuntime(
        node_id=worker.node_id,
        capability=replace(
            worker,
            enabled_roles=("simulation", "evaluation", "nfsp_learner", "nfsp_actor"),
            logical_cpu_cores=8,
            total_memory_bytes=32 * 1024**3,
            available_memory_bytes=32 * 1024**3,
        ),
        coordinator=services.coordinator,
        clock=Clock(),
        lease_duration=timedelta(minutes=5),
        simulation_executor=SimulationExecutor(),
        extra_executors={
            **_rule_executors(),
            **_learning_executors(services.coordinator),  # type: ignore[arg-type]
        },
    )
    WorkerLoop(
        runtime,
        CapturingLogger(),
        idle_seconds=0,
        on_idle=lambda: experiments.reconcile_all(NOW) > 0,
    ).run(max_jobs=80)
    experiments.reconcile_all(NOW)
    rows = []
    jobs = sorted(
        services.coordinator.jobs.list_jobs(created.experiment_id),
        key=lambda job: job.spec.idempotency_key,
    )
    prefix = f"experiment:{created.experiment_id}:"
    for job in jobs:
        outputs = services.coordinator.job_outputs(job.spec.job_id)
        output = (
            services.coordinator.read_artifact(str(outputs[0].artifact.descriptor.artifact_id))
            if outputs
            else b""
        )
        # Output artifacts get new ids in every run; payloads refer to them, so ids are
        # replaced by placeholders (the sha256 next to each id still pins the content).
        payload = re.sub(r'"[0-9a-f]{8}-[0-9a-f-]{27,}"', '"<id>"', job.spec.payload.decode())
        # Snapshots, pools and populations list other artifacts by id, so their own sha256
        # changes with those ids; what they point to (actor outputs, checkpoints) is compared.
        payload = re.sub(
            r'"(snapshot|pool|population)_sha256":"[0-9a-f]{64}"', r'"\1_sha256":"<sha>"', payload
        )
        payload = re.sub(r'"content-[0-9a-f]{64}"', '"<content>"', payload)
        if os.environ.get("BASELINE_PAYLOADS"):
            print(job.spec.idempotency_key.removeprefix(prefix), payload, file=sys.stderr)
        # Ids that differ between runs (experiment ids in keys) are left out.
        rows.append(
            {
                "key": job.spec.idempotency_key.removeprefix(prefix),
                "type": job.spec.payload_type,
                "status": job.status.value,
                "payload": sha(payload.encode()),
                "output": sha(output),
            }
        )
    checkpoints = [
        (item.round_index, item.status.value, item.sha256[:16])
        for item in services.repositories.checkpoints.list_checkpoints()
    ]
    rows.append(
        {"checkpoints": checkpoints, "curve": experiments.training_curve(created.experiment_id)}
    )
    return rows


def main() -> None:
    torch.set_num_threads(1)
    report = {}
    for index, (preset, settings) in enumerate(EXPERIMENTS):
        with tempfile.TemporaryDirectory() as directory:
            report[f"{index}:{preset}"] = run(preset, settings, Path(directory))
    print(json.dumps(report, indent=1, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
