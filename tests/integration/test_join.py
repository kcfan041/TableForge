from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import grpc
import pytest

from tableforge._generated.tableforge.v1 import coordinator_pb2
from tableforge.capability.workloads import BUILTIN_WORKLOADS
from tableforge.composition import Repositories, Services, repositories_for
from tableforge.coordinator.experiments import ExperimentService
from tableforge.coordinator.service import CoordinatorService
from tableforge.domain.auth import InviteScope, OperatorRole
from tableforge.domain.capabilities import NodeCapability, RuntimeCapability, StorageCapability
from tableforge.domain.ids import NodeId
from tableforge.infrastructure.local_artifacts import LocalArtifactStore
from tableforge.infrastructure.sql import SQLiteDatabase
from tableforge.settings import Settings
from tableforge.transport.control import ControlServicer
from tableforge.transport.control_client import ControlError, RemoteControl
from tableforge.transport.credentials import RemoteCredentials
from tableforge.transport.invite import InviteCode
from tableforge.transport.join import (
    JoinError,
    OperatorCredentials,
    choose_storage_endpoint,
    join,
)
from tableforge.transport.server import RunningServer, ServerConfig, start_server
from tableforge.transport.tls import TlsMaterial, generate_self_signed
from tableforge.web.views import ClusterViews
from tableforge.worker.runtime import WorkerRuntime
from tableforge.worker.simulation import SimulationExecutor


@dataclass
class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass
class Cluster:
    repositories: Repositories
    service: CoordinatorService
    tls: TlsMaterial
    server: RunningServer
    address: str
    tmp: Path

    def invite(self, scope: InviteScope, fingerprint: str | None = None) -> InviteCode:
        now = datetime.now(UTC)
        _, secret = self.repositories.auth.create_invite(scope, now, timedelta(hours=1), "t")
        return InviteCode(
            cluster_id=str(self.repositories.auth.cluster_id(now)),
            address=self.address,
            fingerprint=fingerprint or self.tls.fingerprint(),
            secret=secret,
            scope=scope,
        )


@pytest.fixture
def cluster(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Cluster]:
    monkeypatch.setattr("tableforge.transport.join.probe_storage", lambda url: url.endswith(":1"))
    repositories = repositories_for(SQLiteDatabase(tmp_path / "coordinator.db"))
    service = CoordinatorService(
        jobs=repositories.jobs,
        artifacts=LocalArtifactStore(tmp_path / "artifacts"),
        workloads=BUILTIN_WORKLOADS,
        nodes=repositories.nodes,
    )
    experiments = ExperimentService(repositories.experiments, service, repositories.checkpoints)
    tls = generate_self_signed(tmp_path / "tls", ())
    clock = Clock()
    views = ClusterViews(
        Services(Settings(data_dir=tmp_path), repositories, service, experiments), clock
    )
    server = start_server(
        service,
        repositories.auth,
        clock,
        "127.0.0.1:0",
        tls,
        config=ServerConfig(
            storage_endpoints=(("unreachable", "http://127.0.0.1:9"), ("", "http://localhost:1"))
        ),
        control=ControlServicer(
            experiments, repositories.checkpoints, repositories.auth, clock
        ).with_views(views),
    )
    try:
        yield Cluster(repositories, service, tls, server, f"127.0.0.1:{server.port}", tmp_path)
    finally:
        server.stop(0)


def capability(node_id: str) -> NodeCapability:
    return NodeCapability(
        schema_version="1.0",
        node_id=NodeId(node_id),
        revision=1,
        operating_system="test",
        architecture="x86_64",
        logical_cpu_cores=4,
        physical_cpu_cores=2,
        total_memory_bytes=8 * 1024**3,
        available_memory_bytes=8 * 1024**3,
        gpus=(),
        storage=(StorageCapability("data", 20 * 1024**3),),
        runtime=RuntimeCapability("3.12", ("cpu",)),
        enabled_roles=("simulation",),
        captured_at=datetime.now(UTC),
    )


def no_storage(settings: coordinator_pb2.NodeSettings) -> coordinator_pb2.NodeSettings:
    return coordinator_pb2.NodeSettings(lease_duration=settings.lease_duration)


