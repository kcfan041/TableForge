from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum


class Suit(StrEnum):
    CLUBS = "c"
    DIAMONDS = "d"
    HEARTS = "h"
    SPADES = "s"


class Rank(IntEnum):
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5
    SIX = 6
    SEVEN = 7
    EIGHT = 8
    NINE = 9
    TEN = 10
    JACK = 11
    QUEEN = 12
    KING = 13
    ACE = 14


_RANK_TO_SYMBOL = {
    Rank.TWO: "2",
    Rank.THREE: "3",
    Rank.FOUR: "4",
    Rank.FIVE: "5",
    Rank.SIX: "6",
    Rank.SEVEN: "7",
    Rank.EIGHT: "8",
    Rank.NINE: "9",
    Rank.TEN: "T",
    Rank.JACK: "J",
    Rank.QUEEN: "Q",
    Rank.KING: "K",
    Rank.ACE: "A",
}
_SYMBOL_TO_RANK = {symbol: rank for rank, symbol in _RANK_TO_SYMBOL.items()}


@dataclass(frozen=True, order=True, slots=True)
class Card:
    rank: Rank
    suit: Suit

    def __str__(self) -> str:
        return f"{_RANK_TO_SYMBOL[self.rank]}{self.suit.value}"

    @classmethod
    def parse(cls, value: str) -> Card:
        normalized = value.strip()
        if len(normalized) != 2:
            raise ValueError(f"invalid card: {value!r}")
        try:
            rank = _SYMBOL_TO_RANK[normalized[0].upper()]
            suit = Suit(normalized[1].lower())
            return cls(rank=rank, suit=suit)
        except (KeyError, ValueError) as error:
            raise ValueError(f"invalid card: {value!r}") from error


def full_deck() -> tuple[Card, ...]:
    return tuple(Card(rank, suit) for suit in Suit for rank in Rank)
