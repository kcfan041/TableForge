from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

import grpc

from tableforge.transport.client import GrpcCoordinatorClient, create_channel


@dataclass(frozen=True, slots=True)
class RemoteCredentials:
    target: str
    session_token: str
    ca_certificate: str | None

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "ca_certificate": self.ca_certificate,
                    "session_token": self.session_token,
                    "target": self.target,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(path)

    @classmethod
    def load(cls, path: Path) -> RemoteCredentials:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data["target"], data["session_token"], data["ca_certificate"])

    def channel(self) -> grpc.Channel:
        ca = self.ca_certificate.encode() if self.ca_certificate else None
        return create_channel(self.target, ca)

    def client(
        self,
        cache_dir: Path | None = None,
        cache_max_bytes: int | None = None,
        storage_endpoint: str = "",
    ) -> GrpcCoordinatorClient:
        return GrpcCoordinatorClient(
            self.channel(),
            self.session_token,
            cache_dir=cache_dir,
            cache_max_bytes=cache_max_bytes,
            storage_endpoint=storage_endpoint,
        )
