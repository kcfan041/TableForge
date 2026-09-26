from __future__ import annotations

import platform
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from tableforge.domain.ids import NodeId
from tableforge.transport.client import GrpcCoordinatorClient
from tableforge.transport.credentials import SessionKeeper
from tableforge.transport.errors import RemoteCoordinatorError
from tableforge.version import check_compatibility
from tableforge.worker.loop import EventLogger, WorkerStoppedError


class RemoteWorkerSession:
    """Lifecycle of one remote worker process: startup, reconnection and session renewal.

    Startup waits for an unreachable coordinator instead of failing, so a worker started
    before the coordinator (or before Tailscale is up) simply joins once it can. Each
    reconnection re-checks protocol compatibility and re-registers the worker slot, since
    the coordinator may have been upgraded or restarted meanwhile.
    """

    def __init__(
        self,
        client: GrpcCoordinatorClient,
        node_id: NodeId,
        credentials_path: Path,
        logger: EventLogger,
        sleep: Callable[[float], None],
        should_stop: Callable[[], bool],
        max_backoff_seconds: float = 60.0,
        label: str | None = None,
    ) -> None:
        self.client = client
        self.node_id = node_id
        self.logger = logger
        self.sleep = sleep
        self.should_stop = should_stop
        self.max_backoff_seconds = max_backoff_seconds
        self.label = (label or platform.node())[:64]
        self.keeper = SessionKeeper(credentials_path, client)
        self._cluster_id = ""
        self._connected = False

    def start(self) -> dict[str, object]:
        backoff = 0.0
        while True:
            try:
                return self._register()
            except RemoteCoordinatorError as error:
                if error.fatal:
                    raise WorkerStoppedError(_hint(error)) from error
                if not error.transient:
                    raise
                if backoff == 0.0:
                    self.logger.emit("coordinator_unreachable", detail=str(error)[:200])
                if self.should_stop():
                    raise WorkerStoppedError(
                        "stopped before the coordinator was reachable"
                    ) from error
                backoff = min(max(1.0, backoff * 2), self.max_backoff_seconds)
                self.sleep(backoff)

    def before_iteration(self) -> None:
        now = datetime.now(UTC)
        try:
            renewed = self.keeper.maybe_renew(now)
        except RemoteCoordinatorError as error:
            if error.fatal:
                raise WorkerStoppedError(_hint(error)) from error
            raise
        if renewed:
            self.logger.emit("session_renewed")

    def _register(self) -> dict[str, object]:
        info = self.client.cluster_info()
        result = check_compatibility(
            info.coordinator_version, info.protocol_version, info.min_protocol_version
        )
        if not result.compatible:
            raise RemoteCoordinatorError("INCOMPATIBLE_VERSION", result.error)
        if result.warning:
            self.logger.emit("version_warning", detail=result.warning)
        self.keeper.maybe_renew(datetime.now(UTC))
        started = self.client.start_worker(self.node_id, self.label)
        self._cluster_id = started.cluster_id
        self._connected = True
        return {
            "cluster_id": started.cluster_id,
            "storage_endpoint": self.client.storage_endpoint or "default",
            "worker_id": started.worker_id,
        }


def _hint(error: RemoteCoordinatorError) -> str:
    if error.code == "UNAUTHENTICATED":
        return (
            "the coordinator rejected this node's credential (revoked or expired); ask the "
            "admin for a new invite and join again with a fresh --data-dir"
        )
    if error.code == "INCOMPATIBLE_VERSION":
        return str(error).split(": ", 1)[-1]
    return str(error)
