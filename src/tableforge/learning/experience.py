from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import numpy as np

from tableforge.learning.actions import ACTION_COUNT
from tableforge.learning.buffers import PolicySample, Transition
from tableforge.learning.bundle import (
    array_bytes,
    array_from_bytes,
    json_bytes,
    pack_entries,
    unpack_entries,
)
from tableforge.learning.encoding import FEATURE_SIZE
from tableforge.learning.encoding import SCHEMA as OBSERVATION_SCHEMA

SHARD_SCHEMA = "tableforge.nfsp-experience-shard.v1"
SNAPSHOT_SCHEMA = "tableforge.nfsp-dataset-snapshot.v1"
_SHARD_ENTRIES = frozenset(
    {
        "manifest.json",
        "rl_states.npy",
        "rl_masks.npy",
        "rl_actions.npy",
        "rl_rewards.npy",
        "rl_next_states.npy",
        "rl_next_masks.npy",
        "rl_done.npy",
        "sl_states.npy",
        "sl_masks.npy",
        "sl_actions.npy",
    }
)


@dataclass(frozen=True, slots=True)
class ExperienceShard:
    checkpoint_sha256: str
    seed_start: int
    hands: int
    transitions: tuple[Transition, ...]
    policy_samples: tuple[PolicySample, ...]

    def to_bytes(self) -> bytes:
        rl = self.transitions
        sl = self.policy_samples
        zero_state = (0.0,) * FEATURE_SIZE
        zero_mask = (False,) * ACTION_COUNT
        return pack_entries(
            {
                "manifest.json": json_bytes(
                    {
                        "checkpoint_sha256": self.checkpoint_sha256,
                        "hands": self.hands,
                        "observation_schema": OBSERVATION_SCHEMA,
                        "policy_samples": len(sl),
                        "schema_version": SHARD_SCHEMA,
                        "seed_start": self.seed_start,
                        "transitions": len(rl),
                    }
                ),
                "rl_states.npy": _states([item.state for item in rl]),
                "rl_masks.npy": _masks([item.mask for item in rl]),
                "rl_actions.npy": array_bytes(
                    np.asarray([item.action for item in rl], dtype=np.int8)
                ),
                "rl_rewards.npy": array_bytes(
                    np.asarray([item.reward for item in rl], dtype=np.float32)
                ),
                "rl_next_states.npy": _states(
                    [item.next_state or zero_state for item in rl]
                ),
                "rl_next_masks.npy": _masks([item.next_mask or zero_mask for item in rl]),
                "rl_done.npy": array_bytes(
                    np.asarray([item.next_state is None for item in rl], dtype=np.bool_)
                ),
                "sl_states.npy": _states([item.state for item in sl]),
                "sl_masks.npy": _masks([item.mask for item in sl]),
                "sl_actions.npy": array_bytes(
                    np.asarray([item.action for item in sl], dtype=np.int8)
                ),
            }
        )

    @classmethod
    def from_bytes(cls, content: bytes) -> ExperienceShard:
        entries = unpack_entries(content, _SHARD_ENTRIES)
        if set(entries) != _SHARD_ENTRIES:
            raise ValueError("experience shard is incomplete")
        manifest = json.loads(entries["manifest.json"])
        if manifest.get("schema_version") != SHARD_SCHEMA:
            raise ValueError("unsupported experience shard schema")
        if manifest.get("observation_schema") != OBSERVATION_SCHEMA:
            raise ValueError("experience shard observation schema mismatch")
        arrays = {
            name: array_from_bytes(entries[name]) for name in _SHARD_ENTRIES - {"manifest.json"}
        }
        count = int(manifest["transitions"])
        if any(len(arrays[f"rl_{name}.npy"]) != count for name in (
            "states", "masks", "actions", "rewards", "next_states", "next_masks", "done"
        )):
            raise ValueError("experience shard arrays have inconsistent lengths")
        transitions = tuple(
            Transition(
                state=_row_floats(arrays["rl_states.npy"][index]),
                mask=_row_bools(arrays["rl_masks.npy"][index]),
                action=int(arrays["rl_actions.npy"][index]),
                reward=float(arrays["rl_rewards.npy"][index]),
                next_state=(
                    None
                    if bool(arrays["rl_done.npy"][index])
                    else _row_floats(arrays["rl_next_states.npy"][index])
                ),
                next_mask=(
                    None
                    if bool(arrays["rl_done.npy"][index])
                    else _row_bools(arrays["rl_next_masks.npy"][index])
                ),
            )
            for index in range(count)
        )
        policy_count = int(manifest["policy_samples"])
        policy_samples = tuple(
            PolicySample(
                _row_floats(arrays["sl_states.npy"][index]),
                _row_bools(arrays["sl_masks.npy"][index]),
                int(arrays["sl_actions.npy"][index]),
            )
            for index in range(policy_count)
        )
        return cls(
            checkpoint_sha256=str(manifest["checkpoint_sha256"]),
            seed_start=int(manifest["seed_start"]),
            hands=int(manifest["hands"]),
            transitions=transitions,
            policy_samples=policy_samples,
        )


@dataclass(frozen=True, slots=True)
class ShardReference:
    artifact_id: str
    sha256: str


@dataclass(frozen=True, slots=True)
class DatasetSnapshot:
    checkpoint_sha256: str
    round_index: int
    shards: tuple[ShardReference, ...]

    def __post_init__(self) -> None:
        if not self.shards:
            raise ValueError("dataset snapshot requires at least one shard")
        ordered = tuple(sorted(self.shards, key=lambda shard: shard.sha256))
        if ordered != self.shards:
            object.__setattr__(self, "shards", ordered)

    def to_bytes(self) -> bytes:
        return json_bytes(
            {
                "checkpoint_sha256": self.checkpoint_sha256,
                "round_index": self.round_index,
                "schema_version": SNAPSHOT_SCHEMA,
                "shards": [
                    {"artifact_id": shard.artifact_id, "sha256": shard.sha256}
                    for shard in self.shards
                ],
            }
        )

    @classmethod
    def from_bytes(cls, content: bytes) -> DatasetSnapshot:
        data = json.loads(content)
        if data.get("schema_version") != SNAPSHOT_SCHEMA:
            raise ValueError("unsupported dataset snapshot schema")
        return cls(
            checkpoint_sha256=str(data["checkpoint_sha256"]),
            round_index=int(data["round_index"]),
            shards=tuple(
                ShardReference(str(item["artifact_id"]), str(item["sha256"]))
                for item in data["shards"]
            ),
        )


def verified(content: bytes, sha256: str) -> bytes:
    if hashlib.sha256(content).hexdigest() != sha256:
        raise ValueError("artifact content does not match referenced hash")
    return content


def _states(rows: list[tuple[float, ...]]) -> bytes:
    return array_bytes(np.asarray(rows, dtype=np.float32).reshape(len(rows), FEATURE_SIZE))


def _masks(rows: list[tuple[bool, ...]]) -> bytes:
    return array_bytes(np.asarray(rows, dtype=np.bool_).reshape(len(rows), ACTION_COUNT))


def _row_floats(row: np.ndarray) -> tuple[float, ...]:
    return tuple(float(value) for value in row)


def _row_bools(row: np.ndarray) -> tuple[bool, ...]:
    return tuple(bool(value) for value in row)
