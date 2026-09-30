"""The 169 preflop hand classes and the all-in equity table between them.

Hand classes are in grid order (row i, column j of the 13x13 chart: pairs on the
diagonal, suited above it, offsuit below), the same order as strategy tables. Each class
stands for its card combinations: 6 for a pair, 4 suited, 12 offsuit (1,326 in total).

`preflop_equity()` loads `data/preflop_equity.json`, built by `scripts/preflop_equity.py`:
for each pair of classes, how often the first wins an all-in against the second (ties
count half), averaged over their card combinations that share no card.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

RANKS = "AKQJT98765432"
_DATA = Path(__file__).parent / "data" / "preflop_equity.json"


def _classes() -> tuple[str, ...]:
    names = []
    for row, first in enumerate(RANKS):
        for column, second in enumerate(RANKS):
            if row == column:
                names.append(first + second)
            elif column > row:
                names.append(first + second + "s")
            else:
                names.append(second + first + "o")
    return tuple(names)


HAND_CLASSES = _classes()
TOTAL_COMBOS = 1326


def rank_value(symbol: str) -> int:
    """2 for a deuce ... 14 for an ace."""
    return 14 - RANKS.index(symbol)


def class_combos(name: str) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """Card combinations of a class as ((rank, suit), (rank, suit)), suits 0-3."""
    high, low = rank_value(name[0]), rank_value(name[1])
    if high == low:
        return [
            ((high, first), (low, second)) for first in range(4) for second in range(first + 1, 4)
        ]
    if name.endswith("s"):
        return [((high, suit), (low, suit)) for suit in range(4)]
    return [
        ((high, first), (low, second))
        for first in range(4)
        for second in range(4)
        if first != second
    ]


def combo_count(name: str) -> int:
    if name[0] == name[1]:
        return 6
    return 4 if name.endswith("s") else 12


def weights() -> list[float]:
    """Share of all starting hands in each class (adds up to 1)."""
    return [combo_count(name) / TOTAL_COMBOS for name in HAND_CLASSES]


@cache
def compatible_combos() -> tuple[tuple[int, ...], ...]:
    """count[i][j]: pairs of a class-i combination and a class-j one that share no card.

    Dividing row i by (combos of i) x 1,225 gives the chance that an opponent holds class
    j when you hold class i (card removal between two players).
    """
    combos = [
        (index, {first, second})
        for index, name in enumerate(HAND_CLASSES)
        for first, second in class_combos(name)
    ]
    counts = [[0] * len(HAND_CLASSES) for _ in HAND_CLASSES]
    for left, (first_class, first_cards) in enumerate(combos):
        for second_class, second_cards in combos[left + 1 :]:
            if first_cards.isdisjoint(second_cards):
                counts[first_class][second_class] += 1
                counts[second_class][first_class] += 1
    return tuple(tuple(row) for row in counts)


@cache
def preflop_equity() -> tuple[tuple[float, ...], ...]:
    """equity[i][j]: chance class i wins an all-in against class j (ties count half)."""
    data = json.loads(_DATA.read_text(encoding="utf-8"))
    if data.get("schema_version") != "tableforge.preflop-equity.v1":
        raise ValueError("unsupported preflop equity table")
    if tuple(data["hands"]) != HAND_CLASSES:
        raise ValueError("preflop equity table uses another hand order")
    unit = float(data["unit"])
    return tuple(tuple(value / unit for value in row) for row in data["equity"])
