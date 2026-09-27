from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from tableforge.capability.discovery import LocalCapabilityDiscovery
from tableforge.capability.evaluator import assess_compatibility
from tableforge.capability.workloads import BUILTIN_WORKLOADS
from tableforge.composition import Services, build_services
from tableforge.coordinator.service import CoordinatorService
from tableforge.domain.auth import INVITABLE_ROLES, OperatorRole
from tableforge.domain.capabilities import CompatibilityStatus, NodeCapability
from tableforge.domain.ids import (
    AgentId,
    CheckpointId,
    ExperimentId,
    InviteId,
    JobId,
    NodeId,
    OperatorId,
    new_experiment_id,
    new_job_id,
)
from tableforge.domain.jobs import Job, JobSpec, JobType
from tableforge.domain.league import MatrixCell
from tableforge.domain.simulation import SimulationJobPayload
from tableforge.infrastructure.node_identity import NodeIdentityStore
from tableforge.learning.device import hide_accelerators
from tableforge.observability import COUNTERS
from tableforge.settings import Settings, SettingsError, load_settings
from tableforge.transport.client import enroll
from tableforge.transport.credentials import RemoteCredentials
from tableforge.transport.server import start_server
from tableforge.transport.tls import CertificateHostsError, TlsMaterial, generate_self_signed
from tableforge.worker.client import CoordinatorClient
from tableforge.worker.loop import JsonLineLogger, WorkerLoop, WorkerStoppedError
from tableforge.worker.runtime import RegisteredExecutor, WorkerRuntime
from tableforge.worker.simulation import SimulationExecutor


