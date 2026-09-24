from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import IntEnum
from itertools import combinations
from typing import Self

from tableforge.poker.cards import Card


class HandCategory(IntEnum):
    HIGH_CARD = 0
    ONE_PAIR = 1
    TWO_PAIR = 2
    THREE_OF_A_KIND = 3
    STRAIGHT = 4
    FLUSH = 5
    FULL_HOUSE = 6
    FOUR_OF_A_KIND = 7
    STRAIGHT_FLUSH = 8


@dataclass(frozen=True, order=True, slots=True)
class HandRank:
    category: HandCategory
    kickers: tuple[int, ...]

    @classmethod
    def from_five_cards(cls, cards: tuple[Card, ...]) -> Self:
        if len(cards) != 5:
            raise ValueError("exactly five cards are required")
        if len(set(cards)) != 5:
            raise ValueError("cards must be unique")

        counts = Counter(int(card.rank) for card in cards)
        groups = sorted(((count, rank) for rank, count in counts.items()), reverse=True)
        ranks_desc = tuple(sorted(counts, reverse=True))
        flush = len({card.suit for card in cards}) == 1
        straight_high = _straight_high(ranks_desc)

        if flush and straight_high is not None:
            return cls(HandCategory.STRAIGHT_FLUSH, (straight_high,))
        if groups[0][0] == 4:
            four_rank = groups[0][1]
            kicker = next(rank for rank in ranks_desc if rank != four_rank)
            return cls(HandCategory.FOUR_OF_A_KIND, (four_rank, kicker))
        if groups[0][0] == 3 and groups[1][0] == 2:
            return cls(HandCategory.FULL_HOUSE, (groups[0][1], groups[1][1]))
        if flush:
            return cls(HandCategory.FLUSH, ranks_desc)
        if straight_high is not None:
            return cls(HandCategory.STRAIGHT, (straight_high,))
        if groups[0][0] == 3:
            trips_rank = groups[0][1]
            kickers = tuple(rank for rank in ranks_desc if rank != trips_rank)
            return cls(HandCategory.THREE_OF_A_KIND, (trips_rank, *kickers))
        pairs = sorted((rank for count, rank in groups if count == 2), reverse=True)
        if len(pairs) == 2:
            kicker = next(rank for rank in ranks_desc if rank not in pairs)
            return cls(HandCategory.TWO_PAIR, (*pairs, kicker))
        if len(pairs) == 1:
            pair_rank = pairs[0]
            kickers = tuple(rank for rank in ranks_desc if rank != pair_rank)
            return cls(HandCategory.ONE_PAIR, (pair_rank, *kickers))
        return cls(HandCategory.HIGH_CARD, ranks_desc)


def evaluate(cards: tuple[Card, ...]) -> HandRank:
    if not 5 <= len(cards) <= 7:
        raise ValueError("between five and seven cards are required")
    if len(set(cards)) != len(cards):
        raise ValueError("cards must be unique")
    return max(HandRank.from_five_cards(tuple(hand)) for hand in combinations(cards, 5))


def _straight_high(ranks_desc: tuple[int, ...]) -> int | None:
    unique = set(ranks_desc)
    if 14 in unique:
        unique.add(1)
    ordered = sorted(unique, reverse=True)
    for start in range(len(ordered) - 4):
        window = ordered[start : start + 5]
        if window[0] - window[4] == 4:
            return window[0]
    return None
