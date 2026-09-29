from __future__ import annotations

import json

SNAPSHOT_SCHEMA = "tableforge.nfsp-dataset-snapshot.v1"


def snapshot_bytes(
    checkpoint_sha256: str, round_index: int, shards: list[tuple[str, str]]
) -> bytes:
    if not shards:
        raise ValueError("dataset snapshot requires at least one shard")
    ordered = sorted(shards, key=lambda shard: shard[1])
    return json.dumps(
        {
            "checkpoint_sha256": checkpoint_sha256,
            "round_index": round_index,
            "schema_version": SNAPSHOT_SCHEMA,
            "shards": [{"artifact_id": item[0], "sha256": item[1]} for item in ordered],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def promotion_decision(result: dict[str, object]) -> tuple[bool, str]:
    head = result.get("head_to_head")
    if not isinstance(head, dict):
        raise ValueError("match result is missing head_to_head")
    high = float(head["confidence_high"])
    mean = float(head["bb_per_100"])
    unit = "ROI points" if head.get("unit") == "roi_points" else "bb/100"
    if high < 0:
        return False, f"significantly worse than active: {mean:.1f} {unit}, CI high {high:.1f}"
    return True, f"not significantly worse than active: {mean:.1f} {unit}, CI high {high:.1f}"