def main() -> None:
    parser = argparse.ArgumentParser(prog="tableforge")
    parser.add_argument("--data-dir", type=Path, default=Path(".tableforge"))
    parser.add_argument("--config", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--json", action="store_true", dest="as_json")
    doctor.add_argument("--data-path", type=Path, default=Path.cwd())
    doctor.add_argument(
        "--coordinator",
        action="store_true",
        help="also test this node's connection to its cluster (DNS, TLS, session, storage)",
    )

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
    worker_start.add_argument("--idle-seconds", type=float)
    worker_start.add_argument("--remote", action="store_true")
    worker_start.add_argument("--lease-seconds", type=float)
    worker_start.add_argument("--no-gpu", action="store_false", dest="use_gpu", default=None)

    coordinator = subparsers.add_parser("coordinator")
    coordinator_subparsers = coordinator.add_subparsers(dest="coordinator_command", required=True)
    coordinator_init = coordinator_subparsers.add_parser("init")
    coordinator_init.add_argument(
        "--host",
        action="append",
        default=[],
        help="extra DNS name or IP the certificate must cover (repeatable), e.g. a Tailscale IP",
    )
    coordinator_init.add_argument(
        "--regenerate",
        action="store_true",
        help="replace the certificate; enrolled nodes then need a new invite",
    )
    coordinator_serve = coordinator_subparsers.add_parser("serve")
    coordinator_serve.add_argument("--listen")
    coordinator_serve.add_argument("--insecure", action="store_true")
    coordinator_serve.add_argument("--reconcile-seconds", type=float)
    coordinator_serve.add_argument("--metrics-listen")
    coordinator_serve.add_argument(
        "--http-listen",
        help="web UI address (default 127.0.0.1:8080); an empty string disables the web UI",
    )
    coordinator_admin = coordinator_subparsers.add_parser(
        "admin",
        help="issue a web/remote admin credential (coordinator host only, never by invite)",
    )
    coordinator_admin.add_argument("--label", default="admin")
    coordinator_admin.add_argument("--ttl-days", type=float, default=30.0)
    enrollment = coordinator_subparsers.add_parser("enrollment-token")
    enrollment.add_argument("--ttl-minutes", type=int, default=60)
    enrollment.add_argument("--max-uses", type=int, default=1)
    enrollment.add_argument("--output", type=Path)
    invite = coordinator_subparsers.add_parser(
        "invite", help="create, list or revoke single-use invites (coordinator host only)"
    )
    invite_subparsers = invite.add_subparsers(dest="invite_command", required=True)
    invite_create = invite_subparsers.add_parser("create")
    invite_create.add_argument(
        "--address",
        required=True,
        help="host:port the new machine uses to reach this coordinator, e.g. 100.64.0.1:7443",
    )
    invite_create.add_argument(
        "--node", action="store_true", help="enroll the joining machine as a worker node"
    )
    invite_create.add_argument(
        "--role",
        choices=[role.value for role in INVITABLE_ROLES],
        help="also grant a remote control credential with this role",
    )
    invite_create.add_argument("--ttl-hours", type=float, default=24.0)
    invite_create.add_argument(
        "--confirm-admin",
        action="store_true",
        help="required with --role admin; admin invites are valid for 1 hour at most",
    )
    invite_create.add_argument("--label", default="")
    invite_subparsers.add_parser("list")
    invite_revoke = invite_subparsers.add_parser("revoke")
    invite_revoke.add_argument("invite_id")
    operator = coordinator_subparsers.add_parser(
        "operator", help="list or revoke remote control credentials (coordinator host only)"
    )
    operator_subparsers = operator.add_subparsers(dest="operator_command", required=True)
    operator_subparsers.add_parser("list")
    operator_revoke = operator_subparsers.add_parser("revoke")
    operator_revoke.add_argument("operator_id")

    join_parser = subparsers.add_parser(
        "join", help="join a TableForge cluster with an invite code"
    )
    join_parser.add_argument("invite")

    ui_parser = subparsers.add_parser(
        "ui",
        help="local control panel on 127.0.0.1: join, check the connection, run workers",
    )
    ui_parser.add_argument("--port", type=int, default=8765)
    ui_parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    ui_parser.add_argument(
        "--no-gpu", action="store_false", dest="ui_use_gpu", help="start workers without GPU"
    )
    ui_parser.add_argument(
        "--home",
        type=Path,
        help="where TableForge keeps this machine's data (default: per-user app data folder)",
    )

    remote = subparsers.add_parser(
        "remote", help="control a cluster remotely with the credential from `join`"
    )
    remote.add_argument("--cluster", help="cluster id when this machine joined several")
    remote_subparsers = remote.add_subparsers(dest="remote_command", required=True)
    remote_subparsers.add_parser("whoami")
    remote_subparsers.add_parser("presets")
    remote_subparsers.add_parser("nodes")
    remote_experiment = remote_subparsers.add_parser("experiment")
    remote_experiment_subparsers = remote_experiment.add_subparsers(
        dest="remote_experiment_command", required=True
    )
    remote_experiment_subparsers.add_parser("list")
    remote_create = remote_experiment_subparsers.add_parser("create")
    remote_create.add_argument("--preset", required=True)
    for name in ("start", "stop", "status"):
        remote_experiment_subparsers.add_parser(name).add_argument("experiment_id")
    remote_checkpoint = remote_subparsers.add_parser("checkpoints")
    remote_checkpoint.add_argument("--agent", default="")
    remote_matrix = remote_subparsers.add_parser("matrix")
    remote_matrix.add_argument("experiment_id")
    remote_matrix.add_argument("--json", action="store_true", dest="as_json")

    experiment = subparsers.add_parser("experiment")
    experiment_subparsers = experiment.add_subparsers(dest="experiment_command", required=True)
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
    node_enroll = node_subparsers.add_parser("enroll")
    node_enroll.add_argument("--coordinator", required=True)
    token_source = node_enroll.add_mutually_exclusive_group(required=True)
    token_source.add_argument("--token")
    token_source.add_argument("--token-file", type=Path)
    token_source.add_argument(
        "--bundle",
        type=Path,
        help="directory containing enrollment-token and server.crt (e.g. copied from Compose)",
    )
    node_enroll.add_argument("--ca-file", type=Path)
    node_enroll.add_argument("--insecure", action="store_true")
    node_revoke = node_subparsers.add_parser("revoke")
    node_revoke.add_argument("node_id")
    node_subparsers.add_parser("rotate-session")

    events = subparsers.add_parser("events")
    events_subparsers = events.add_subparsers(dest="events_command", required=True)
    events_tail = events_subparsers.add_parser("tail")
    events_tail.add_argument("--after", type=int, default=0)
    events_tail.add_argument("--limit", type=int, default=50)

    artifact = subparsers.add_parser("artifact")
    artifact_subparsers = artifact.add_subparsers(dest="artifact_command", required=True)
    artifact_gc = artifact_subparsers.add_parser("gc")
    artifact_gc.add_argument("--dry-run", action="store_true")
    artifact_gc.add_argument("--grace-hours", type=float, default=24.0)

    metadata = subparsers.add_parser("metadata")
    metadata_subparsers = metadata.add_subparsers(dest="metadata_command", required=True)
    metadata_subparsers.add_parser("migrate-schema")
    metadata_copy = metadata_subparsers.add_parser("copy-from-sqlite")
    metadata_copy.add_argument("source", type=Path)

    checkpoint = subparsers.add_parser("checkpoint")
    checkpoint_subparsers = checkpoint.add_subparsers(dest="checkpoint_command", required=True)
    checkpoint_list = checkpoint_subparsers.add_parser("list")
    checkpoint_list.add_argument("--agent")
    checkpoint_show = checkpoint_subparsers.add_parser("show")
    checkpoint_show.add_argument("checkpoint_id")

    league = subparsers.add_parser("league")
    league_subparsers = league.add_subparsers(dest="league_command", required=True)
    league_matrix = league_subparsers.add_parser("matrix")
    league_matrix.add_argument("experiment_id")
    league_matrix.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args()
    try:
        settings = load_settings(
            args.data_dir,
            config_file=args.config,
            overrides={
                "worker.lease_seconds": getattr(args, "lease_seconds", None),
                "worker.idle_seconds": getattr(args, "idle_seconds", None),
                "worker.use_gpu": getattr(args, "use_gpu", None),
                "coordinator.listen": getattr(args, "listen", None),
                "coordinator.reconcile_seconds": getattr(args, "reconcile_seconds", None),
                "coordinator.metrics_listen": getattr(args, "metrics_listen", None),
                "coordinator.http_listen": getattr(args, "http_listen", None),
            },
        )
    except SettingsError as error:
        raise SystemExit(f"configuration error: {error}") from error
    is_worker = args.command == "worker"
    if (is_worker and not settings.worker.use_gpu) or args.command == "coordinator":
        hide_accelerators()
    if args.command == "doctor":
        exit_code = run_doctor(args.data_path, args.as_json)
        if args.coordinator:
            exit_code = max(exit_code, _doctor_coordinator(settings, args.as_json))
        raise SystemExit(exit_code)
    if args.command == "join":
        _join(args, settings)
        return
    if args.command == "ui":
        _local_ui(args, settings)
        return
    if args.command == "remote":
        _remote(args)
        return
    if args.command == "node" and args.node_command == "enroll":
        _enroll_node(args)
        return
    if args.command == "node" and args.node_command == "rotate-session":
        path = settings.data_dir / "coordinator.json"
        credentials = RemoteCredentials.load(path)
        session, expires_at = credentials.client().rotate_session()
        credentials.with_session(session, expires_at).save(path)
        print(f"rotated\texpires={expires_at.isoformat()}")
        return
    if args.command == "worker" and args.worker_command == "start" and args.remote:
        credentials_path = settings.data_dir / "coordinator.json"
        if not credentials_path.is_file():
            raise SystemExit(
                f"this machine has not joined a cluster ({credentials_path} not found); "
                "run `tableforge join <invite>` first"
            )
        client = RemoteCredentials.load(credentials_path).client(
            settings.data_dir / "artifact-cache",
            settings.worker.cache_max_bytes,
            settings.artifacts.endpoint or None,
        )
        _run_worker(
            client, settings, args.max_jobs, start_slot=True, credentials_path=credentials_path
        )
        return
    if args.command == "metadata":
        _metadata_command(args, settings)
        return
    services = build_services(settings)
    service = services.coordinator
    if args.command == "coordinator":
        _coordinator_command(args, services)
        return
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
        runtime = _local_worker(service, settings)
        lease = runtime.run_once()
        print("idle" if lease is None else lease.job.spec.job_id)
    elif args.command == "worker" and args.worker_command == "start":
        experiments = services.experiments
        _run_worker(
            service,
            settings,
            args.max_jobs,
            on_idle=lambda: experiments.reconcile_all(datetime.now(UTC)) > 0,
        )
    elif args.command == "experiment":
        experiments = services.experiments
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
            report: dict[str, object] = {
                "experiment_id": str(current.experiment_id),
                "jobs": {
                    status: sum(job.status.value == status for job in jobs)
                    for status in sorted({job.status.value for job in jobs})
                },
                "preset": current.config.preset_key,
                "revision": current.revision,
                "status": current.status.value,
            }
            evaluation = experiments.evaluation_result(current.experiment_id)
            if evaluation is not None:
                report["evaluation"] = evaluation
            print(json.dumps(report, sort_keys=True))
        elif args.experiment_command == "stop":
            stopped = experiments.stop(ExperimentId(args.experiment_id), now)
            print(f"{stopped.experiment_id}\t{stopped.status.value}")
        elif args.experiment_command == "list":
            for current in experiments.list_experiments():
                print(f"{current.experiment_id}\t{current.status.value}")
    elif args.command == "league":
        matrix = services.experiments.league_matrix(ExperimentId(args.experiment_id))
        if matrix is None:
            raise SystemExit("matchup matrix is not available yet")
        if args.as_json:
            print(
                json.dumps(
                    {
                        row: {column: asdict(cell) for column, cell in cells.items()}
                        for row, cells in matrix.items()
                    },
                    sort_keys=True,
                )
            )
        else:
            print(_format_matrix(matrix))
    elif args.command == "checkpoint":
        checkpoints = services.repositories.checkpoints
        if args.checkpoint_command == "list":
            agent = AgentId(args.agent) if args.agent else None
            for item in checkpoints.list_checkpoints(agent):
                print(
                    f"{item.checkpoint_id}\t{item.agent_id}\tround={item.round_index}"
                    f"\t{item.status.value}"
                )
        else:
            found = checkpoints.get(CheckpointId(args.checkpoint_id))
            if found is None:
                raise SystemExit(f"unknown checkpoint {args.checkpoint_id}")
            print(
                json.dumps(
                    {
                        "agent_id": str(found.agent_id),
                        "artifact_id": str(found.artifact_id),
                        "checkpoint_id": str(found.checkpoint_id),
                        "decision_reason": found.decision_reason,
                        "experiment_id": str(found.experiment_id),
                        "parent_id": str(found.parent_id) if found.parent_id else None,
                        "round": found.round_index,
                        "sha256": found.sha256,
                        "status": found.status.value,
                    },
                    sort_keys=True,
                )
            )
    elif args.command == "node" and args.node_command == "revoke":
        revoked = services.repositories.auth.revoke_node(NodeId(args.node_id))
        print(f"{args.node_id}\trevoked_sessions={revoked}")
    elif args.command == "events":
        for event in services.repositories.events.list_events(args.after, args.limit):
            print(
                json.dumps(
                    {
                        "aggregate_id": event.aggregate_id,
                        "aggregate_type": event.aggregate_type,
                        "aggregate_version": event.aggregate_version,
                        "event_type": event.event_type,
                        "occurred_at": event.occurred_at.isoformat(),
                        "payload": event.payload,
                        "sequence": event.sequence,
                    },
                    sort_keys=True,
                )
            )
    elif args.command == "artifact":
        from tableforge.infrastructure.artifact_gc import collect_garbage, referenced_hashes

        gc_report = collect_garbage(
            service.artifacts,
            referenced_hashes(service.jobs, services.repositories.checkpoints),
            grace=timedelta(hours=args.grace_hours),
            dry_run=args.dry_run,
        )
        print(json.dumps(asdict(gc_report), sort_keys=True))
    elif args.command == "node" and args.node_command == "init":
        capability = _discover_local_worker(settings.data_dir)
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


def _learning_available() -> bool:
    import importlib.util

    return all(importlib.util.find_spec(name) is not None for name in ("torch", "safetensors"))


def _worker_roles() -> tuple[str, ...]:
    if _learning_available():
        return ("simulation", "evaluation", "nfsp_learner", "nfsp_actor")
    return ("simulation",)


def _discover_local_worker(data_dir: Path, use_gpu: bool = True) -> NodeCapability:
    node_id = NodeIdentityStore(data_dir / "node-id").load_or_create()
    return LocalCapabilityDiscovery(
        node_id=node_id,
        roles=_worker_roles(),
        storage_path=data_dir,
        detect_gpus=use_gpu,
        probe_cuda_runtime=use_gpu,
    ).discover()


def _learning_executors(
    service: CoordinatorClient,
) -> dict[str, RegisteredExecutor]:
    if not _learning_available():
        return {}
    from tableforge.learning.registry import learning_executors

    return learning_executors(service.read_artifact)


def _stop_event() -> threading.Event:
    stop = threading.Event()

    def request_stop(signum: int, frame: object) -> None:
        del signum, frame
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, request_stop)
    return stop


