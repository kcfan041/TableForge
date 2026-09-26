from __future__ import annotations

import json
import os
import secrets
import socket
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import grpc

from tableforge._generated.tableforge.v1 import coordinator_pb2, coordinator_pb2_grpc
from tableforge.transport import convert
from tableforge.transport.client import create_channel
from tableforge.transport.invite import InviteCode
from tableforge.transport.tls import certificate_fingerprint
from tableforge.version import check_compatibility

CLUSTER_FILE = "cluster.json"


class JoinError(Exception):
    """A join step failed; the message says which step and what to check."""


def fetch_server_certificate(address: str, timeout: float = 10.0) -> bytes:
    """Read the certificate the coordinator presents, without trusting it yet."""
    host, _, port = address.rpartition(":")
    host = host.strip("[]")
    try:
        pem = ssl.get_server_certificate((host, int(port)), timeout=timeout)
    except (OSError, ssl.SSLError, ValueError) as error:
        raise JoinError(
            f"cannot reach the coordinator at {address}: {error}. "
            "Check that Tailscale (or your network) is connected and the address is right."
        ) from error
    return pem.encode()


def verify_certificate(pem: bytes, expected_fingerprint: str) -> None:
    actual = certificate_fingerprint(pem)
    if not secrets.compare_digest(actual, expected_fingerprint):
        raise JoinError(
            "the coordinator's certificate does not match the invite "
            f"(expected {expected_fingerprint[:16]}…, got {actual[:16]}…). "
            "You may be connected to the wrong machine, or the coordinator certificate "
            "was regenerated; ask for a new invite."
        )


def probe_storage(url: str, timeout: float = 5.0) -> bool:
    """True if an S3-compatible endpoint answers at all (any HTTP status counts)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def choose_storage_endpoint(
    settings: coordinator_pb2.NodeSettings,
    probe: Callable[[str], bool] | None = None,
) -> tuple[str | None, list[dict[str, object]]]:
    """Return the name of the first reachable endpoint and a report of every probe.

    ``None`` means no candidates were offered (e.g. local storage streamed through the
    coordinator), which needs no storage configuration on the node.
    """
    probe = probe or probe_storage
    report: list[dict[str, object]] = []
    candidates = sorted(settings.storage_endpoints, key=lambda item: item.priority)
    for candidate in candidates:
        reachable = probe(candidate.url)
        report.append({"name": candidate.name, "reachable": reachable, "url": candidate.url})
        if reachable:
            return candidate.name, report
    if candidates:
        raise JoinError(
            "none of the coordinator's storage addresses are reachable from this machine: "
            + ", ".join(f"{item['name'] or 'default'}={item['url']}" for item in report)
            + ". Ask the coordinator admin to add an address this machine can reach "
            "(EXTRA_STORAGE_ENDPOINTS)."
        )
    return None, report


@dataclass(frozen=True, slots=True)
class JoinResult:
    cluster_id: str
    scope: str
    node_id: str | None
    storage_endpoint: str | None
    storage_report: list[dict[str, object]]
    operator_role: str | None
    operator_file: Path | None
    session_expires_at: datetime | None
    version_warning: str = ""


def join(
    invite: InviteCode,
    node_id: str | None,
    node_dir: Path,
    operator_dir: Path,
) -> JoinResult:
    pem = fetch_server_certificate(invite.address)
    verify_certificate(pem, invite.fingerprint)
    ca = pem.decode()
    channel = create_channel(invite.address, pem)
    stub = coordinator_pb2_grpc.CoordinatorServiceStub(channel)
    try:
        info = stub.GetClusterInfo(coordinator_pb2.GetClusterInfoRequest(), timeout=15)
    except grpc.RpcError as error:
        raise JoinError(f"TLS connection to the coordinator failed: {error.details()}") from error
    if info.cluster_id != invite.cluster_id:
        raise JoinError(
            f"this coordinator belongs to cluster {info.cluster_id}, but the invite is for "
            f"{invite.cluster_id}; ask for an invite for this cluster"
        )
    compatibility = check_compatibility(
        info.coordinator_version, info.protocol_version, info.min_protocol_version
    )
    if not compatibility.compatible:
        raise JoinError(compatibility.error)
    try:
        response = stub.RedeemInvite(
            coordinator_pb2.RedeemInviteRequest(
                invite_secret=invite.secret,
                node_id=(node_id or "") if invite.scope.node else "",
            ),
            timeout=30,
        )
    except grpc.RpcError as error:
        raise JoinError(f"the invite was rejected: {error.details()}") from error

    storage_name: str | None = None
    storage_report: list[dict[str, object]] = []
    session_expiry: datetime | None = None
    if response.session_token:
        storage_name, storage_report = choose_storage_endpoint(response.node_settings)
        from tableforge.transport.credentials import RemoteCredentials

        session_expiry = convert.timestamp_from_proto(response.session_expires_at)
        RemoteCredentials(
            invite.address,
            response.session_token,
            ca,
            cluster_id=response.cluster_id,
            storage_endpoint=storage_name or "",
            expires_at=session_expiry,
            issued_at=datetime.now(UTC),
        ).save(node_dir / "coordinator.json")

    operator_file: Path | None = None
    if response.operator_token:
        operator_file = OperatorCredentials(
            cluster_id=response.cluster_id,
            address=invite.address,
            ca_certificate=ca,
            operator_id=response.operator_id,
            token=response.operator_token,
            role=response.operator_role,
        ).save(operator_dir)

    return JoinResult(
        version_warning=compatibility.warning,
        cluster_id=response.cluster_id,
        scope=response.scope,
        node_id=node_id if response.session_token else None,
        storage_endpoint=storage_name,
        storage_report=storage_report,
        operator_role=response.operator_role or None,
        operator_file=operator_file,
        session_expires_at=session_expiry,
    )


@dataclass(frozen=True, slots=True)
class OperatorCredentials:
    """Credential for the remote control plane, stored per cluster outside node data."""

    cluster_id: str
    address: str
    ca_certificate: str
    operator_id: str
    token: str
    role: str

    def save(self, directory: Path) -> Path:
        path = directory / self.cluster_id / "operator.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "address": self.address,
                    "ca_certificate": self.ca_certificate,
                    "cluster_id": self.cluster_id,
                    "operator_id": self.operator_id,
                    "role": self.role,
                    "token": self.token,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, path)
        (directory / "default").write_text(self.cluster_id, encoding="utf-8")
        return path

    @classmethod
    def load(cls, directory: Path, cluster_id: str | None = None) -> OperatorCredentials:
        if cluster_id is None:
            default = directory / "default"
            if not default.is_file():
                raise JoinError(
                    "no operator credential found; run `tableforge join <invite>` with an "
                    "invite that grants viewer or operator"
                )
            cluster_id = default.read_text(encoding="utf-8").strip()
        path = directory / cluster_id / "operator.json"
        if not path.is_file():
            raise JoinError(f"no operator credential for cluster {cluster_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            cluster_id=data["cluster_id"],
            address=data["address"],
            ca_certificate=data["ca_certificate"],
            operator_id=data["operator_id"],
            token=data["token"],
            role=data["role"],
        )

    def channel(self) -> grpc.Channel:
        return create_channel(self.address, self.ca_certificate.encode())


def default_operator_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "TableForge" / "clusters"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "tableforge" / "clusters"


def reachable(address: str, timeout: float = 5.0) -> bool:
    host, _, port = address.rpartition(":")
    try:
        with socket.create_connection((host.strip("[]"), int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False
