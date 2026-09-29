from __future__ import annotations

import random

import numpy as np
import pytest

from tableforge.learning.buffers import CircularBuffer, PolicySample, ReservoirBuffer, Transition
from tableforge.learning.memory import PolicyReservoir, TransitionMemory

FEATURES = 7
ACTIONS = 5


def transition(index: int, terminal: bool) -> Transition:
    state = tuple(float(index * 10 + feature) / 3 for feature in range(FEATURES))
    mask = tuple((index + action) % 3 != 0 for action in range(ACTIONS))
    return Transition(
        state,
        mask,
        index % ACTIONS,
        index * 0.37 - 5,
        None if terminal else tuple(value + 1 for value in state),
        None if terminal else mask[::-1],
    )


@pytest.mark.parametrize("pattern", ["all_terminal", "mixed", "none_terminal"])
def test_transition_memory_matches_the_object_buffer(pattern: str) -> None:
    objects: CircularBuffer[Transition] = CircularBuffer(50)
    memory = TransitionMemory(50, FEATURES, ACTIONS)
    left, right = random.Random(3), random.Random(3)
    for index in range(137):
        terminal = pattern == "all_terminal" or (pattern == "mixed" and index % 2 == 0)
        item = transition(index, terminal)
        objects.add(item)
        memory.add(item)
        if index % 9 == 0 and len(objects) >= 8:
            expected = objects.sample(8, left)
            batch = memory.sample(8, right)
            assert np.array_equal(
                batch.states, np.asarray([item.state for item in expected], dtype=np.float32)
            )
            assert list(batch.actions) == [item.action for item in expected]
            assert list(batch.rewards) == [item.reward for item in expected]
            assert list(batch.done) == [item.next_state is None for item in expected]
            for row, item in enumerate(expected):
                if item.next_state is None:
                    assert not batch.next_states[row].any() and batch.next_masks[row].all()
                else:
                    assert np.array_equal(
                        batch.next_states[row], np.asarray(item.next_state, dtype=np.float32)
                    )
                    assert tuple(batch.next_masks[row]) == item.next_mask
    assert len(memory) == len(objects) == 50
    assert memory.position == objects.position
    assert [item.action for item in memory.items()] == [item.action for item in objects.items]
    memory.clear()
    assert len(memory) == 0 and memory.position == 0


def test_policy_reservoir_matches_the_object_reservoir_and_round_trips() -> None:
    objects: ReservoirBuffer[PolicySample] = ReservoirBuffer(40)
    reservoir = PolicyReservoir(40, FEATURES, ACTIONS)
    left, right = random.Random(8), random.Random(8)
    for index in range(1_500):
        item = PolicySample(transition(index, True).state, transition(index, True).mask, index % 5)
        objects.add(item, left)
        reservoir.add(item, right)
    assert reservoir.seen == objects.seen == 1_500
    assert [item.action for item in reservoir.items()] == [item.action for item in objects.items]
    batch = reservoir.sample(10, right)
    expected = objects.sample(10, left)
    assert list(batch.actions) == [item.action for item in expected]

    states, masks, actions = reservoir.arrays()
    copy = PolicyReservoir(40, FEATURES, ACTIONS)
    copy.load(states, masks, actions, reservoir.seen)
    assert [item.state for item in copy.items()] == [item.state for item in reservoir.items()]
    assert copy.seen == 1_500
    with pytest.raises(ValueError):
        PolicyReservoir(2, FEATURES, ACTIONS).load(states, masks, actions, 1)


def test_memory_grows_only_as_needed() -> None:
    memory = TransitionMemory(1_000_000, FEATURES, ACTIONS)
    for index in range(10):
        memory.add(transition(index, True))

    assert memory._states.shape[0] == 1_024
    assert memory._next_states is None
    memory.add(transition(10, False))
    assert memory._next_states is not None and memory._next_states.shape[0] == 1_024
