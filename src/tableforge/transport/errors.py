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
