from __future__ import annotations

import hashlib
import secrets
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import grpc

from tableforge._generated.tableforge.v1 import coordinator_pb2, coordinator_pb2_grpc
from tableforge.domain.artifacts import ArtifactDescriptor, ArtifactKind
from tableforge.domain.ids import new_artifact_id
from tableforge.transport.credentials import RemoteCredentials
from tableforge.transport.tls import certificate_fingerprint
from tableforge.version import check_compatibility


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str
    hint: str = ""
    seconds: float = 0.0


@dataclass(slots=True)
class ConnectionReport:
    target: str
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "target": self.target,
            "checks": [
                {
                    "detail": check.detail,
                    "hint": check.hint,
                    "name": check.name,
                    "ok": check.ok,
                    "seconds": round(check.seconds, 3),
                }
                for check in self.checks
            ],
        }


def _timed(operation: Callable[[], str]) -> tuple[bool, str, float]:
    started = time.perf_counter()
    try:
        detail = operation()
        return True, detail, time.perf_counter() - started
    except Exception as error:
        return False, _describe(error), time.perf_counter() - started


def _describe(error: Exception) -> str:
    if isinstance(error, grpc.RpcError):
        code = error.code() if hasattr(error, "code") else None
        details = error.details() if hasattr(error, "details") else str(error)
        return f"{code.name if code else 'RPC_ERROR'}: {details}"
    return f"{type(error).__name__}: {error}"


def check_connection(data_dir: Path, node_id: str) -> ConnectionReport:
    """Diagnose this node's path to its coordinator one layer at a time.

    Stops at the first failing layer, since later checks would only repeat the same
    failure with a less helpful message.
    """
    path = data_dir / "coordinator.json"
    if not path.is_file():
        report = ConnectionReport(target="")
        report.checks.append(
            Check(
                "credentials",
                False,
                f"{path} not found",
                "this machine has not joined a cluster; run `tableforge join <invite>`",
            )
        )
        return report
    credentials = RemoteCredentials.load(path)
    report = ConnectionReport(target=credentials.target)
    host, _, port = credentials.target.rpartition(":")
    host = host.strip("[]")

    ok, detail, seconds = _timed(lambda: _resolve(host))
    report.checks.append(
        Check(
            "dns",
            ok,
            detail,
            "" if ok else "the coordinator name does not resolve; is Tailscale (MagicDNS) up?",
            seconds,
        )
    )
    if not ok:
        return report

    ok, detail, seconds = _timed(lambda: _tcp(host, int(port)))
    report.checks.append(
        Check(
            "tcp",
            ok,
            detail,
            ""
            if ok
            else f"nothing answers on {credentials.target}; check that Tailscale is connected on "
            "both machines, the coordinator is running, and its firewall allows this port",
            seconds,
        )
    )
    if not ok:
        return report

    if credentials.ca_certificate:
        expected = certificate_fingerprint(credentials.ca_certificate.encode())

        def tls() -> str:
            import ssl

            pem = ssl.get_server_certificate((host, int(port)), timeout=10).encode()
            actual = certificate_fingerprint(pem)
            if actual != expected:
                raise RuntimeError(
                    f"certificate changed (saved {expected[:16]}…, now {actual[:16]}…)"
                )
            return f"certificate matches ({expected[:16]}…)"

        ok, detail, seconds = _timed(tls)
        report.checks.append(
            Check(
                "tls",
                ok,
                detail,
                ""
                if ok
                else "the coordinator certificate was regenerated or this is another machine; "
                "ask for a new invite and join again with a fresh --data-dir",
                seconds,
            )
        )
        if not ok:
            return report

    channel = credentials.channel()
    stub = coordinator_pb2_grpc.CoordinatorServiceStub(channel)

    def cluster() -> str:
        info = stub.GetClusterInfo(coordinator_pb2.GetClusterInfoRequest(), timeout=15)
        if credentials.cluster_id and info.cluster_id != credentials.cluster_id:
            raise RuntimeError(
                f"coordinator is cluster {info.cluster_id}, this node joined "
                f"{credentials.cluster_id}"
            )
        return f"cluster {info.cluster_id}, coordinator {info.coordinator_version}"

    ok, detail, seconds = _timed(cluster)
    report.checks.append(
        Check("cluster", ok, detail, "" if ok else "the address points at another cluster", seconds)
    )
    if not ok:
        return report

    def compatibility() -> str:
        info = stub.GetClusterInfo(coordinator_pb2.GetClusterInfoRequest(), timeout=15)
        result = check_compatibility(
            info.coordinator_version, info.protocol_version, info.min_protocol_version
        )
        if not result.compatible:
            raise RuntimeError(result.error)
        return result.warning or f"protocol {info.protocol_version or 1} compatible"

    ok, detail, seconds = _timed(compatibility)
    report.checks.append(
        Check(
            "version",
            ok,
            detail,
            "" if ok else "update TableForge on the side that is too old",
            seconds,
        )
    )
    if not ok:
        return report

    client = credentials.client(data_dir / "artifact-cache")

    def session() -> str:
        started = client.start_worker(type(node_id)(node_id), "doctor")  # type: ignore[arg-type]
        return f"node session valid; storage endpoint {client.storage_endpoint or 'default'}" + (
            f" (worker slot {started.worker_id[:8]}…)"
        )

    ok, detail, seconds = _timed(session)
    report.checks.append(
        Check(
            "session",
            ok,
            detail,
            ""
            if ok
            else "the node credential was revoked or expired; ask for a new invite and join "
            "again with a fresh --data-dir",
            seconds,
        )
    )
    if not ok:
        return report

    def storage() -> str:
        payload = f"tableforge-doctor {secrets.token_hex(8)}".encode()
        uploaded = client.upload_artifact(
            ArtifactDescriptor(new_artifact_id(), ArtifactKind.DIAGNOSTIC, "text/plain", "1"),
            payload,
        )
        downloaded, cached = client.download_artifact(
            str(uploaded.descriptor.artifact_id), data_dir / "artifact-cache"
        )
        if hashlib.sha256(cached.read_bytes()).hexdigest() != downloaded.sha256:
            raise RuntimeError("downloaded bytes differ from upload")
        cached.unlink(missing_ok=True)
        mode = "direct to storage" if client._direct else "through the coordinator"  # noqa: SLF001
        return f"uploaded and downloaded {len(payload)} bytes {mode}"

    ok, detail, seconds = _timed(storage)
    report.checks.append(
        Check(
            "storage",
            ok,
            detail,
            ""
            if ok
            else "the storage address offered by the coordinator is not usable from here; "
            "ask the admin to add one this machine can reach (EXTRA_STORAGE_ENDPOINTS)",
            seconds,
        )
    )
    return report


def _resolve(host: str) -> str:
    addresses = sorted({str(item[4][0]) for item in socket.getaddrinfo(host, None)})
    return f"{host} -> {', '.join(addresses)}"


def _tcp(host: str, port: int) -> str:
    with socket.create_connection((host, port), timeout=5):
        return f"connected to {host}:{port}"