def test_join_node_and_operator_then_work_and_control(cluster: Cluster) -> None:
    code = cluster.invite(InviteScope(node=True, role=OperatorRole.OPERATOR))

    result = join(code, "node-a", cluster.tmp / "node", cluster.tmp / "operators")

    assert result.cluster_id == code.cluster_id
    assert result.storage_endpoint == ""
    assert [item["reachable"] for item in result.storage_report] == [False, True]
    assert result.operator_role == "operator"
    node = RemoteCredentials.load(cluster.tmp / "node" / "coordinator.json")
    assert node.cluster_id == code.cluster_id
    assert node.ca_certificate == cluster.tls.certificate_path.read_text(encoding="utf-8")

    control = RemoteControl(OperatorCredentials.load(cluster.tmp / "operators"))
    assert control.whoami().role == "operator"
    experiment = control.create("hu-rule-smoke")
    assert control.start(experiment.experiment_id).status == "running"

    client = node.client(cluster.tmp / "cache", None, "")
    started = client.start_worker(NodeId("node-a"), "test-slot")
    runtime = WorkerRuntime(
        node_id=NodeId("node-a"),
        capability=capability("node-a"),
        coordinator=client,
        clock=Clock(),
        lease_duration=timedelta(minutes=5),
        simulation_executor=SimulationExecutor(),
    )
    lease = runtime.run_once()
    assert lease is not None
    assert lease.worker_id == started.worker_id
    provenance = cluster.repositories.jobs.attempt_provenance(lease.job.spec.job_id)
    assert provenance[0].worker_id == started.worker_id
    assert any(view.node_id == "node-a" for view in control.nodes())
    assert control.get(experiment.experiment_id).job_counts["succeeded"] == 1


def test_cluster_documents_over_grpc_follow_roles(cluster: Cluster) -> None:
    def operator(role: OperatorRole, name: str) -> RemoteControl:
        code = cluster.invite(InviteScope(node=False, role=role))
        join(code, None, cluster.tmp / name, cluster.tmp / f"operators-{name}")
        return RemoteControl(OperatorCredentials.load(cluster.tmp / f"operators-{name}"))

    viewer = operator(OperatorRole.VIEWER, "viewer")
    admin = RemoteControl(
        OperatorCredentials(
            cluster_id=str(cluster.repositories.auth.cluster_id(datetime.now(UTC))),
            address=cluster.address,
            ca_certificate=cluster.tls.certificate_path.read_text(encoding="utf-8"),
            operator_id="",
            token=cluster.repositories.auth.issue_operator(
                OperatorRole.ADMIN, "root", datetime.now(UTC), timedelta(hours=1)
            ).token,
            role="admin",
        )
    )

    overview = viewer.query("overview")
    assert overview["nodes"] == {"total": 0, "online": 0}
    assert any(item["key"] == "6max-rule-smoke" for item in viewer.query("presets"))
    with pytest.raises(ControlError) as denied:
        viewer.command("create_experiment", {"preset": "hu-rule-smoke"})
    assert denied.value.code == "ROLE_REQUIRED"
    with pytest.raises(ControlError):
        viewer.query("invites")

    created = admin.command("create_experiment", {"preset": "6max-rule-smoke"})
    assert created["players"] == 6
    assert (
        admin.command("start_experiment", {"experiment_id": created["experiment_id"]})["status"]
        == "running"
    )
    detail = viewer.query("experiment", created["experiment_id"])
    assert detail["job_counts"] == {"queued": 4}
    assert viewer.query("matrix", created["experiment_id"]) == {
        "available": False,
        "reason": "not_league",
    }
    assert any(event["type"] == "EXPERIMENT_RUNNING" for event in viewer.query("events", "50"))

    invite = admin.command(
        "create_invite", {"node": True, "label": "friend", "address": cluster.address}
    )
    assert InviteCode.decode(invite["invite"]).address == cluster.address
    assert [item["label"] for item in admin.query("invites")].count("friend") == 1
    with pytest.raises(ControlError) as unconfirmed:
        admin.command("create_invite", {"role": "admin", "ttl_hours": 1})
    assert "confirm" in str(unconfirmed.value)
    with pytest.raises(ControlError) as missing:
        viewer.query("experiment", "01a0dd5a-0000-7000-8000-000000000000")
    assert missing.value.code in ("NOT_FOUND", "HTTP_404")
    with pytest.raises(ControlError):
        admin.query("no-such-resource")


