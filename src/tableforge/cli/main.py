from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from tableforge.capability.discovery import LocalCapabilityDiscovery
from tableforge.capability.evaluator import assess_compatibility
from tableforge.capability.workloads import BUILTIN_WORKLOADS
from tableforge.coordinator.experiments import ExperimentService
from tableforge.coordinator.service import CoordinatorService
from tableforge.domain.capabilities import CompatibilityStatus, NodeCapability
from tableforge.domain.ids import ExperimentId, JobId, NodeId, new_experiment_id, new_job_id
from tableforge.domain.jobs import Job, JobSpec, JobType
from tableforge.domain.simulation import SimulationJobPayload
from tableforge.infrastructure.local_artifacts import LocalArtifactStore
from tableforge.infrastructure.sqlite.experiments import SQLiteExperimentRepository
from tableforge.infrastructure.sqlite.jobs import SQLiteJobRepository
from tableforge.infrastructure.sqlite.nodes import SQLiteNodeRepository
from tableforge.worker.loop import JsonLineLogger, WorkerLoop
from tableforge.worker.runtime import WorkerRuntime
from tableforge.worker.simulation import SimulationExecutor


def main() -> None:
    parser = argparse.ArgumentParser(prog="tableforge")
    parser.add_argument("--data-dir", type=Path, default=Path(".tableforge"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--json", action="store_true", dest="as_json")
    doctor.add_argument("--data-path", type=Path, default=Path.cwd())

    job = subparsers.add_parser("job")
    job_subparsers = job.add_subparsers(dest="job_command", required=True)
    submit = job_subparsers.add_parser("submit-simulation")
    submit.add_argument("--hands", type=int, required=True)
    submit.add_argument("--seed-start", type=int, default=0)
    submit.add_argument("--idempotency-key", required=True)
    job_subparsers.add_parser("list")
    status = job_subparsers.add_parser("status")
    status.add_argument("job_id")
    cancel = job_subparsers.add_parser("cancel")
    cancel.add_argument("job_id")

    worker = subparsers.add_parser("worker")
    worker_subparsers = worker.add_subparsers(dest="worker_command", required=True)
    worker_subparsers.add_parser("run-once")
    worker_start = worker_subparsers.add_parser("start")
    worker_start.add_argument("--max-jobs", type=int)
    worker_start.add_argument("--idle-seconds", type=float, default=1.0)

    experiment = subparsers.add_parser("experiment")
    experiment_subparsers = experiment.add_subparsers(
        dest="experiment_command", required=True
    )
    experiment_create = experiment_subparsers.add_parser("create")
    experiment_create.add_argument("--preset", required=True)
    experiment_start = experiment_subparsers.add_parser("start")
    experiment_start.add_argument("experiment_id")
    experiment_status = experiment_subparsers.add_parser("status")
    experiment_status.add_argument("experiment_id")
    experiment_stop = experiment_subparsers.add_parser("stop")
    experiment_stop.add_argument("experiment_id")
    experiment_subparsers.add_parser("list")

    node = subparsers.add_parser("node")
    node_subparsers = node.add_subparsers(dest="node_command", required=True)
    node_subparsers.add_parser("init")
    node_subparsers.add_parser("status")

    args = parser.parse_args()
    if args.command == "doctor":
        exit_code = run_doctor(args.data_path, args.as_json)
        raise SystemExit(exit_code)
    service = _local_service(args.data_dir)
    if args.command == "job" and args.job_command == "submit-simulation":
        created_job = submit_simulation(
            service,
            hands=args.hands,
            seed_start=args.seed_start,
            idempotency_key=args.idempotency_key,
        )
        print(created_job.spec.job_id)
    elif args.command == "job" and args.job_command == "list":
        for queued_job in service.list_jobs():
            print(f"{queued_job.spec.job_id}\t{queued_job.status.value}")
    elif args.command == "job" and args.job_command == "status":
        selected = service.get_job(JobId(args.job_id))
        outputs = service.job_outputs(selected.spec.job_id)
        errors = service.job_attempt_errors(selected.spec.job_id)
        print(
            json.dumps(
                {
                    "errors": [
                        {
                            "code": error.error_code,
                            "retryable": error.retryable,
                            "summary": error.error_summary,
                        }
                        for error in errors
                    ],
                    "job_id": str(selected.spec.job_id),
                    "outputs": [str(output.artifact.descriptor.artifact_id) for output in outputs],
                    "status": selected.status.value,
                    "version": selected.version,
                },
                sort_keys=True,
            )
        )
    elif args.command == "job" and args.job_command == "cancel":
        cancelled = service.cancel_job(JobId(args.job_id), datetime.now(UTC))
        print(f"{cancelled.spec.job_id}\t{cancelled.status.value}")
    elif args.command == "worker" and args.worker_command == "run-once":
        runtime = _local_worker(service, args.data_dir)
        lease = runtime.run_once()
        print("idle" if lease is None else lease.job.spec.job_id)
    elif args.command == "worker" and args.worker_command == "start":
        WorkerLoop(
            runtime=_local_worker(service, args.data_dir),
            logger=JsonLineLogger(),
            idle_seconds=args.idle_seconds,
        ).run(max_jobs=args.max_jobs)
    elif args.command == "experiment":
        experiments = _experiment_service(service, args.data_dir)
        now = datetime.now(UTC)
        if args.experiment_command == "create":
            created_experiment = experiments.create(args.preset, now)
            print(created_experiment.experiment_id)
        elif args.experiment_command == "start":
            started = experiments.start(ExperimentId(args.experiment_id), now)
            print(f"{started.experiment_id}\t{started.status.value}")
        elif args.experiment_command == "status":
            current = experiments.status(ExperimentId(args.experiment_id), now)
            jobs = service.jobs.list_jobs(current.experiment_id)
            print(
                json.dumps(
                    {
                        "experiment_id": str(current.experiment_id),
                        "jobs": {
                            status: sum(job.status.value == status for job in jobs)
                            for status in sorted({job.status.value for job in jobs})
                        },
                        "preset": current.config.preset_key,
                        "revision": current.revision,
                        "status": current.status.value,
                    },
                    sort_keys=True,
                )
            )
        elif args.experiment_command == "stop":
            stopped = experiments.stop(ExperimentId(args.experiment_id), now)
            print(f"{stopped.experiment_id}\t{stopped.status.value}")
        elif args.experiment_command == "list":
            for current in experiments.list_experiments():
                print(f"{current.experiment_id}\t{current.status.value}")
    elif args.command == "node" and args.node_command == "init":
        capability = _discover_local_worker(args.data_dir)
        service.register_node(capability, datetime.now(UTC))
        print(f"{capability.node_id}\trevision={capability.revision}")
    elif args.command == "node" and args.node_command == "status":
        for registered in service.list_nodes():
            print(
                f"{registered.node_id}\trevision={registered.capability.revision}"
                f"\theartbeat={registered.last_heartbeat_at.isoformat()}"
            )


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def submit_simulation(
    service: CoordinatorService,
    hands: int,
    seed_start: int,
    idempotency_key: str,
) -> Job:
    profile = BUILTIN_WORKLOADS[0]
    return service.create_job(
        JobSpec(
            schema_version="1.0",
            job_id=new_job_id(),
            experiment_id=new_experiment_id(),
            type=JobType.SIMULATION,
            priority=1,
            resources=profile.minimum,
            payload=SimulationJobPayload(
                schema_version="1.0",
                hands=hands,
                seed_start=seed_start,
            ).to_bytes(),
            payload_type=profile.payload_type,
            idempotency_key=idempotency_key,
        )
    )


def _local_service(data_dir: Path) -> CoordinatorService:
    data_dir.mkdir(parents=True, exist_ok=True)
    return CoordinatorService(
        jobs=SQLiteJobRepository(data_dir / "tableforge.db"),
        artifacts=LocalArtifactStore(data_dir / "artifacts"),
        workloads=BUILTIN_WORKLOADS,
        nodes=SQLiteNodeRepository(data_dir / "tableforge.db"),
    )


def _discover_local_worker(data_dir: Path) -> NodeCapability:
    return LocalCapabilityDiscovery(
        node_id=NodeId("local-worker"),
        roles=("simulation",),
        storage_path=data_dir,
    ).discover()


def _experiment_service(
    coordinator: CoordinatorService, data_dir: Path
) -> ExperimentService:
    return ExperimentService(
        experiments=SQLiteExperimentRepository(data_dir / "tableforge.db"),
        coordinator=coordinator,
    )


def _local_worker(service: CoordinatorService, data_dir: Path) -> WorkerRuntime:
    capability = _discover_local_worker(data_dir)
    return WorkerRuntime(
        node_id=NodeId("local-worker"),
        capability=capability,
        coordinator=service,
        clock=SystemClock(),
        lease_duration=timedelta(minutes=5),
        simulation_executor=SimulationExecutor(),
    )


def run_doctor(data_path: Path, as_json: bool) -> int:
    capability = LocalCapabilityDiscovery(
        node_id=NodeId("local-doctor"),
        roles=("simulation", "evaluation", "nfsp_learner"),
        storage_path=data_path,
    ).discover()
    assessments = [assess_compatibility(capability, profile) for profile in BUILTIN_WORKLOADS]
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "capability": asdict(capability),
        "workloads": [asdict(assessment) for assessment in assessments],
    }
    if as_json:
        print(json.dumps(report, default=_json_default, sort_keys=True))
    else:
        print(f"Node: {capability.operating_system}/{capability.architecture}")
        print(
            f"CPU: {capability.logical_cpu_cores or 'unknown'} logical cores | "
            f"RAM: {_format_bytes(capability.total_memory_bytes)}"
        )
        print(f"GPU: {len(capability.gpus)} detected")
        for assessment in assessments:
            reasons = ", ".join(reason.value for reason in assessment.reason_codes)
            suffix = f": {reasons}" if reasons else ""
            print(f"{assessment.workload_key:<28} {assessment.status.value.upper()}{suffix}")
        for warning in capability.warnings:
            print(f"Warning: {warning}")
    unhealthy = not any(
        assessment.status is CompatibilityStatus.SUPPORTED for assessment in assessments
    )
    has_limitations = any(
        assessment.status is not CompatibilityStatus.SUPPORTED for assessment in assessments
    )
    if unhealthy:
        return 1
    if has_limitations or capability.warnings:
        return 2
    return 0


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    return f"{value / 1024**3:.1f} GiB"


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


if __name__ == "__main__":
    main()