def _run_worker(
    coordinator: CoordinatorClient,
    settings: Settings,
    max_jobs: int | None,
    on_idle: Callable[[], bool] | None = None,
    start_slot: bool = False,
    credentials_path: Path | None = None,
) -> None:
    stop = _stop_event()

    def interruptible_sleep(seconds: float) -> None:
        stop.wait(seconds)

    runtime = _local_worker(coordinator, settings)
    context: dict[str, object] = {"component": "worker", "node_id": str(runtime.node_id)}
    before_iteration: Callable[[], None] | None = None
    if start_slot:
        from tableforge.transport.client import GrpcCoordinatorClient
        from tableforge.transport.remote_worker import RemoteWorkerSession

        assert isinstance(coordinator, GrpcCoordinatorClient)
        assert credentials_path is not None
        session = RemoteWorkerSession(
            coordinator,
            runtime.node_id,
            credentials_path,
            JsonLineLogger().bind(**context),
            sleep=interruptible_sleep,
            should_stop=stop.is_set,
        )
        try:
            context.update(session.start())
        except WorkerStoppedError as error:
            raise SystemExit(f"worker stopped: {error}") from error
        before_iteration = session.before_iteration
    try:
        WorkerLoop(
            runtime=runtime,
            logger=JsonLineLogger().bind(**context),
            idle_seconds=settings.worker.idle_seconds,
            sleep=interruptible_sleep,
            on_idle=on_idle,
            before_iteration=before_iteration,
        ).run(should_stop=stop.is_set, max_jobs=max_jobs)
    except WorkerStoppedError as error:
        raise SystemExit(f"worker stopped: {error}") from error


