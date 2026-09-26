"""Hand-strength features computed only from a player's own cards and the visible board.

Shared by the learned-model encoders and rule-based bots. Everything here is a pure
function of (hole cards, board); nothing looks at other players' cards or the deck.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from tableforge.poker.cards import Card
from tableforge.poker.evaluator import HandCategory, evaluate

_CHEN_HIGH = {14: 10.0, 13: 8.0, 12: 7.0, 11: 6.0}


def chen_score(hole: tuple[Card, Card]) -> float:
    """Bill Chen's preflop hand score, from -1 (72o) to 20 (AA)."""
    high, low = sorted((int(hole[0].rank), int(hole[1].rank)), reverse=True)
    score = _CHEN_HIGH.get(high, high / 2)
    if high == low:
        return max(score * 2, 5.0)
    if hole[0].suit == hole[1].suit:
        score += 2
    gap = high - low - 1
    score -= {0: 0, 1: 1, 2: 2, 3: 4}.get(gap, 5)
    if gap <= 1 and high < 12:
        score += 1
    return float(math.ceil(score))


@dataclass(frozen=True, slots=True)
class PreflopFeatures:
    high_rank: int
    low_rank: int
    suited: bool
    pair: bool
    gap: int
    chen: float


def preflop_features(hole: tuple[Card, Card]) -> PreflopFeatures:
    high, low = sorted((int(hole[0].rank), int(hole[1].rank)), reverse=True)
    return PreflopFeatures(
        high_rank=high,
        low_rank=low,
        suited=hole[0].suit == hole[1].suit,
        pair=high == low,
        gap=high - low,
        chen=chen_score(hole),
    )


@dataclass(frozen=True, slots=True)
class PostflopFeatures:
    category: HandCategory
    primary_rank: int
    pair_uses_hole: bool
    overpair: bool
    top_pair: bool
    flush_draw: bool
    open_ended_draw: bool
    gutshot: bool
    board_paired: bool
    board_trips: bool
    board_three_suited: bool
    board_four_suited: bool
    board_straight_possible: bool
    board_high_rank: int


def postflop_features(hole: tuple[Card, Card], board: tuple[Card, ...]) -> PostflopFeatures:
    if len(board) < 3:
        raise ValueError("postflop features need at least three board cards")
    rank = evaluate((*hole, *board))
    hole_ranks = {int(card.rank) for card in hole}
    board_ranks = [int(card.rank) for card in board]
    board_high = max(board_ranks)
    pair_rank = rank.kickers[0] if rank.category is HandCategory.ONE_PAIR else 0
    pocket_pair = int(hole[0].rank) == int(hole[1].rank)
    all_cards = (*hole, *board)
    suit_counts = Counter(card.suit for card in all_cards)
    flush_suit, flush_count = suit_counts.most_common(1)[0]
    cards_to_come = 5 - len(board)
    made_flush_or_better = rank.category >= HandCategory.FLUSH
    flush_draw = (
        cards_to_come > 0
        and not made_flush_or_better
        and flush_count == 4
        and any(card.suit == flush_suit for card in hole)
    )
    completing = _straight_completing_ranks({int(card.rank) for card in all_cards})
    has_straight = rank.category >= HandCategory.STRAIGHT
    straight_outs = 0 if has_straight or cards_to_come == 0 else len(completing)
    board_counts = Counter(board_ranks)
    board_suits = Counter(card.suit for card in board)
    return PostflopFeatures(
        category=rank.category,
        primary_rank=rank.kickers[0],
        pair_uses_hole=pair_rank in hole_ranks,
        overpair=pocket_pair and int(hole[0].rank) > board_high,
        top_pair=pair_rank == board_high and pair_rank in hole_ranks,
        flush_draw=flush_draw,
        open_ended_draw=straight_outs >= 2,
        gutshot=straight_outs == 1,
        board_paired=max(board_counts.values()) >= 2,
        board_trips=max(board_counts.values()) >= 3,
        board_three_suited=max(board_suits.values()) >= 3,
        board_four_suited=max(board_suits.values()) >= 4,
        board_straight_possible=_straight_possible(set(board_ranks)),
        board_high_rank=board_high,
    )


def _with_wheel(ranks: set[int]) -> set[int]:
    return ranks | {1} if 14 in ranks else set(ranks)


def _has_straight(ranks: set[int]) -> bool:
    values = _with_wheel(ranks)
    return any(all(start + offset in values for offset in range(5)) for start in range(1, 11))


def _straight_completing_ranks(ranks: set[int]) -> set[int]:
    """Ranks that would complete a straight that is not already made."""
    if _has_straight(ranks):
        return set()
    return {rank for rank in range(2, 15) if rank not in ranks and _has_straight(ranks | {rank})}


def _straight_possible(board_ranks: set[int]) -> bool:
    """True if some two cards could make a straight with this board (3 ranks in a window)."""
    values = _with_wheel(board_ranks)
    return any(
        sum(1 for offset in range(5) if start + offset in values) >= 3 for start in range(1, 11)
    )
