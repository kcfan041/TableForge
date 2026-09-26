from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tableforge.application.ports import LeaseFencedError
from tableforge.transport.credentials import RemoteCredentials, SessionKeeper
from tableforge.transport.errors import RemoteCoordinatorError
from tableforge.version import check_compatibility
from tableforge.worker.loop import WorkerLoop, WorkerStoppedError

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class Logger:
    events: list[str] = field(default_factory=list)

    def emit(self, event: str, **fields: object) -> None:
        self.events.append(event)


@dataclass
class ScriptedRuntime:
    """A runtime whose run_once replays a script of results or exceptions."""

    script: list[object]
    node_id: str = "node-a"
    calls: int = 0

    def run_once(self) -> object:
        self.calls += 1
        step = self.script.pop(0) if self.script else None
        if isinstance(step, BaseException):
            raise step
        return step


def unavailable() -> RemoteCoordinatorError:
    return RemoteCoordinatorError("UNAVAILABLE", "connection refused")


def test_transient_failures_back_off_and_log_only_transitions() -> None:
    runtime = ScriptedRuntime([unavailable(), unavailable(), unavailable(), None])
    logger = Logger()
    sleeps: list[float] = []
    loop = WorkerLoop(runtime=runtime, logger=logger, sleep=sleeps.append)  # type: ignore[arg-type]

    loop.run(should_stop=lambda: runtime.calls >= 4)

    assert sleeps[:3] == [1.0, 2.0, 4.0]
    assert logger.events.count("coordinator_unreachable") == 1
    assert logger.events.count("coordinator_reconnected") == 1


def test_backoff_is_capped() -> None:
    runtime = ScriptedRuntime([unavailable() for _ in range(10)])
    sleeps: list[float] = []
    loop = WorkerLoop(
        runtime=runtime,  # type: ignore[arg-type]
        logger=Logger(),
        sleep=sleeps.append,
        max_backoff_seconds=8.0,
    )

    loop.run(should_stop=lambda: runtime.calls >= 10)

    assert max(sleeps) == 8.0


def test_lost_lease_is_skipped_not_fatal() -> None:
    runtime = ScriptedRuntime([LeaseFencedError("job-1"), None])
    logger = Logger()
    loop = WorkerLoop(runtime=runtime, logger=logger, sleep=lambda _: None)  # type: ignore[arg-type]

    loop.run(should_stop=lambda: runtime.calls >= 2)

    assert "lease_lost" in logger.events


def test_revoked_credential_stops_with_instructions() -> None:
    runtime = ScriptedRuntime([RemoteCoordinatorError("UNAUTHENTICATED", "invalid session")])
    logger = Logger()
    loop = WorkerLoop(runtime=runtime, logger=logger, sleep=lambda _: None)  # type: ignore[arg-type]

    with pytest.raises(WorkerStoppedError, match="new invite"):
        loop.run()
    assert "worker_stopping" in logger.events


def test_unexpected_errors_still_surface() -> None:
    runtime = ScriptedRuntime([KeyError("bug")])
    loop = WorkerLoop(runtime=runtime, logger=Logger(), sleep=lambda _: None)  # type: ignore[arg-type]

    with pytest.raises(KeyError):
        loop.run()


def test_version_compatibility_rules() -> None:
    same = check_compatibility("1.0", 1, 1, client_protocol=1, client_package="1.0")
    newer_package = check_compatibility("1.1", 1, 1, client_protocol=1, client_package="1.0")
    too_old = check_compatibility("2.0", 3, 2, client_protocol=1, client_package="1.0")
    server_too_old = check_compatibility(
        "0.9", 1, 1, client_protocol=3, client_min_protocol=2, client_package="1.0"
    )
    legacy = check_compatibility("0.1.0", 0, 0, client_protocol=1, client_package="1.0")

    assert same.compatible and not same.warning
    assert newer_package.compatible and "recommended" in newer_package.warning
    assert not too_old.compatible and "git pull" in too_old.error
    assert not server_too_old.compatible and "coordinator" in server_too_old.error
    assert legacy.compatible


@dataclass
class RotatingClient:
    rotations: int = 0
    token: str = ""

    def rotate_session(self) -> tuple[str, datetime]:
        self.rotations += 1
        return f"token-{self.rotations}", NOW + timedelta(days=7 + self.rotations)

    def set_session_token(self, token: str) -> None:
        self.token = token


def credentials(path: Path, issued: datetime, expires: datetime, token: str = "t0") -> None:
    RemoteCredentials(
        "coord:7443", token, None, "cluster", "", expires_at=expires, issued_at=issued
    ).save(path)


def test_session_renews_only_in_the_last_quarter(tmp_path: Path) -> None:
    path = tmp_path / "coordinator.json"
    credentials(path, NOW, NOW + timedelta(days=8))
    client = RotatingClient()
    keeper = SessionKeeper(path, client, check_interval=timedelta(0))  # type: ignore[arg-type]

    assert not keeper.maybe_renew(NOW + timedelta(days=5))
    assert keeper.maybe_renew(NOW + timedelta(days=6, hours=1))

    saved = RemoteCredentials.load(path)
    assert client.rotations == 1
    assert saved.session_token == "token-1"
    assert saved.expires_at == NOW + timedelta(days=8)
    assert saved.cluster_id == "cluster"


def test_second_worker_adopts_renewed_session_without_rotating(tmp_path: Path) -> None:
    path = tmp_path / "coordinator.json"
    credentials(path, NOW, NOW + timedelta(days=8))
    first_client, second_client = RotatingClient(), RotatingClient()
    first = SessionKeeper(path, first_client, check_interval=timedelta(0))  # type: ignore[arg-type]
    second = SessionKeeper(path, second_client, check_interval=timedelta(0))  # type: ignore[arg-type]
    late = NOW + timedelta(days=7)

    assert first.maybe_renew(late)
    assert second.maybe_renew(late)

    assert first_client.rotations == 1
    assert second_client.rotations == 0
    assert second_client.token == "token-1"


def test_credentials_without_expiry_are_renewed(tmp_path: Path) -> None:
    path = tmp_path / "coordinator.json"
    RemoteCredentials("coord:7443", "legacy", None).save(path)
    client = RotatingClient()

    assert SessionKeeper(path, client).maybe_renew(NOW)  # type: ignore[arg-type]
    assert RemoteCredentials.load(path).expires_at is not None


def test_check_interval_limits_file_reads(tmp_path: Path) -> None:
    path = tmp_path / "coordinator.json"
    credentials(path, NOW, NOW + timedelta(days=8))
    client = RotatingClient()
    keeper = SessionKeeper(path, client, check_interval=timedelta(minutes=10))  # type: ignore[arg-type]

    assert not keeper.maybe_renew(NOW)
    credentials(path, NOW, NOW + timedelta(days=8), token="changed-elsewhere")
    assert not keeper.maybe_renew(NOW + timedelta(minutes=5))
    assert keeper.maybe_renew(NOW + timedelta(minutes=11))
    assert client.token == "changed-elsewhere"
