from __future__ import annotations

import hashlib
import urllib.error
import urllib.request

from tableforge.application.ports import SignedTransfer

DIRECT_TIMEOUT = 120.0


class DirectTransferError(Exception):
    def __init__(self, status: int | None, detail: str) -> None:
        super().__init__(f"direct transfer failed ({status}): {detail}")
        self.status = status
        self.retryable = status is None or status >= 500 or status == 429


def put(transfer: SignedTransfer, content: bytes) -> None:
    request = urllib.request.Request(
        transfer.url,
        data=content,
        method=transfer.method,
        headers=dict(transfer.headers),
    )
    _perform(request)


def get(transfer: SignedTransfer, sha256: str, size_bytes: int) -> bytes:
    request = urllib.request.Request(
        transfer.url, method=transfer.method, headers=dict(transfer.headers)
    )
    content = _perform(request)
    if len(content) != size_bytes or hashlib.sha256(content).hexdigest() != sha256:
        raise DirectTransferError(None, "downloaded bytes do not match the artifact hash")
    return content


def _perform(request: urllib.request.Request) -> bytes:
    try:
        with urllib.request.urlopen(request, timeout=DIRECT_TIMEOUT) as response:
            body: bytes = response.read()
            return body
    except urllib.error.HTTPError as error:
        detail = error.read()[:300].decode("utf-8", "replace")
        raise DirectTransferError(error.code, detail) from error
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        raise DirectTransferError(None, str(error)) from error
