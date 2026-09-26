from __future__ import annotations

import json
from collections import namedtuple
from pathlib import Path

import pytest

from tableforge.capability import discovery
from tableforge.capability.workloads import BUILTIN_WORKLOADS
from tableforge.cli.main import run_doctor

_Usage = namedtuple("_Usage", "total used free")


def _with_disk(monkeypatch: pytest.MonkeyPatch, free_bytes: int) -> None:
    monkeypatch.setattr(
        discovery.shutil,
        "disk_usage",
        lambda _: _Usage(2 * free_bytes, free_bytes, free_bytes),
    )


def test_doctor_json_has_versioned_capability_and_workloads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_disk(monkeypatch, 500 * 1024**3)

    exit_code = run_doctor(tmp_path, as_json=True)
    report = json.loads(capsys.readouterr().out)

    assert exit_code in (0, 2)
    assert report["schema_version"] == "1.0"
    assert report["capability"]["schema_version"] == "1.0"
    assert len(report["workloads"]) == len(BUILTIN_WORKLOADS)
    assert {item["status"] for item in report["workloads"]} <= {
        "supported",
        "degraded",
        "unsupported",
        "unknown",
    }


def test_doctor_is_unhealthy_when_nothing_can_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_disk(monkeypatch, 1024**2)

    exit_code = run_doctor(tmp_path, as_json=True)
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert all("INSUFFICIENT_DISK" in item["reason_codes"] for item in report["workloads"])
