from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import grpc

from tableforge.transport.client import GrpcCoordinatorClient, create_channel


@dataclass(frozen=True, slots=True)
class RemoteCredentials:
    """A node's credential for one coordinator. Never an operator credential."""

    target: str
    session_token: str
    ca_certificate: str | None
    cluster_id: str = ""
    storage_endpoint: str = ""
    expires_at: datetime | None = None
    issued_at: datetime | None = None

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "ca_certificate": self.ca_certificate,
                    "cluster_id": self.cluster_id,
                    "expires_at": self.expires_at.isoformat() if self.expires_at else None,
                    "issued_at": self.issued_at.isoformat() if self.issued_at else None,
                    "session_token": self.session_token,
                    "storage_endpoint": self.storage_endpoint,
                    "target": self.target,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        if os.name != "nt":
            temporary.chmod(0o600)
        _replace_with_retry(temporary, path)

    @classmethod
    def load(cls, path: Path) -> RemoteCredentials:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            data["target"],
            data["session_token"],
            data["ca_certificate"],
            data.get("cluster_id", ""),
            data.get("storage_endpoint", ""),
            _timestamp(data.get("expires_at")),
            _timestamp(data.get("issued_at")),
        )

    def with_session(
        self, session_token: str, expires_at: datetime | None = None, now: datetime | None = None
    ) -> RemoteCredentials:
        return replace(
            self,
            session_token=session_token,
            expires_at=expires_at,
            issued_at=now or datetime.now(UTC),
        )

    def needs_renewal(self, now: datetime, fraction: float = 0.25) -> bool:
        """True once less than ``fraction`` of the session lifetime remains.

        Credentials saved before expiry was recorded are renewed immediately so that they
        gain an expiry.
        """
        if self.expires_at is None or self.issued_at is None:
            return True
        lifetime = self.expires_at - self.issued_at
        return self.expires_at - now <= lifetime * fraction

    def channel(self) -> grpc.Channel:
        ca = self.ca_certificate.encode() if self.ca_certificate else None
        return create_channel(self.target, ca)

    def client(
        self,
        cache_dir: Path | None = None,
        cache_max_bytes: int | None = None,
        storage_endpoint: str | None = None,
    ) -> GrpcCoordinatorClient:
        return GrpcCoordinatorClient(
            self.channel(),
            self.session_token,
            cache_dir=cache_dir,
            cache_max_bytes=cache_max_bytes,
            storage_endpoint=(
                self.storage_endpoint if storage_endpoint is None else storage_endpoint
            ),
        )


class SessionKeeper:
    """Renews a node session before it expires, shared safely by several worker processes.

    All workers of a node read the same credential file. The first one to notice the
    session is getting old takes an exclusive lock, re-reads the file (another worker may
    already have renewed it) and only then rotates the session. Everyone else simply
    adopts the new token from the file.
    """

    def __init__(
        self,
        path: Path,
        client: GrpcCoordinatorClient,
        check_interval: timedelta = timedelta(minutes=10),
    ) -> None:
        self.path = path
        self.client = client
        self.check_interval = check_interval
        self._checked_at: datetime | None = None
        self._token = RemoteCredentials.load(path).session_token

    def maybe_renew(self, now: datetime | None = None) -> bool:
        """Renew or adopt a newer session if due. Returns True when the token changed."""
        now = now or datetime.now(UTC)
        if self._checked_at is not None and now - self._checked_at < self.check_interval:
            return False
        self._checked_at = now
        current = RemoteCredentials.load(self.path)
        if current.session_token != self._token:
            self._adopt(current.session_token)
            return True
        if not current.needs_renewal(now):
            return False
        with _file_lock(self.path.with_name(self.path.name + ".lock")):
            current = RemoteCredentials.load(self.path)
            if current.session_token != self._token:
                self._adopt(current.session_token)
                return True
            if not current.needs_renewal(now):
                return False
            token, expires_at = self.client.rotate_session()
            current.with_session(token, expires_at, now).save(self.path)
            self._token = token
            return True

    def _adopt(self, token: str) -> None:
        self.client.set_session_token(token)
        self._token = token


@contextmanager
def _file_lock(path: Path, timeout: float = 30.0) -> Iterator[None]:
    deadline = time.monotonic() + timeout
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                stale = time.time() - path.stat().st_mtime > 120
            except FileNotFoundError:
                continue
            if stale:
                path.unlink(missing_ok=True)
                continue
            if time.monotonic() > deadline:
                raise TimeoutError(f"could not lock {path}") from None
            time.sleep(0.05)
    try:
        os.write(descriptor, str(os.getpid()).encode())
        os.close(descriptor)
        yield
    finally:
        path.unlink(missing_ok=True)


def _replace_with_retry(source: Path, target: Path) -> None:
    for attempt in range(40):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.025 * (attempt + 1))


def _timestamp(value: object) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None
