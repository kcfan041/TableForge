from __future__ import annotations

import hashlib

import pytest

pytest.importorskip("torch")

from tableforge.domain.learning import (  # noqa: E402
    NfspActorPayload,
    NfspInitPayload,
    NfspLearnerPayload,
    NfspMatchPayload,
)
from tableforge.learning.experience import (  # noqa: E402
    DatasetSnapshot,
    ExperienceShard,
    ShardReference,
)
from tableforge.learning.iterative import (  # noqa: E402
    NfspActorExecutor,
    NfspInitExecutor,
    NfspLearnerExecutor,
    NfspMatchExecutor,
    promotion_decision,
)
from tableforge.learning.nfsp import NfspAgent, checkpoint_manifest  # noqa: E402


class Store:
    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}

    def put(self, content: bytes) -> tuple[str, str]:
        digest = hashlib.sha256(content).hexdigest()
        self.items[digest[:16]] = content
        return digest[:16], digest

    def fetch(self, artifact_id: str) -> bytes:
        return self.items[artifact_id]


def init(store: Store) -> tuple[str, str]:
    payload = NfspInitPayload("1.0", agent_seed=7, hidden_size=32, batch_size=32)
    return store.put(NfspInitExecutor().execute(payload.to_bytes()))


def actor(store: Store, checkpoint: tuple[str, str], seed: int) -> tuple[str, str]:
    payload = NfspActorPayload("1.0", checkpoint[0], checkpoint[1], 120, seed * 1_000, seed)
    return store.put(NfspActorExecutor(store.fetch).execute(payload.to_bytes()))


def snapshot(
    store: Store, checkpoint: tuple[str, str], shards: list[tuple[str, str]]
) -> tuple[str, str]:
    return store.put(
        DatasetSnapshot(
            checkpoint[1], 1, tuple(ShardReference(*shard) for shard in shards)
        ).to_bytes()
    )


def learner_payload(
    checkpoint: tuple[str, str], data: tuple[str, str], steps: int = 50
) -> bytes:
    return NfspLearnerPayload(
        "1.0", checkpoint[0], checkpoint[1], data[0], data[1], steps, 1
    ).to_bytes()


def test_actor_shard_round_trips_and_is_deterministic() -> None:
    store = Store()
    checkpoint = init(store)
    first = actor(store, checkpoint, 1)
    second = actor(store, checkpoint, 1)

    shard = ExperienceShard.from_bytes(store.fetch(first[0]))

    assert first == second
    assert shard.checkpoint_sha256 == checkpoint[1]
    assert shard.transitions
    assert ExperienceShard.from_bytes(shard.to_bytes()) == shard


def test_snapshot_order_does_not_change_hash() -> None:
    shards = (ShardReference("a", "1" * 64), ShardReference("b", "2" * 64))

    assert (
        DatasetSnapshot("f" * 64, 1, shards).to_bytes()
        == DatasetSnapshot("f" * 64, 1, shards[::-1]).to_bytes()
    )


def test_learner_is_deterministic_and_records_lineage() -> None:
    store = Store()
    checkpoint = init(store)
    data = snapshot(store, checkpoint, [actor(store, checkpoint, 1), actor(store, checkpoint, 2)])
    learner = NfspLearnerExecutor(store.fetch)

    first = learner.execute(learner_payload(checkpoint, data))
    second = learner.execute(learner_payload(checkpoint, data))

    assert first == second
    manifest = checkpoint_manifest(first)
    assert manifest["train_steps"] == 50
    assert manifest["metadata"]["parent_sha256"] == checkpoint[1]
    assert manifest["metadata"]["snapshot_sha256"] == data[1]
    assert len(NfspAgent.from_bundle(first).sl_buffer) > 0


def test_learner_rejects_shard_from_other_checkpoint() -> None:
    store = Store()
    checkpoint = init(store)
    other = store.put(
        NfspInitExecutor().execute(NfspInitPayload("1.0", agent_seed=99, hidden_size=32).to_bytes())
    )
    data = snapshot(store, checkpoint, [actor(store, other, 1)])

    with pytest.raises(ValueError, match="different checkpoint"):
        NfspLearnerExecutor(store.fetch).execute(learner_payload(checkpoint, data))


def test_learner_rejects_tampered_artifact() -> None:
    store = Store()
    checkpoint = init(store)
    shard = actor(store, checkpoint, 1)
    data = snapshot(store, checkpoint, [shard])
    store.items[shard[0]] = store.items[shard[0]] + b"tampered"

    with pytest.raises(ValueError, match="does not match"):
        NfspLearnerExecutor(store.fetch).execute(learner_payload(checkpoint, data))


def test_match_reports_head_to_head_and_references() -> None:
    store = Store()
    checkpoint = init(store)
    payload = NfspMatchPayload(
        "1.0",
        checkpoint[0],
        checkpoint[1],
        checkpoint[0],
        checkpoint[1],
        seed_start=0,
        min_deals=20,
        max_deals=60,
        batch_deals=20,
        target_width=1_000_000.0,
        reference_deals=10,
    )

    import json

    result = json.loads(NfspMatchExecutor(store.fetch).execute(payload.to_bytes()))

    assert result["head_to_head"]["hands"] == 40
    assert set(result["references"]) == {"call", "random"}


@pytest.mark.parametrize(
    ("mean", "high", "promote"),
    [(-50.0, -1.0, False), (-50.0, 0.0, True), (10.0, 40.0, True)],
)
def test_promotion_rule_rejects_only_significantly_worse(
    mean: float, high: float, promote: bool
) -> None:
    decision, reason = promotion_decision(
        {"head_to_head": {"bb_per_100": mean, "confidence_high": high}}
    )

    assert decision is promote
    assert reason
