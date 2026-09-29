"""Replay memories stored as contiguous NumPy arrays.

NFSP keeps hundreds of thousands of transitions (RL memory) and up to a million policy
samples (SL reservoir). Stored as Python objects each 406-feature state costs several
kilobytes; stored as rows of preallocated float32 matrices it costs 1.6 KB. The matrices
grow by doubling up to the capacity, so small runs stay small.

Sampling and replacement use the agent's `random.Random` exactly like the object-based
buffers (`random.sample` over positions, `randrange` for the reservoir), so training is
bit-for-bit identical to storing objects.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from tableforge.learning.buffers import PolicySample, Transition

_INITIAL_ROWS = 1_024


def _grow(array: NDArray[np.generic], rows: int) -> NDArray[np.generic]:
    grown = np.zeros((rows, *array.shape[1:]), dtype=array.dtype)
    grown[: len(array)] = array
    return grown


@dataclass(frozen=True, slots=True)
class TransitionBatch:
    states: NDArray[np.float32]
    masks: NDArray[np.bool_]
    actions: NDArray[np.int64]
    rewards: NDArray[np.float64]
    next_states: NDArray[np.float32]
    next_masks: NDArray[np.bool_]
    done: NDArray[np.bool_]


@dataclass(frozen=True, slots=True)
class PolicyBatch:
    states: NDArray[np.float32]
    masks: NDArray[np.bool_]
    actions: NDArray[np.int64]


class TransitionMemory:
    """Circular RL memory: the newest `capacity` transitions."""

    def __init__(self, capacity: int, features: int, actions: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.features = features
        self.action_count = actions
        self.position = 0
        self._size = 0
        rows = min(capacity, _INITIAL_ROWS)
        self._states = np.zeros((rows, features), dtype=np.float32)
        self._masks = np.zeros((rows, actions), dtype=np.bool_)
        self._actions = np.zeros(rows, dtype=np.int64)
        self._rewards = np.zeros(rows, dtype=np.float64)
        # Next states are allocated only once a transition has one: with one-hand
        # horizons (tournament training) every transition is terminal.
        self._next_states: NDArray[np.float32] | None = None
        self._next_masks = np.ones((rows, actions), dtype=np.bool_)
        self._done = np.zeros(rows, dtype=np.bool_)

    def __len__(self) -> int:
        return self._size

    def clear(self) -> None:
        self._size = 0
        self.position = 0

    def _ensure(self, rows: int) -> None:
        if rows <= len(self._states):
            return
        size = min(self.capacity, max(rows, len(self._states) * 2))
        self._states = _grow(self._states, size)  # type: ignore[assignment]
        self._masks = _grow(self._masks, size)  # type: ignore[assignment]
        self._actions = _grow(self._actions, size)  # type: ignore[assignment]
        self._rewards = _grow(self._rewards, size)  # type: ignore[assignment]
        if self._next_states is not None:
            self._next_states = _grow(self._next_states, size)  # type: ignore[assignment]
        next_masks = np.ones((size, self.action_count), dtype=np.bool_)
        next_masks[: len(self._next_masks)] = self._next_masks
        self._next_masks = next_masks
        self._done = _grow(self._done, size)  # type: ignore[assignment]

    def add(self, item: Transition) -> None:
        if self._size < self.capacity:
            row = self._size
            self._ensure(row + 1)
            self._size += 1
        else:
            row = self.position
        self._states[row] = item.state
        self._masks[row] = item.mask
        self._actions[row] = item.action
        self._rewards[row] = item.reward
        if item.next_state is None:
            self._done[row] = True
            if self._next_states is not None:
                self._next_states[row] = 0.0
            self._next_masks[row] = True
        else:
            self._done[row] = False
            if self._next_states is None:
                self._next_states = np.zeros((len(self._states), self.features), dtype=np.float32)
            self._next_states[row] = item.next_state
            self._next_masks[row] = True if item.next_mask is None else item.next_mask
        self.position = (self.position + 1) % self.capacity

    def sample(self, count: int, rng: random.Random) -> TransitionBatch:
        """Same positions `random.sample(items, count)` would pick from a list."""
        rows = np.asarray(rng.sample(range(self._size), min(count, self._size)), dtype=np.int64)
        next_states = (
            np.zeros((len(rows), self.features), dtype=np.float32)
            if self._next_states is None
            else self._next_states[rows]
        )
        return TransitionBatch(
            self._states[rows],
            self._masks[rows],
            self._actions[rows],
            self._rewards[rows],
            next_states,
            self._next_masks[rows],
            self._done[rows],
        )

    def items(self) -> list[Transition]:
        """The stored transitions as objects (tests and diagnostics)."""
        return [
            Transition(
                tuple(float(value) for value in self._states[row]),
                tuple(bool(value) for value in self._masks[row]),
                int(self._actions[row]),
                float(self._rewards[row]),
                None
                if self._done[row] or self._next_states is None
                else tuple(float(value) for value in self._next_states[row]),
                None if self._done[row] else tuple(bool(value) for value in self._next_masks[row]),
            )
            for row in range(self._size)
        ]


class PolicyReservoir:
    """SL reservoir: a uniform sample of every policy sample ever offered."""

    def __init__(self, capacity: int, features: int, actions: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.features = features
        self.action_count = actions
        self.seen = 0
        self._size = 0
        rows = min(capacity, _INITIAL_ROWS)
        self._states = np.zeros((rows, features), dtype=np.float32)
        self._masks = np.zeros((rows, actions), dtype=np.bool_)
        self._actions = np.zeros(rows, dtype=np.int64)

    def __len__(self) -> int:
        return self._size

    def _ensure(self, rows: int) -> None:
        if rows <= len(self._states):
            return
        size = min(self.capacity, max(rows, len(self._states) * 2))
        self._states = _grow(self._states, size)  # type: ignore[assignment]
        self._masks = _grow(self._masks, size)  # type: ignore[assignment]
        self._actions = _grow(self._actions, size)  # type: ignore[assignment]

    def _write(self, row: int, item: PolicySample) -> None:
        self._states[row] = item.state
        self._masks[row] = item.mask
        self._actions[row] = item.action

    def add(self, item: PolicySample, rng: random.Random) -> None:
        self.seen += 1
        if self._size < self.capacity:
            self._ensure(self._size + 1)
            self._write(self._size, item)
            self._size += 1
            return
        index = rng.randrange(self.seen)
        if index < self.capacity:
            self._write(index, item)

    def sample(self, count: int, rng: random.Random) -> PolicyBatch:
        rows = np.asarray(rng.sample(range(self._size), min(count, self._size)), dtype=np.int64)
        return PolicyBatch(self._states[rows], self._masks[rows], self._actions[rows])

    def arrays(self) -> tuple[NDArray[np.float32], NDArray[np.bool_], NDArray[np.int64]]:
        """States, masks and actions of the stored samples (for checkpoints)."""
        return self._states[: self._size], self._masks[: self._size], self._actions[: self._size]

    def load(self, states: ArrayLike, masks: ArrayLike, actions: ArrayLike, seen: int) -> None:
        """Replace the contents (from a checkpoint)."""
        actions = np.asarray(actions)
        count = len(actions)
        if count > self.capacity:
            raise ValueError("more samples than the reservoir holds")
        self._states = np.zeros(
            (max(count, min(self.capacity, _INITIAL_ROWS)), self.features), dtype=np.float32
        )
        self._masks = np.zeros((len(self._states), self.action_count), dtype=np.bool_)
        self._actions = np.zeros(len(self._states), dtype=np.int64)
        if count:
            self._states[:count] = np.asarray(states, dtype=np.float32).reshape(
                count, self.features
            )
            self._masks[:count] = np.asarray(masks, dtype=np.bool_).reshape(
                count, self.action_count
            )
            self._actions[:count] = np.asarray(actions, dtype=np.int64)
        self._size = count
        self.seen = seen

    def items(self) -> list[PolicySample]:
        return [
            PolicySample(
                tuple(float(value) for value in self._states[row]),
                tuple(bool(value) for value in self._masks[row]),
                int(self._actions[row]),
            )
            for row in range(self._size)
        ]
