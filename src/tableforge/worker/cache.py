from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CacheReport:
    removed_files: int
    removed_bytes: int
    remaining_bytes: int


def enforce_cache_limit(directory: Path, max_bytes: int) -> CacheReport:
    if max_bytes < 0:
        raise ValueError("max_bytes cannot be negative")
    if not directory.exists():
        return CacheReport(0, 0, 0)
    entries = []
    for path in directory.iterdir():
        if not path.is_file() or path.suffix == ".part":
            continue
        stat = path.stat()
        entries.append((stat.st_atime, stat.st_mtime, path, stat.st_size))
    total = sum(entry[3] for entry in entries)
    removed_files = 0
    removed_bytes = 0
    for _, _, path, size in sorted(entries, key=lambda entry: (entry[0], entry[1], entry[2].name)):
        if total <= max_bytes:
            break
        try:
            os.remove(path)
        except FileNotFoundError:
            continue
        total -= size
        removed_files += 1
        removed_bytes += size
    return CacheReport(removed_files, removed_bytes, total)
