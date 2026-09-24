from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from tableforge.capability.discovery import LocalCapabilityDiscovery
from tableforge.capability.evaluator import assess_compatibility
from tableforge.capability.workloads import BUILTIN_WORKLOADS
from tableforge.domain.capabilities import CompatibilityStatus
from tableforge.domain.ids import NodeId


def main() -> None:
    parser = argparse.ArgumentParser(prog="tableforge")
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--json", action="store_true", dest="as_json")
    doctor.add_argument("--data-path", type=Path, default=Path.cwd())
    args = parser.parse_args()
    if args.command == "doctor":
        exit_code = run_doctor(args.data_path, args.as_json)
        raise SystemExit(exit_code)


def run_doctor(data_path: Path, as_json: bool) -> int:
    capability = LocalCapabilityDiscovery(
        node_id=NodeId("local-doctor"),
        roles=("simulation", "evaluation", "nfsp_learner"),
        storage_path=data_path,
    ).discover()
    assessments = [assess_compatibility(capability, profile) for profile in BUILTIN_WORKLOADS]
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "capability": asdict(capability),
        "workloads": [asdict(assessment) for assessment in assessments],
    }
    if as_json:
        print(json.dumps(report, default=_json_default, sort_keys=True))
    else:
        print(f"Node: {capability.operating_system}/{capability.architecture}")
        print(
            f"CPU: {capability.logical_cpu_cores or 'unknown'} logical cores | "
            f"RAM: {_format_bytes(capability.total_memory_bytes)}"
        )
        print(f"GPU: {len(capability.gpus)} detected")
        for assessment in assessments:
            reasons = ", ".join(reason.value for reason in assessment.reason_codes)
            suffix = f": {reasons}" if reasons else ""
            print(f"{assessment.workload_key:<28} {assessment.status.value.upper()}{suffix}")
        for warning in capability.warnings:
            print(f"Warning: {warning}")
    unhealthy = not any(
        assessment.status is CompatibilityStatus.SUPPORTED for assessment in assessments
    )
    has_limitations = any(
        assessment.status is not CompatibilityStatus.SUPPORTED for assessment in assessments
    )
    if unhealthy:
        return 1
    if has_limitations or capability.warnings:
        return 2
    return 0


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    return f"{value / 1024**3:.1f} GiB"


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


if __name__ == "__main__":
    main()
