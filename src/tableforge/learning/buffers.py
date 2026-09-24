from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Transition:
    state: tuple[float, ...]
    mask: tuple[bool, ...]
    action: int
    reward: float
    next_state: tuple[float, ...] | None
    next_mask: tuple[bool, ...] | None


@dataclass(frozen=True, slots=True)
class PolicySample:
    state: tuple[float, ...]
    mask: tuple[bool, ...]
    action: int


@dataclass(slots=True)
class CircularBuffer[T]:
    capacity: int
    items: list[T] = field(default_factory=list)
    position: int = 0

    def __post_init__(self) -> None:
        if self.capacity < 1:
            raise ValueError("capacity must be positive")

    def __len__(self) -> int:
        return len(self.items)

    def add(self, item: T) -> None:
        if len(self.items) < self.capacity:
            self.items.append(item)
        else:
            self.items[self.position] = item
        self.position = (self.position + 1) % self.capacity

    def sample(self, count: int, rng: random.Random) -> list[T]:
        return rng.sample(self.items, min(count, len(self.items)))


@dataclass(slots=True)
class ReservoirBuffer[T]:
    capacity: int
    items: list[T] = field(default_factory=list)
    seen: int = 0

    def __post_init__(self) -> None:
        if self.capacity < 1:
            raise ValueError("capacity must be positive")

    def __len__(self) -> int:
        return len(self.items)

    def add(self, item: T, rng: random.Random) -> None:
        self.seen += 1
        if len(self.items) < self.capacity:
            self.items.append(item)
            return
        index = rng.randrange(self.seen)
        if index < self.capacity:
            self.items[index] = item

    def sample(self, count: int, rng: random.Random) -> list[T]:
        return rng.sample(self.items, min(count, len(self.items)))