def test_one_node_runs_several_worker_slots(cluster: Cluster) -> None:
    code = cluster.invite(InviteScope(node=True, role=None))
    join(code, "node-a", cluster.tmp / "node", cluster.tmp / "operators")
    node = RemoteCredentials.load(cluster.tmp / "node" / "coordinator.json")

    first = node.client().start_worker(NodeId("node-a"), "gpu0").worker_id
    second = node.client().start_worker(NodeId("node-a"), "gpu1").worker_id

    assert first != second
    assert cluster.repositories.auth.worker_node(type(first)(first)) == NodeId("node-a")


def test_wrong_fingerprint_is_refused_before_redeeming(cluster: Cluster) -> None:
    code = cluster.invite(InviteScope(node=True, role=None), fingerprint="0" * 64)

    with pytest.raises(JoinError, match="does not match the invite"):
        join(code, "node-a", cluster.tmp / "node", cluster.tmp / "operators")

    assert not (cluster.tmp / "node" / "coordinator.json").exists()
    assert all(item.redeemed_at is None for item in cluster.repositories.auth.list_invites())


def test_wrong_cluster_is_refused(cluster: Cluster) -> None:
    good = cluster.invite(InviteScope(node=True, role=None))
    code = InviteCode(
        cluster_id="another-cluster",
        address=good.address,
        fingerprint=good.fingerprint,
        secret=good.secret,
        scope=good.scope,
    )

    with pytest.raises(JoinError, match="belongs to cluster"):
        join(code, "node-a", cluster.tmp / "node", cluster.tmp / "operators")


def test_unreachable_coordinator_explains_network(cluster: Cluster) -> None:
    good = cluster.invite(InviteScope(node=True, role=None))
    code = InviteCode(good.cluster_id, "127.0.0.1:9", good.fingerprint, good.secret, good.scope)

    with pytest.raises(JoinError, match="cannot reach the coordinator"):
        join(code, "node-a", cluster.tmp / "node", cluster.tmp / "operators")


def test_reused_invite_is_rejected(cluster: Cluster) -> None:
    code = cluster.invite(InviteScope(node=True, role=None))
    join(code, "node-a", cluster.tmp / "node-a", cluster.tmp / "operators")

    with pytest.raises(JoinError, match="already used"):
        join(code, "node-b", cluster.tmp / "node-b", cluster.tmp / "operators")


def test_viewer_cannot_change_experiments(cluster: Cluster) -> None:
    code = cluster.invite(InviteScope(node=False, role=OperatorRole.VIEWER))
    join(code, None, cluster.tmp / "node", cluster.tmp / "operators")
    control = RemoteControl(OperatorCredentials.load(cluster.tmp / "operators"))

    assert control.list_experiments() == []
    assert "hu-rule-smoke" in control.presets()
    with pytest.raises(ControlError) as error:
        control.create("hu-rule-smoke")
    assert error.value.code == "ROLE_REQUIRED"
    assert not (cluster.tmp / "node" / "coordinator.json").exists()


def test_node_session_cannot_use_control_plane(cluster: Cluster) -> None:
    code = cluster.invite(InviteScope(node=True, role=None))
    join(code, "node-a", cluster.tmp / "node", cluster.tmp / "operators")
    node = RemoteCredentials.load(cluster.tmp / "node" / "coordinator.json")
    as_operator = OperatorCredentials(
        node.cluster_id, node.target, node.ca_certificate or "", "x", node.session_token, "operator"
    )

    with pytest.raises(ControlError) as error:
        RemoteControl(as_operator).list_experiments()
    assert error.value.code == "UNAUTHENTICATED"


def test_operator_token_cannot_act_as_node(cluster: Cluster) -> None:
    code = cluster.invite(InviteScope(node=False, role=OperatorRole.OPERATOR))
    join(code, None, cluster.tmp / "node", cluster.tmp / "operators")
    operator = OperatorCredentials.load(cluster.tmp / "operators")
    impostor = RemoteCredentials(operator.address, operator.token, operator.ca_certificate)

    with pytest.raises(Exception, match="UNAUTHENTICATED|operator credentials"):
        impostor.client().register_node(capability("node-x"), datetime.now(UTC))


