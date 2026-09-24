from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from tableforge.agents.rule import RandomBot
from tableforge.poker.betting import Seat
from tableforge.poker.environment import HeadsUpHand
from tableforge.poker.runner import HandRunner


@given(
    stack_0=st.integers(min_value=2, max_value=500),
    stack_1=st.integers(min_value=2, max_value=500),
    hand_seed=st.integers(min_value=0, max_value=2**63 - 1),
    agent_seed_0=st.integers(min_value=0, max_value=2**63 - 1),
    agent_seed_1=st.integers(min_value=0, max_value=2**63 - 1),
    button=st.sampled_from(tuple(Seat)),
)
@settings(max_examples=300, deadline=None)
def test_random_legal_action_traces_preserve_chips(
    stack_0: int,
    stack_1: int,
    hand_seed: int,
    agent_seed_0: int,
    agent_seed_1: int,
    button: Seat,
) -> None:
    hand = HeadsUpHand((stack_0, stack_1), button, 1, 2, seed=hand_seed)

    result = HandRunner().run(
        hand,
        (RandomBot(agent_seed_0), RandomBot(agent_seed_1)),
    )

    assert sum(result.final_stacks) == stack_0 + stack_1
    assert all(stack >= 0 for stack in result.final_stacks)
    assert sum(result.payouts) == hand.betting.pot
    assert all(player.stack >= 0 for player in hand.betting.players.values())


@given(seed=st.integers(min_value=0, max_value=2**63 - 1))
@settings(max_examples=200, deadline=None)
def test_replaying_same_inputs_produces_same_trace(seed: int) -> None:
    first = HeadsUpHand((100, 100), Seat(seed % 2), 1, 2, seed=seed)
    second = HeadsUpHand((100, 100), Seat(seed % 2), 1, 2, seed=seed)

    first_result = HandRunner().run(first, (RandomBot(seed + 1), RandomBot(seed + 2)))
    second_result = HandRunner().run(second, (RandomBot(seed + 1), RandomBot(seed + 2)))

    assert first.action_history == second.action_history
    assert first_result == second_result