def _coordinator_command(args: argparse.Namespace, services: Services) -> None:
    settings = services.settings
    data_dir = settings.data_dir
    auth = services.repositories.auth
    service = services.coordinator
    if args.coordinator_command == "init":
        try:
            material = generate_self_signed(
                data_dir / "tls", tuple(args.host), regenerate=args.regenerate
            )
        except CertificateHostsError as error:
            raise SystemExit(str(error)) from error
        print(
            json.dumps(
                {
                    "certificate": str(material.certificate_path),
                    "fingerprint": material.fingerprint(),
                    "hosts": list(material.hosts()),
                },
                sort_keys=True,
            )
        )
    elif args.coordinator_command == "invite":
        _invite_command(args, services)
    elif args.coordinator_command == "admin":
        if args.ttl_days <= 0:
            raise SystemExit("--ttl-days must be positive")
        issued = auth.issue_operator(
            OperatorRole.ADMIN, args.label, datetime.now(UTC), timedelta(days=args.ttl_days)
        )
        print(
            json.dumps(
                {
                    "credential": issued.token,
                    "expires_at": issued.expires_at.isoformat(),
                    "operator_id": str(issued.operator_id),
                    "role": issued.role.value,
                },
                sort_keys=True,
            )
        )
        print(
            "Sign in to the web UI with this credential. It is shown only once; revoke it "
            "with `tableforge coordinator operator revoke <operator_id>`.",
            file=sys.stderr,
        )
    elif args.coordinator_command == "operator":
        if args.operator_command == "list":
            for identity in auth.list_operators():
                print(f"{identity.operator_id}\t{identity.role.value}\t{identity.label}")
        elif not auth.revoke_operator(OperatorId(args.operator_id)):
            raise SystemExit(f"no active operator credential {args.operator_id}")
        else:
            print(f"{args.operator_id}\trevoked")
    elif args.coordinator_command == "enrollment-token":
        if args.ttl_minutes < 1 or args.max_uses < 1:
            raise SystemExit("--ttl-minutes and --max-uses must be at least 1")
        token = auth.create_enrollment_token(
            datetime.now(UTC), timedelta(minutes=args.ttl_minutes), args.max_uses
        )
        if args.output is None:
            print(token)
        else:
            _write_secret(args.output, token)
            print(args.output)
    elif args.coordinator_command == "serve":
        tls = None
        if not args.insecure:
            material = TlsMaterial(data_dir / "tls" / "server.crt", data_dir / "tls" / "server.key")
            if not material.certificate_path.exists():
                raise SystemExit("run 'tableforge coordinator init' first or pass --insecure")
            tls = material
        from tableforge.infrastructure.s3_artifacts import parse_endpoints
        from tableforge.transport.control import ControlServicer
        from tableforge.transport.server import ServerConfig
        from tableforge.web.views import ClusterViews

        endpoints: list[tuple[str, str]] = []
        if settings.artifacts.backend == "s3" and settings.artifacts.direct_transfer:
            default_url = settings.artifacts.public_endpoint_url or settings.artifacts.endpoint_url
            if default_url:
                endpoints.append(("", default_url))
            endpoints.extend(parse_endpoints(settings.artifacts.endpoints).items())
        clock = SystemClock()
        auth.cluster_id(clock.now())
        running = start_server(
            service,
            auth,
            clock,
            settings.coordinator.listen,
            tls,
            config=ServerConfig(
                storage_endpoints=tuple(endpoints),
                lease_duration=timedelta(seconds=settings.worker.lease_seconds),
                idle_interval=timedelta(seconds=settings.worker.idle_seconds),
                direct_transfer=settings.artifacts.direct_transfer,
            ),
            control=ControlServicer(
                services.experiments, services.repositories.checkpoints, auth, clock
            ).with_views(ClusterViews(services, clock)),
        )
        logger = JsonLineLogger().bind(component="coordinator")
        logger.emit(
            "coordinator_started",
            port=running.port,
            tls=tls is not None,
            settings=settings.redacted(),
        )
        web = None
        if settings.coordinator.http_listen:
            from tableforge.web.app import WebContext
            from tableforge.web.server import start_web_server

            web = start_web_server(
                WebContext(services, clock),
                settings.coordinator.http_listen,
                certificate=settings.coordinator.http_certificate or None,
                private_key=settings.coordinator.http_private_key or None,
            )
            scheme = "https" if settings.coordinator.http_certificate else "http"
            host = settings.coordinator.http_listen.rpartition(":")[0] or "127.0.0.1"
            logger.emit("web_started", url=f"{scheme}://{host}:{web.port}/")
        experiments = services.experiments
        metrics = None
        if settings.coordinator.metrics_listen:
            from tableforge.observability import render_metrics, start_metrics_server

            metrics = start_metrics_server(
                settings.coordinator.metrics_listen,
                lambda: render_metrics(services.repositories.database, datetime.now(UTC)),
            )
            logger.emit("metrics_started", port=metrics.port)
        stop = _stop_event()
        from tableforge.web.cluster import STOP_FILE

        stop_file = data_dir / STOP_FILE
        stop_file.unlink(missing_ok=True)
        while not stop.wait(settings.coordinator.reconcile_seconds):
            if stop_file.exists():
                logger.emit("stop_requested", source=str(stop_file))
                break
            try:
                advanced = experiments.reconcile_all(datetime.now(UTC))
            except Exception as error:
                COUNTERS.increment("tableforge_reconcile_total", outcome="error")
                logger.emit("reconcile_failed", error=type(error).__name__, detail=str(error))
                continue
            COUNTERS.increment("tableforge_reconcile_total", outcome="ok")
            if advanced:
                logger.emit("experiments_reconciled", count=advanced)
        if metrics is not None:
            metrics.stop()
        if web is not None:
            web.stop()
        running.stop(5.0)
        logger.emit("coordinator_stopped")