def test_worker_slot_of_another_node_is_rejected(cluster: Cluster) -> None:
    for name in ("node-a", "node-b"):
        join(
            cluster.invite(InviteScope(node=True, role=None)),
            name,
            cluster.tmp / name,
            cluster.tmp / "operators",
        )
    node_a = RemoteCredentials.load(cluster.tmp / "node-a" / "coordinator.json").client()
    node_b = RemoteCredentials.load(cluster.tmp / "node-b" / "coordinator.json").client()
    slot_a = node_a.start_worker(NodeId("node-a")).worker_id
    node_b.register_node(capability("node-b"), datetime.now(UTC))
    node_b._worker_id = slot_a  # noqa: SLF001

    with pytest.raises(Exception, match="WORKER_MISMATCH|worker slot"):
        node_b.claim_job(
            NodeId("node-b"), capability("node-b"), datetime.now(UTC), timedelta(minutes=1)
        )


def test_storage_choice_reports_every_candidate() -> None:
    settings = coordinator_pb2.NodeSettings(
        storage_endpoints=[
            coordinator_pb2.StorageEndpoint(name="tailnet", url="http://a", priority=1),
            coordinator_pb2.StorageEndpoint(name="host", url="http://b", priority=0),
        ]
    )

    chosen, report = choose_storage_endpoint(settings, probe=lambda url: url == "http://a")

    assert chosen == "tailnet"
    assert [item["name"] for item in report] == ["host", "tailnet"]
    with pytest.raises(JoinError, match="EXTRA_STORAGE_ENDPOINTS"):
        choose_storage_endpoint(settings, probe=lambda url: False)
    assert choose_storage_endpoint(coordinator_pb2.NodeSettings()) == (None, [])


def test_peer_address_ranks_matching_endpoint_first() -> None:
    config = ServerConfig(
        storage_endpoints=(("", "http://storage:9000"), ("tailnet", "http://100.64.0.1:59000"))
    )

    ranked = config.node_settings("ipv4:100.64.0.1:51234").storage_endpoints

    assert [item.name for item in ranked] == ["tailnet", ""]
    assert [item.name for item in config.node_settings("").storage_endpoints] == ["", "tailnet"]
    _ = grpc


def test_doctor_walks_every_layer(cluster: Cluster) -> None:
    from tableforge.transport.diagnostics import check_connection

    join(
        cluster.invite(InviteScope(node=True, role=None)),
        "node-a",
        cluster.tmp / "node",
        cluster.tmp / "operators",
    )

    report = check_connection(cluster.tmp / "node", "node-a")

    assert report.ok, report.as_dict()
    assert [check.name for check in report.checks] == [
        "dns",
        "tcp",
        "tls",
        "cluster",
        "version",
        "session",
        "storage",
    ]


def test_doctor_stops_at_first_failure_with_a_hint(cluster: Cluster) -> None:
    from tableforge.transport.diagnostics import check_connection

    join(
        cluster.invite(InviteScope(node=True, role=None)),
        "node-a",
        cluster.tmp / "node",
        cluster.tmp / "operators",
    )
    credentials = RemoteCredentials.load(cluster.tmp / "node" / "coordinator.json")
    cluster.repositories.auth.revoke_node(NodeId("node-a"))

    revoked = check_connection(cluster.tmp / "node", "node-a")
    RemoteCredentials("127.0.0.1:9", credentials.session_token, credentials.ca_certificate).save(
        cluster.tmp / "moved" / "coordinator.json"
    )
    unreachable = check_connection(cluster.tmp / "moved", "node-a")
    missing = check_connection(cluster.tmp / "never-joined", "node-a")

    assert revoked.checks[-1].name == "session" and not revoked.checks[-1].ok
    assert "new invite" in revoked.checks[-1].hint
    assert unreachable.checks[-1].name == "tcp" and "Tailscale" in unreachable.checks[-1].hint
    assert missing.checks[0].name == "credentials" and "join" in missing.checks[0].hint
