"""NFSP experiments stay byte-identical across refactors (A0 and later).

`tests/fixtures/nfsp_fingerprint.json` was recorded with `scripts/algorithm_baseline.py`
before the algorithm interface was introduced, on Windows. There every job payload, output,
checkpoint and training curve must match. Elsewhere PyTorch rounds floating point
differently, so trained checkpoints (and the payloads that name them by sha256) differ;
only the job plan (keys, types, statuses) and the round 0 payloads are compared.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("torch")

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import algorithm_baseline  # noqa: E402

FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "nfsp_fingerprint.json").read_text("utf-8"))


@pytest.mark.parametrize("name", sorted(FIXTURE))
def test_nfsp_experiments_are_unchanged(name: str) -> None:
    import torch

    torch.set_num_threads(1)
    index = int(name.split(":", 1)[0])
    preset, settings = algorithm_baseline.EXPERIMENTS[index]
    with tempfile.TemporaryDirectory() as directory:
        rows = json.loads(
            json.dumps(algorithm_baseline.run(preset, settings, Path(directory)), default=str)
        )
    expected = FIXTURE[name]
    jobs = [row for row in rows if "key" in row]
    expected_jobs = [row for row in expected if "key" in row]

    assert [(row["key"], row["type"], row["status"]) for row in jobs] == [
        (row["key"], row["type"], row["status"]) for row in expected_jobs
    ]
    untrained = [row["payload"] for row in jobs if row["key"].startswith("round:0:")]
    assert untrained == [
        row["payload"] for row in expected_jobs if row["key"].startswith("round:0:")
    ]
    if sys.platform == "win32":
        assert rows == expected
