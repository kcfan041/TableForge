from __future__ import annotations

import json

import pytest

torch = pytest.importorskip("torch")

from tableforge.agents.rule import RandomBot  # noqa: E402
from tableforge.domain.learning import NfspInitPayload  # noqa: E402
from tableforge.learning import encoding as v1  # noqa: E402
from tableforge.learning import encoding_v2 as v2  # noqa: E402
from tableforge.learning.bundle import json_bytes, pack_entries, unpack_entries  # noqa: E402
from tableforge.learning.experience import ExperienceShard  # noqa: E402
from tableforge.learning.nfsp import MANIFEST, NfspAgent, NfspConfig  # noqa: E402
from tableforge.learning.training import collect_experience, self_play_train  # noqa: E402
from tableforge.poker.betting import Seat  # noqa: E402
from tableforge.poker.environment import HeadsUpHand  # noqa: E402
from tableforge.poker.runner import HandRunner  # noqa: E402


def config(schema: str, seed: int = 3) -> NfspConfig:
    return NfspConfig(
        hidden_size=32,
        batch_size=32,
        rl_capacity=2_000,
        sl_capacity=2_000,
        seed=seed,
        observation_schema=schema,
    )


def test_v1_remains_the_default() -> None:
    agent = NfspAgent(NfspConfig(hidden_size=16))

    assert agent.encoder.schema == v1.SCHEMA
    assert agent.q_network[0].in_features == v1.FEATURE_SIZE


def test_v2_agent_trains_plays_and_round_trips() -> None:
    agent = NfspAgent(config(v2.SCHEMA))
    report = self_play_train(agent, 200, seed_start=0, train_every=2)

    assert agent.q_network[0].in_features == v2.FEATURE_SIZE
    assert report.train_steps > 0 and report.transitions > 0
    bundle = agent.to_bundle()
    manifest = json.loads(unpack_entries(bundle, frozenset({MANIFEST, *_all_entries()}))[MANIFEST])
    assert manifest["observation_schema"] == v2.SCHEMA
    restored = NfspAgent.from_bundle(bundle)
    assert restored.to_bundle() == bundle
    frozen = NfspAgent.from_bundle(bundle, inference_only=True)
    runner = HandRunner()
    for seed in range(30):
        hand = HeadsUpHand((200, 200), Seat(seed % 2), 1, 2, seed=seed)
        result = runner.run(hand, (frozen, RandomBot(seed)))
        assert sum(result.final_stacks) == 400


def test_checkpoints_from_before_v2_still_load() -> None:
    agent = NfspAgent(config(v1.SCHEMA))
    self_play_train(agent, 100, seed_start=0)
    entries = unpack_entries(agent.to_bundle(), frozenset(_all_entries()))
    manifest = json.loads(entries[MANIFEST])
    del manifest["config"]["observation_schema"]
    entries[MANIFEST] = json_bytes(manifest)

    restored = NfspAgent.from_bundle(pack_entries(entries))

    assert restored.encoder.schema == v1.SCHEMA
    for left, right in zip(
        agent.policy_network.parameters(), restored.policy_network.parameters(), strict=True
    ):
        assert torch.equal(left, right)


def test_manifest_and_config_schema_must_agree() -> None:
    entries = unpack_entries(NfspAgent(config(v2.SCHEMA)).to_bundle(), frozenset(_all_entries()))
    manifest = json.loads(entries[MANIFEST])
    manifest["observation_schema"] = v1.SCHEMA
    entries[MANIFEST] = json_bytes(manifest)

    with pytest.raises(ValueError, match="observation schema"):
        NfspAgent.from_bundle(pack_entries(entries))


def test_experience_shards_record_and_check_their_schema() -> None:
    agent = NfspAgent(config(v2.SCHEMA))
    transitions, samples = collect_experience(agent, 60, seed_start=0)
    shard = ExperienceShard("a" * 64, 0, 60, tuple(transitions), tuple(samples), v2.SCHEMA)

    restored = ExperienceShard.from_bytes(shard.to_bytes())

    assert restored.observation_schema == v2.SCHEMA
    assert len(restored.transitions) == len(shard.transitions)
    for stored, original in zip(restored.transitions, shard.transitions, strict=True):
        assert stored.state == pytest.approx(original.state, abs=1e-6)
        assert stored.action == original.action
    mislabeled = ExperienceShard("a" * 64, 0, 60, tuple(transitions), tuple(samples), v1.SCHEMA)
    with pytest.raises(ValueError):
        mislabeled.to_bytes()


def test_init_payload_bytes_are_unchanged_for_v1() -> None:
    legacy = NfspInitPayload("1.0", agent_seed=1, hidden_size=64, batch_size=64)

    assert "observation_schema" not in json.loads(legacy.to_bytes())
    v2_payload = NfspInitPayload("1.0", 1, observation_schema=v2.SCHEMA)
    assert NfspInitPayload.from_bytes(v2_payload.to_bytes()).observation_schema == v2.SCHEMA


def _all_entries() -> set[str]:
    from tableforge.learning.nfsp import BUNDLE_ENTRIES

    return set(BUNDLE_ENTRIES)