def _doctor_coordinator(settings: Settings, as_json: bool) -> int:
    from tableforge.transport.diagnostics import check_connection

    node_id = str(NodeIdentityStore(settings.data_dir / "node-id").load_or_create())
    report = check_connection(settings.data_dir, node_id)
    if as_json:
        print(json.dumps(report.as_dict(), sort_keys=True))
    else:
        print(f"Coordinator: {report.target or 'not joined'}")
        for check in report.checks:
            mark = "OK  " if check.ok else "FAIL"
            print(f"  [{mark}] {check.name:<8} {check.detail}")
            if check.hint:
                print(f"         -> {check.hint}")
    return 0 if report.ok else 1


def _invite_command(args: argparse.Namespace, services: Services) -> None:
    from tableforge.domain.auth import InviteScope
    from tableforge.transport.invite_issue import InviteIssueError, issue_invite

    auth = services.repositories.auth
    settings = services.settings
    now = datetime.now(UTC)
    if args.invite_command == "list":
        for item in auth.list_invites():
            state = (
                "revoked"
                if item.revoked
                else "used"
                if item.redeemed_at
                else "expired"
                if not item.usable(now)
                else "open"
            )
            print(
                f"{item.invite_id}\t{item.scope.encode()}\t{state}"
                f"\texpires={item.expires_at.isoformat()}\t{item.label}"
            )
        return
    if args.invite_command == "revoke":
        if not auth.revoke_invite(InviteId(args.invite_id)):
            raise SystemExit(f"no open invite {args.invite_id}")
        print(f"{args.invite_id}\trevoked")
        return
    try:
        scope = InviteScope(node=args.node, role=OperatorRole(args.role) if args.role else None)
    except ValueError as error:
        raise SystemExit("choose --node, --role, or both") from error
    try:
        issued = issue_invite(
            auth,
            settings,
            args.address,
            scope,
            timedelta(hours=args.ttl_hours),
            args.label,
            now,
            confirm_admin=args.confirm_admin,
        )
    except InviteIssueError as error:
        raise SystemExit(str(error)) from error
    for warning in issued.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(
        json.dumps(
            {
                "expires_at": issued.invite.expires_at.isoformat(),
                "invite": issued.code,
                "invite_id": str(issued.invite.invite_id),
                "scope": scope.encode(),
            },
            sort_keys=True,
        )
    )


