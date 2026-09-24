from __future__ import annotations

import random
from dataclasses import dataclass

from tableforge.poker.cards import Card, full_deck


class DeckExhaustedError(Exception):
    pass


@dataclass(slots=True)
class Deck:
    _cards: list[Card]
    _position: int = 0

    @classmethod
    def shuffled(cls, seed: int | str | bytes) -> Deck:
        cards = list(full_deck())
        random.Random(seed).shuffle(cards)
        return cls(cards)

    @classmethod
    def ordered(cls) -> Deck:
        return cls(list(full_deck()))

    @property
    def remaining(self) -> int:
        return len(self._cards) - self._position

    def deal(self, count: int = 1) -> tuple[Card, ...]:
        if count < 0:
            raise ValueError("count cannot be negative")
        end = self._position + count
        if end > len(self._cards):
            raise DeckExhaustedError(f"requested {count} cards with {self.remaining} remaining")
        cards = tuple(self._cards[self._position : end])
        self._position = end
        return cards
