from __future__ import annotations

import pytest

from tableforge.agents.protocol import PokerAgent
from tableforge.agents.rule import AggroBot, CallBot, RandomBot, TightBot
from tableforge.poker.betting import Seat
from tableforge.poker.environment import HeadsUpHand
from tableforge.poker.runner import HandRunner


@pytest.mark.parametrize(
    "agents",
    [
        (CallBot(), CallBot()),
        (TightBot(), AggroBot()),
        (RandomBot(1), RandomBot(2)),
        (AggroBot(), CallBot()),
    ],
)
def test_rule_bots_complete_hands_and_conserve_chips(
    agents: tuple[PokerAgent, PokerAgent],
) -> None:
    runner = HandRunner()
    total = 200
    for seed in range(250):
        hand = HeadsUpHand((100, 100), Seat(seed % 2), 1, 2, seed=seed)
        result = runner.run(hand, agents)
        assert sum(result.final_stacks) == total


def test_random_bot_is_reproducible() -> None:
    hand = HeadsUpHand((100, 100), Seat.PLAYER_0, 1, 2, seed=100)
    observation = hand.observe(Seat.PLAYER_0)
    legal = observation.legal_actions
    assert legal is not None
    first = RandomBot("agent-seed")
    second = RandomBot("agent-seed")

    first_actions = [first.act(observation, legal) for _ in range(20)]
    second_actions = [second.act(observation, legal) for _ in range(20)]

    assert first_actions == second_actions


def test_hand_runner_is_deterministic_with_seeded_agents() -> None:
    first_hand = HeadsUpHand((100, 100), Seat.PLAYER_0, 1, 2, seed="hand")
    second_hand = HeadsUpHand((100, 100), Seat.PLAYER_0, 1, 2, seed="hand")

    first_result = HandRunner().run(first_hand, (RandomBot("a"), RandomBot("b")))
    second_result = HandRunner().run(second_hand, (RandomBot("a"), RandomBot("b")))

    assert first_hand.action_history == second_hand.action_history
    assert first_result == second_result
