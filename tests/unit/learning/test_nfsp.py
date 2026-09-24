from __future__ import annotations

import random
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from tableforge.agents.rule import CallBot, RandomBot  # noqa: E402
from tableforge.learning.actions import (  # noqa: E402
    ACTION_COUNT,
    AbstractAction,
    ActionTranslator,
)
from tableforge.learning.buffers import CircularBuffer, ReservoirBuffer  # noqa: E402
from tableforge.learning.encoding import FEATURE_SIZE, encode  # noqa: E402
from tableforge.learning.nfsp import NfspAgent, NfspConfig  # noqa: E402
from tableforge.learning.training import evaluate, self_play_train  # noqa: E402
from tableforge.poker.betting import IllegalActionError, LegalActions, Seat  # noqa: E402
from tableforge.poker.environment import HeadsUpHand  # noqa: E402
from tableforge.poker.runner import HandRunner  # noqa: E402


def small_config(seed: int = 1) -> NfspConfig:
    return NfspConfig(
        hidden_size=32, batch_size=32, rl_capacity=2_000, sl_capacity=2_000, seed=seed
    )


def test_encoding_has_fixed_size_and_hides_opponent_cards() -> None:
    first = HeadsUpHand((200, 200), Seat.PLAYER_0, 1, 2, seed=1)
    second = HeadsUpHand((200, 200), Seat.PLAYER_0, 1, 2, seed=1)
    second.hole_cards = (second.hole_cards[0], first.hole_cards[0])

    encoded = encode(first.observe(Seat.PLAYER_0), 2)

    assert len(encoded) == FEATURE_SIZE
    assert encoded == encode(second.observe(Seat.PLAYER_0), 2)


def test_encoding_hides_future_board() -> None:
    hand = HeadsUpHand((200, 200), Seat.PLAYER_0, 1, 2, seed=5)
    before = encode(hand.observe(Seat.PLAYER_0), 2)
    hand._flop = hand._flop[::-1]

    assert encode(hand.observe(Seat.PLAYER_0), 2) == before


def test_translated_actions_are_always_legal() -> None:
    translator = ActionTranslator()
    runner_rng = random.Random(3)
    for seed in range(200):
        hand = HeadsUpHand((200, 50 + seed), Seat(seed % 2), 1, 2, seed=seed)
        while not hand.complete:
            actor = hand.betting.current_actor
            assert actor is not None
            observation = hand.observe(actor)
            legal = observation.legal_actions
            assert legal is not None
            mask = translator.legal_mask(legal)
            choice = AbstractAction(
                runner_rng.choice([index for index, allowed in enumerate(mask) if allowed])
            )
            me = observation.players[int(actor)]
            action = translator.to_action(
                choice, legal, observation.pot, observation.current_bet, me.street_contribution
            )
            try:
                hand.act(actor, action)
            except IllegalActionError as error:
                pytest.fail(f"{choice.name} produced illegal {action}: {error}")


def test_illegal_abstract_action_is_rejected() -> None:
    legal = LegalActions(True, False, 2, None, None, False)

    with pytest.raises(ValueError):
        ActionTranslator().to_action(AbstractAction.RAISE_POT, legal, 3, 2, 1)
    assert ActionTranslator().legal_mask(legal) == (True, True, False, False, False)
    assert len(ActionTranslator().legal_mask(legal)) == ACTION_COUNT


def test_reservoir_buffer_is_uniform_and_bounded() -> None:
    rng = random.Random(0)
    counts = [0] * 10
    for _ in range(2_000):
        buffer: ReservoirBuffer[int] = ReservoirBuffer(5)
        for value in range(10):
            buffer.add(value, rng)
        for value in buffer.items:
            counts[value] += 1
    assert len(buffer) == 5
    assert all(800 < count < 1200 for count in counts)


def test_circular_buffer_overwrites_oldest() -> None:
    buffer: CircularBuffer[int] = CircularBuffer(3)
    for value in range(5):
        buffer.add(value)
    assert sorted(buffer.items) == [2, 3, 4]


def test_training_is_deterministic_for_fixed_seed() -> None:
    first = NfspAgent(small_config())
    second = NfspAgent(small_config())

    first_report = self_play_train(first, 200, seed_start=0)
    second_report = self_play_train(second, 200, seed_start=0)

    assert first_report == second_report
    for left, right in zip(
        first.policy_network.parameters(), second.policy_network.parameters(), strict=True
    ):
        assert torch.equal(left, right)


def test_checkpoint_round_trip_preserves_policy(tmp_path: Path) -> None:
    agent = NfspAgent(small_config())
    self_play_train(agent, 150, seed_start=0)

    agent.save(tmp_path)
    restored = NfspAgent.load_inference(tmp_path)

    for left, right in zip(
        agent.policy_network.parameters(), restored.policy_network.parameters(), strict=True
    ):
        assert torch.equal(left, right)
    assert not restored.training
    assert restored.train_steps == agent.train_steps


def test_full_bundle_resumes_training_identically() -> None:
    original = NfspAgent(small_config())
    self_play_train(original, 150, seed_start=0)
    restored = NfspAgent.from_bundle(original.to_bundle())

    assert restored.to_bundle() == original.to_bundle()
    assert len(restored.sl_buffer) == len(original.sl_buffer)
    assert restored.sl_buffer.seen == original.sl_buffer.seen
    assert len(restored.rl_buffer) == 0

    original = NfspAgent.from_bundle(original.to_bundle())
    self_play_train(original, 100, seed_start=1_000)
    self_play_train(restored, 100, seed_start=1_000)
    for left, right in zip(
        original.policy_network.parameters(), restored.policy_network.parameters(), strict=True
    ):
        assert torch.equal(left, right)
    for left, right in zip(
        original.q_network.parameters(), restored.q_network.parameters(), strict=True
    ):
        assert torch.equal(left, right)


def test_bundle_rejects_unexpected_entries() -> None:
    from tableforge.learning.bundle import pack_entries

    with pytest.raises(ValueError):
        NfspAgent.from_bundle(pack_entries({"manifest.json": b"{}", "evil.py": b""}))


def test_nfsp_agent_completes_hands_legally() -> None:
    agent = NfspAgent(small_config())
    agent.training = False
    runner = HandRunner()
    for seed in range(100):
        hand = HeadsUpHand((200, 200), Seat(seed % 2), 1, 2, seed=seed)
        result = runner.run(hand, (agent, RandomBot(seed)))
        assert sum(result.final_stacks) == 400


def test_q_loss_decreases_with_training() -> None:
    agent = NfspAgent(small_config())
    report = self_play_train(agent, 400, seed_start=0, train_every=1)
    early = [agent._train_q() for _ in range(5)]
    for _ in range(300):
        agent._train_q()
    late = [agent._train_q() for _ in range(5)]

    assert report.transitions > 0
    assert sum(late) < sum(early)


def test_evaluation_reports_confidence_interval() -> None:
    report = evaluate(CallBot(), CallBot, deals=50, seed_start=0)

    assert report.hands == 100
    assert report.confidence_low <= report.bb_per_100 <= report.confidence_high
    assert report.bb_per_100 == pytest.approx(0.0, abs=1e-9)