def _join(args: argparse.Namespace, settings: Settings) -> None:
    from tableforge.transport.join import JoinError, default_operator_dir, join_with_code

    try:
        report = join_with_code(
            args.invite, settings.data_dir, default_operator_dir(), _node_id_for
        )
    except JoinError as error:
        raise SystemExit(f"join failed: {error}") from error
    print(json.dumps(report, sort_keys=True, indent=2))


def _node_id_for(data_dir: Path) -> str:
    return str(NodeIdentityStore(data_dir / "node-id").load_or_create())


def _default_home() -> Path:
    from tableforge.web.cluster import default_home

    return default_home()


def _local_ui(args: argparse.Namespace, settings: Settings) -> None:
    import webbrowser

    import uvicorn

    from tableforge.web.cluster import ClusterSetupError, CoordinatorSupervisor, Layout
    from tableforge.web.local import (
        LocalUiContext,
        WorkerManager,
        create_local_app,
        default_command_prefix,
    )

    layout = Layout(args.home.resolve() if args.home else _default_home())
    data_dir = layout.node
    prefix = default_command_prefix()
    if args.config is not None:
        prefix = [*prefix, "--config", str(args.config.resolve())]
    manager = WorkerManager(data_dir, prefix, use_gpu=args.ui_use_gpu)
    supervisor = CoordinatorSupervisor(layout, prefix)
    if layout.hosts_cluster:
        try:
            supervisor.ensure_running()
            print("cluster service is running", flush=True)
        except ClusterSetupError as error:
            print(f"warning: {error}", file=sys.stderr, flush=True)
    context = LocalUiContext(
        data_dir, layout.operators, manager, _node_id_for, supervisor=supervisor
    )
    url = f"http://127.0.0.1:{args.port}/"
    server = uvicorn.Server(
        uvicorn.Config(
            create_local_app(context),
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
            access_log=False,
        )
    )
    print(f"TableForge: {url}  (Ctrl+C to close)", flush=True)
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    stop = _stop_event()
    thread = threading.Thread(target=server.run, name="tableforge-ui", daemon=True)
    thread.start()
    try:
        while not stop.wait(0.5) and thread.is_alive():
            pass
    finally:
        server.should_exit = True
        manager.stop_all()
        thread.join(10)
    print("TableForge local panel stopped", flush=True)


