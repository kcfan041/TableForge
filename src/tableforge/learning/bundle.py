from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Mapping

import numpy as np
from numpy.typing import NDArray

_ZIP_TIME = (2020, 1, 1, 0, 0, 0)


def pack_entries(entries: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, entries[name])
    return buffer.getvalue()


def unpack_entries(content: bytes, allowed: frozenset[str]) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
        unexpected = set(names) - allowed
        if unexpected or len(names) != len(set(names)):
            raise ValueError(f"bundle has unexpected entries: {sorted(unexpected)}")
        return {name: archive.read(name) for name in names}


def array_bytes(array: NDArray[np.generic]) -> bytes:
    buffer = io.BytesIO()
    np.save(buffer, np.ascontiguousarray(array), allow_pickle=False)
    return buffer.getvalue()


def array_from_bytes(content: bytes) -> NDArray[np.generic]:
    loaded = np.load(io.BytesIO(content), allow_pickle=False)
    if not isinstance(loaded, np.ndarray):
        raise ValueError("expected a single array")
    return loaded


def json_bytes(data: object) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
