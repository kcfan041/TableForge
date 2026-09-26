from __future__ import annotations

from tableforge.application.ports import (
    IdempotencyConflictError,
    JobNotFoundError,
    LeaseConflictError,
    LeaseFencedError,
)

ERROR_METADATA_KEY = "tableforge-error"

ERROR_CODES: dict[type[Exception], str] = {
    LeaseFencedError: "LEASE_FENCED",
    LeaseConflictError: "LEASE_CONFLICT",
    JobNotFoundError: "NOT_FOUND",
    IdempotencyConflictError: "IDEMPOTENCY_CONFLICT",
}

EXCEPTIONS_BY_CODE: dict[str, type[Exception]] = {
    code: exception for exception, code in ERROR_CODES.items()
}


class RemoteCoordinatorError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code

    @property
    def transient(self) -> bool:
        """The coordinator or network is temporarily unavailable; retrying may succeed."""
        return self.code in TRANSIENT_CODES

    @property
    def fatal(self) -> bool:
        """This node can no longer work with the coordinator until a person intervenes."""
        return self.code in FATAL_CODES


TRANSIENT_CODES = frozenset(
    {"UNAVAILABLE", "DEADLINE_EXCEEDED", "RESOURCE_EXHAUSTED", "ABORTED", "INTERNAL", "UNKNOWN"}
)
FATAL_CODES = frozenset(
    {"UNAUTHENTICATED", "NODE_MISMATCH", "WORKER_MISMATCH", "INCOMPATIBLE_VERSION"}
)