def _remote(args: argparse.Namespace) -> None:
    from tableforge.transport.control_client import ControlError, RemoteControl, experiment_json
    from tableforge.transport.join import JoinError, OperatorCredentials, default_operator_dir

    try:
        control = RemoteControl(OperatorCredentials.load(default_operator_dir(), args.cluster))
    except JoinError as error:
        raise SystemExit(str(error)) from error
    try:
        if args.remote_command == "whoami":
            me = control.whoami()
            print(
                json.dumps(
                    {
                        "cluster_id": me.cluster_id,
                        "label": me.label,
                        "operator_id": me.operator_id,
                        "role": me.role,
                    },
                    sort_keys=True,
                )
            )
        elif args.remote_command == "presets":
            print("\n".join(control.presets()))
        elif args.remote_command == "nodes":
            for node_view in control.nodes():
                heartbeat = node_view.last_heartbeat_at.ToDatetime().isoformat()
                print(
                    f"{node_view.node_id}\t{node_view.operating_system}"
                    f"\tcpu={node_view.logical_cpu_cores}"
                    f"\tgpus={','.join(node_view.gpus) or '-'}\theartbeat={heartbeat}"
                )
        elif args.remote_command == "checkpoints":
            for item in control.checkpoints(args.agent):
                print(
                    f"{item.checkpoint_id}\t{item.agent_id}\tround={item.round_index}\t{item.status}"
                )
        elif args.remote_command == "matrix":
            matrix = control.matrix(args.experiment_id)
            if matrix is None:
                raise SystemExit("matchup matrix is not available yet")
            if args.as_json:
                print(json.dumps(matrix, sort_keys=True))
            else:
                print(
                    _format_matrix(
                        {
                            row: {column: MatrixCell(**cell) for column, cell in cells.items()}
                            for row, cells in matrix.items()
                        }
                    )
                )
        else:
            command = args.remote_experiment_command
            if command == "list":
                for view in control.list_experiments():
                    print(f"{view.experiment_id}\t{view.preset}\t{view.status}")
            elif command == "create":
                print(control.create(args.preset).experiment_id)
            elif command == "start":
                view = control.start(args.experiment_id)
                print(f"{view.experiment_id}\t{view.status}")
            elif command == "stop":
                view = control.stop(args.experiment_id)
                print(f"{view.experiment_id}\t{view.status}")
            else:
                print(json.dumps(experiment_json(control.get(args.experiment_id)), sort_keys=True))
    except ControlError as error:
        raise SystemExit(f"{error.code}: {error}") from error


def _metadata_command(args: argparse.Namespace, settings: Settings) -> None:
    from tableforge.composition import open_database
    from tableforge.infrastructure.sql.copy import TargetNotEmptyError, copy_metadata
    from tableforge.infrastructure.sql.migrations import schema_version

    target = open_database(settings)
    try:
        if args.metadata_command == "migrate-schema":
            print(
                json.dumps(
                    {"backend": target.name, "schema_version": schema_version(target)},
                    sort_keys=True,
                )
            )
            return
        source_path: Path = args.source
        if not source_path.is_file():
            raise SystemExit(f"source database not found: {source_path}")
        if target.name != "postgres":
            raise SystemExit("copy-from-sqlite requires metadata.backend = postgres")
        from tableforge.infrastructure.sql import SQLiteDatabase

        source = SQLiteDatabase(source_path)
        try:
            report = copy_metadata(
                source,
                target,
                progress=lambda table, rows: print(
                    json.dumps({"copied": rows, "table": table}), file=sys.stderr
                ),
            )
        except TargetNotEmptyError as error:
            raise SystemExit(str(error)) from error
        print(json.dumps({"rows": report.rows, "total": report.total}, sort_keys=True))
    finally:
        target.close()


