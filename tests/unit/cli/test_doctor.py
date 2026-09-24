from __future__ import annotations

import json
from pathlib import Path

from tableforge.cli.main import run_doctor


def test_doctor_json_has_versioned_capability_and_workloads(
    tmp_path: Path, capsys: object
) -> None:
    exit_code = run_doctor(tmp_path, as_json=True)
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    report = json.loads(captured.out)

    assert exit_code in (0, 2)
    assert report["schema_version"] == "1.0"
    assert report["capability"]["schema_version"] == "1.0"
    assert len(report["workloads"]) == 3
    assert {item["status"] for item in report["workloads"]} <= {
        "supported",
        "degraded",
        "unsupported",
        "unknown",
    }
