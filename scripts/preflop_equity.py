"""Build the preflop all-in equity table used by the Push/Fold Nash baseline.

For every pair of the 169 starting-hand classes this estimates how often the first hand
wins an all-in against the second (ties count half), averaged over all card combinations
that do not share a card. It samples random combination pairs and boards (Monte Carlo)
with a vectorised 7-card evaluator that is first checked against the engine's evaluator.

    uv run python scripts/preflop_equity.py --samples 40000 --workers 16

Writes `src/tableforge/poker/data/preflop_equity.json`: equities in units of 1/10,000.
The standard error per pair is about 0.5 / sqrt(samples) (0.25 % at 40,000).
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tableforge.poker.pushfold_ranges import HAND_CLASSES, class_combos  # noqa: E402

OUTPUT = Path(__file__).resolve().parents[1] / "src" / "tableforge" / "poker" / "data"

# --- vectorised evaluator ---------------------------------------------------------------
# Cards are 0..51: rank = card // 4 (0 = deuce ... 12 = ace), suit = card % 4.
# A hand's value is category << 20 | five 4-bit rank digits (rank + 1, 0 = none).


def _top5(mask: int) -> int:
    ranks = [rank for rank in range(12, -1, -1) if mask >> rank & 1][:5]
    value = 0
    for position in range(5):
        value = value << 4 | (ranks[position] + 1 if position < len(ranks) else 0)
    return value


def _straight_high(mask: int) -> int:
    extended = mask << 1 | (mask >> 12 & 1)  # ace also plays low
    for high in range(13, 3, -1):
        window = 0b11111 << (high - 4)
        if extended & window == window:
            return high  # 13 = ace-high ... 4 = five-high (in +1 units)
    return 0


TOP5 = np.array([_top5(mask) for mask in range(1 << 13)], dtype=np.int64)
STRAIGHT = np.array([_straight_high(mask) for mask in range(1 << 13)], dtype=np.int64)
BITS = (1 << np.arange(13)).astype(np.int64)


def evaluate7(cards: np.ndarray) -> np.ndarray:
    """Values of hands given as an (N, 7) array; larger is better."""
    ranks = cards // 4
    suits = cards % 4
    rank_counts = np.zeros((cards.shape[0], 13), dtype=np.int64)
    np.add.at(rank_counts, (np.arange(cards.shape[0])[:, None], ranks), 1)
    presence = (rank_counts > 0).astype(np.int64) @ BITS
    suit_counts = np.zeros((cards.shape[0], 4), dtype=np.int64)
    np.add.at(suit_counts, (np.arange(cards.shape[0])[:, None], suits), 1)
    flush_suit = suit_counts.argmax(axis=1)
    is_flush = suit_counts.max(axis=1) >= 5
    in_flush = suits == flush_suit[:, None]
    flush_mask = np.where(in_flush, 1 << ranks, 0).sum(axis=1)

    value = np.zeros(cards.shape[0], dtype=np.int64)
    # Groups sorted by (count, rank), best first.
    keys = np.sort(rank_counts * 16 + np.arange(13) * (rank_counts > 0), axis=1)[:, ::-1]
    first_count, first_rank = keys[:, 0] // 16, keys[:, 0] % 16
    second_count, second_rank = keys[:, 1] // 16, keys[:, 1] % 16

    def kickers(exclude: np.ndarray, count: int) -> np.ndarray:
        rest = TOP5[presence & ~exclude]
        return rest >> (4 * (5 - count))

    high_card = TOP5[presence]
    one = 1 << first_rank
    pair = (1 << 20) | (first_rank + 1) << 16 | kickers(one, 3) << 4
    two = 1 << second_rank
    two_pair = (
        (2 << 20) | (first_rank + 1) << 16 | (second_rank + 1) << 12 | (kickers(one | two, 1) << 8)
    )
    trips = (3 << 20) | (first_rank + 1) << 16 | kickers(one, 2) << 8
    straight_high = STRAIGHT[presence]
    straight = (4 << 20) | straight_high << 16
    flush = (5 << 20) | TOP5[flush_mask]
    full_house = (6 << 20) | (first_rank + 1) << 16 | (second_rank + 1) << 12
    quads = (7 << 20) | (first_rank + 1) << 16 | kickers(one, 1) << 12
    straight_flush_high = STRAIGHT[flush_mask]
    straight_flush = (8 << 20) | straight_flush_high << 16

    value = high_card
    value = np.where(first_count == 2, pair, value)
    value = np.where((first_count == 2) & (second_count == 2), two_pair, value)
    value = np.where(first_count == 3, trips, value)
    value = np.where(straight_high > 0, np.maximum(value, straight), value)
    value = np.where(is_flush, np.maximum(value, flush), value)
    value = np.where((first_count == 3) & (second_count >= 2), full_house, value)
    value = np.where(first_count == 4, quads, value)
    value = np.where(is_flush & (straight_flush_high > 0), straight_flush, value)
    return value


def self_check(samples: int = 20_000) -> None:
    """Compare the vectorised evaluator with the engine's on random 7-card hands."""
    from tableforge.poker.cards import Card, Rank, Suit
    from tableforge.poker.evaluator import evaluate

    suits = (Suit.CLUBS, Suit.DIAMONDS, Suit.HEARTS, Suit.SPADES)
    rng = np.random.default_rng(1)
    hands = np.array([rng.choice(52, 7, replace=False) for _ in range(samples)])
    fast = evaluate7(hands)

    def card(value: int) -> Card:
        return Card(Rank(value // 4 + 2), suits[value % 4])

    slow = [evaluate(tuple(card(int(item)) for item in hand)) for hand in hands]
    values = [int(item) for item in fast]
    for left in range(0, samples - 1):
        right = left + 1
        expected = (slow[left] > slow[right]) - (slow[left] < slow[right])
        actual = (values[left] > values[right]) - (values[left] < values[right])
        if expected != actual:
            raise AssertionError(f"evaluator disagrees on hands {hands[left]} and {hands[right]}")
    for index in range(samples):
        if int(fast[index]) >> 20 != int(slow[index].category):
            raise AssertionError(f"category differs for {hands[index]}")


# --- equity -----------------------------------------------------------------------------


def _card_index(card: tuple[int, int]) -> int:
    rank, suit = card
    return (rank - 2) * 4 + suit


def _combos(name: str) -> np.ndarray:
    return np.array(
        [[_card_index(first), _card_index(second)] for first, second in class_combos(name)]
    )


def pair_equity(args: tuple[int, int, int, int]) -> tuple[int, int, float]:
    first, second, samples, seed = args
    rng = np.random.default_rng(seed)
    mine, theirs = _combos(HAND_CLASSES[first]), _combos(HAND_CLASSES[second])
    compatible = np.array(
        [
            (a, b)
            for a in range(len(mine))
            for b in range(len(theirs))
            if not set(mine[a]) & set(theirs[b])
        ]
    )
    picks = compatible[rng.integers(len(compatible), size=samples)]
    hero, villain = mine[picks[:, 0]], theirs[picks[:, 1]]
    used = np.zeros((samples, 52), dtype=bool)
    rows = np.arange(samples)[:, None]
    used[rows, hero] = True
    used[rows, villain] = True
    # Five board cards from the 48 unused ones: random keys, unused cards sort first.
    keys = rng.random((samples, 52)) + used
    board = np.argsort(keys, axis=1)[:, :5]
    first_value = evaluate7(np.concatenate([hero, board], axis=1))
    second_value = evaluate7(np.concatenate([villain, board], axis=1))
    equity = float(np.mean((first_value > second_value) + 0.5 * (first_value == second_value)))
    return first, second, equity


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=40_000)
    parser.add_argument(
        "--workers", type=int, default=max(1, (multiprocessing.cpu_count() or 2) // 2)
    )
    arguments = parser.parse_args()
    started = time.monotonic()
    self_check()
    print(f"evaluator agrees with the engine ({time.monotonic() - started:.1f}s)", flush=True)
    count = len(HAND_CLASSES)
    tasks = [
        (first, second, arguments.samples, first * count + second)
        for first in range(count)
        for second in range(first, count)
    ]
    table = [[5000] * count for _ in range(count)]
    with multiprocessing.Pool(arguments.workers) as pool:
        for done, (first, second, equity) in enumerate(pool.imap_unordered(pair_equity, tasks, 32)):
            if first == second:
                continue  # a class against itself is 50 % by symmetry
            table[first][second] = round(equity * 10_000)
            table[second][first] = 10_000 - table[first][second]
            if done % 2000 == 0:
                print(f"{done}/{len(tasks)} pairs ({time.monotonic() - started:.0f}s)", flush=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": "tableforge.preflop-equity.v1",
        "hands": list(HAND_CLASSES),
        "samples_per_pair": arguments.samples,
        "unit": 10_000,
        "equity": table,
    }
    (OUTPUT / "preflop_equity.json").write_text(
        json.dumps(document, separators=(",", ":")), encoding="utf-8"
    )
    print(
        f"wrote {OUTPUT / 'preflop_equity.json'} in {time.monotonic() - started:.0f}s", flush=True
    )


if __name__ == "__main__":
    main()