def _enroll_node(args: argparse.Namespace) -> None:
    data_dir: Path = args.data_dir
    ca_file: Path | None = args.ca_file
    token_file: Path | None = args.token_file
    if args.bundle is not None:
        token_file = args.bundle / "enrollment-token"
        if ca_file is None and not args.insecure:
            ca_file = args.bundle / "server.crt"
    if ca_file is None and not args.insecure:
        raise SystemExit("--ca-file is required unless --insecure is used")
    for required in (token_file, None if args.insecure else ca_file):
        if required is not None and not required.is_file():
            raise SystemExit(f"file not found: {required}")
    ca = None if args.insecure or ca_file is None else ca_file.read_text(encoding="utf-8")
    token: str = (
        args.token if args.token else (token_file or Path()).read_text(encoding="utf-8").strip()
    )
    if not token:
        raise SystemExit("enrollment token is empty")
    node_id = NodeIdentityStore(data_dir / "node-id").load_or_create()
    credentials = RemoteCredentials(args.coordinator, "", ca)
    session, expires_at = enroll(credentials.channel(), node_id, token)
    RemoteCredentials(
        args.coordinator, session, ca, expires_at=expires_at, issued_at=datetime.now(UTC)
    ).save(data_dir / "coordinator.json")
    print(f"{node_id}\tenrolled\texpires={expires_at.isoformat()}")


def _write_secret(path: Path, value: str) -> None:
    import os
    import secrets as token_secrets

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{token_secrets.token_hex(6)}.tmp")
    temporary.write_text(value + "\n", encoding="utf-8")
    if os.name != "nt":
        temporary.chmod(0o600)
    os.replace(temporary, path)


def _local_worker(service: CoordinatorClient, settings: Settings) -> WorkerRuntime:
    lease_duration = timedelta(seconds=settings.worker.lease_seconds)
    capability = _discover_local_worker(settings.data_dir, settings.worker.use_gpu)
    return WorkerRuntime(
        node_id=capability.node_id,
        capability=capability,
        coordinator=service,
        clock=SystemClock(),
        lease_duration=lease_duration,
        simulation_executor=SimulationExecutor(),
        renew_margin=min(timedelta(seconds=30), lease_duration / 2),
        extra_executors={**_rule_executors(), **_learning_executors(service)},
    )


def _rule_executors() -> dict[str, RegisteredExecutor]:
    from tableforge.domain.tournament import TOURNAMENT_PAYLOAD_TYPE
    from tableforge.worker.runtime import OutputSpec
    from tableforge.worker.tournament import TournamentExecutor

    executor = TournamentExecutor()
    return {
        TOURNAMENT_PAYLOAD_TYPE: RegisteredExecutor(
            executor,
            OutputSpec(executor.output_kind, executor.output_media_type, executor.output_schema),
        )
    }


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
        for gpu in capability.gpus:
            print(
                f"  {gpu.device_id} {gpu.model} | VRAM {_format_bytes(gpu.total_memory_bytes)}"
                f" (free {_format_bytes(gpu.available_memory_bytes)})"
                f" | compute {gpu.compute_capability or 'unknown'}"
                f" | runtimes {','.join(gpu.runtimes) or 'none'}"
            )
        for assessment in assessments:
            reasons = ", ".join(reason.value for reason in assessment.reason_codes)
            if assessment.plan is not None and assessment.plan.adjustments:
                reasons = f"{reasons} ({', '.join(assessment.plan.adjustments)})"
            suffix = f": {reasons}" if reasons else ""
            print(f"{assessment.workload_key:<28} {assessment.status.value.upper()}{suffix}")
        for warning in capability.warnings:
            print(f"Warning: {warning}")
    unhealthy = not any(
        assessment.status in (CompatibilityStatus.SUPPORTED, CompatibilityStatus.DEGRADED)
        for assessment in assessments
    )
    has_limitations = any(
        assessment.status is not CompatibilityStatus.SUPPORTED for assessment in assessments
    )
    if unhealthy:
        return 1
    if has_limitations or capability.warnings:
        return 2
    return 0


def _format_matrix(matrix: dict[str, dict[str, MatrixCell]]) -> str:
    names = list(matrix)
    width = max(len(name) for name in names) + 2
    lines = [
        "row vs column, bb/100 (* = 95% CI excludes 0)",
        " " * width + "".join(f"{name:>{width}}" for name in names),
    ]
    for row in names:
        cells = []
        for column in names:
            cell = matrix[row].get(column)
            if row == column or cell is None:
                cells.append(f"{'-':>{width}}")
            else:
                significant = cell.confidence_low > 0 or cell.confidence_high < 0
                cells.append(f"{cell.bb_per_100:>{width - 1}.1f}{'*' if significant else ' '}")
        lines.append(f"{row:<{width}}" + "".join(cells))
    return "\n".join(lines)


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
