from __future__ import annotations

import os
import time
from pathlib import Path

from tableforge.worker.cache import enforce_cache_limit


def write(path: Path, size: int, stamp: float) -> None:
    path.write_bytes(b"x" * size)
    os.utime(path, (stamp, stamp))


def test_cache_removes_least_recently_used_until_under_limit(tmp_path: Path) -> None:
    now = time.time()
    write(tmp_path / "old", 100, now - 300)
    write(tmp_path / "middle", 100, now - 200)
    write(tmp_path / "new", 100, now - 100)
    (tmp_path / "in-flight.part").write_bytes(b"x" * 500)

    report = enforce_cache_limit(tmp_path, 200)

    assert report.removed_files == 1
    assert report.remaining_bytes == 200
    assert not (tmp_path / "old").exists()
    assert (tmp_path / "new").exists()
    assert (tmp_path / "in-flight.part").exists()


def test_cache_within_limit_is_untouched(tmp_path: Path) -> None:
    write(tmp_path / "a", 50, time.time())

    assert enforce_cache_limit(tmp_path, 100).removed_files == 0
    assert enforce_cache_limit(tmp_path / "missing", 0).remaining_bytes == 0
