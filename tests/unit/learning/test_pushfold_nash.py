from __future__ import annotations

import pytest

pytest.importorskip("numpy")

from tableforge.learning.pushfold_nash import (  # noqa: E402
    PushFoldGame,
    action_order,
    solve,
)
from tableforge.poker.pushfold_ranges import (  # noqa: E402
    HAND_CLASSES,
    RANKS,
    TOTAL_COMBOS,
    class_combos,
    combo_count,
    preflop_equity,
    weights,
)

# Heads-up push/fold chart (no ante), published by HoldemResources.net
# (https://www.holdemresources.net/hune): the largest effective stack in big blinds at which
# a hand is pushed (small blind) or called (big blind); 99 stands for "20+". Rows and
# columns follow RANKS; above the diagonal suited, below offsuit. None marks hands whose
# chart entry has gaps (63s, 53s, 43s).
PUSH = """
99 99 99 99 99 99 99 99 99 99 99 99 99
99 99 99 99 99 99 99 99 99 99 99 19.9 19.3
99 99 99 99 99 99 99 99 99 99 16.3 13.5 12.7
99 99 99 99 99 99 99 99 18.6 14.7 13.5 10.6 8.5
99 99 99 99 99 99 99 99 99 11.9 10.5 7.7 6.5
99 99 99 99 99 99 99 99 99 14.4 6.9 4.9 3.7
99 18.0 13.0 13.3 17.5 99 99 99 99 18.8 10.1 2.7 2.5
99 16.1 10.3 8.5 9.0 10.8 14.7 99 99 99 13.9 2.5 2.1
99 15.1 9.6 6.5 5.7 5.2 7.0 10.7 99 99 16.3 None 2.0
99 14.2 8.9 6.0 4.1 3.5 3.0 2.6 2.4 99 99 None 2.0
99 13.1 7.9 5.4 3.8 2.7 2.3 2.1 2.0 2.1 99 None 1.8
99 12.2 7.5 5.0 3.4 2.5 1.9 1.8 1.7 1.8 1.6 99 1.7
99 11.6 7.0 4.6 2.9 2.2 1.8 1.6 1.5 1.5 1.4 1.4 99
"""
CALL = """
99 99 99 99 99 99 99 99 99 99 99 99 99
99 99 99 99 99 99 17.6 15.2 14.3 13.2 12.1 11.4 10.7
99 99 99 99 99 16.1 13.0 10.5 9.9 8.9 8.4 7.8 7.2
99 99 19.5 99 18.0 13.4 10.6 8.8 7.0 6.9 6.1 5.8 5.6
99 99 15.3 12.7 99 11.5 9.3 7.4 6.3 5.2 5.2 4.8 4.5
99 17.1 11.7 9.5 8.4 99 8.2 7.0 5.8 5.0 4.3 4.1 3.9
99 13.8 9.7 7.6 6.6 6.0 99 6.5 5.6 4.8 4.1 3.6 3.5
99 12.4 8.0 6.4 5.5 5.0 4.7 99 5.4 4.8 4.1 3.6 3.3
99 11.0 7.3 5.4 4.6 4.2 4.1 4.0 99 4.9 4.3 3.8 3.3
99 10.2 6.8 5.1 4.0 3.7 3.6 3.6 3.7 99 4.6 4.0 3.6
18.3 9.1 6.2 4.7 3.8 3.3 3.2 3.2 3.3 3.5 99 3.8 3.4
16.6 8.7 5.9 4.5 3.6 3.1 2.9 2.9 2.9 3.1 3.0 99 3.3
15.8 8.1 5.6 4.2 3.5 3.0 2.8 2.6 2.7 2.8 2.7 2.6 15.0
"""


def chart(text: str) -> dict[str, float | None]:
    rows = [line.split() for line in text.strip().splitlines()]
    values: dict[str, float | None] = {}
    for row, first in enumerate(RANKS):
        for column, second in enumerate(RANKS):
            item = rows[row][column]
            if row == column:
                name = first + second
            elif column > row:
                name = first + second + "s"
            else:
                name = second + first + "o"
            values[name] = None if item == "None" else float(item)
    return values


def test_hand_classes_cover_every_starting_hand() -> None:
    assert len(HAND_CLASSES) == 169 and len(set(HAND_CLASSES)) == 169
    assert sum(combo_count(name) for name in HAND_CLASSES) == TOTAL_COMBOS
    assert all(len(class_combos(name)) == combo_count(name) for name in HAND_CLASSES)
    assert sum(weights()) == pytest.approx(1.0)


def test_equity_table_matches_known_matchups() -> None:
    equity = preflop_equity()
    index = HAND_CLASSES.index
    known = {("AA", "KK"): 0.82, ("AKs", "QQ"): 0.46, ("22", "AKo"): 0.53, ("72o", "AKo"): 0.33}
    for (first, second), value in known.items():
        assert equity[index(first)][index(second)] == pytest.approx(value, abs=0.01)
    for first in range(169):
        assert equity[first][first] == 0.5
        for second in range(169):
            assert equity[first][second] + equity[second][first] == pytest.approx(1.0, abs=1e-4)


@pytest.mark.parametrize("stack_bb", [3, 5, 8, 10, 13, 16])
def test_heads_up_matches_the_published_push_fold_chart(stack_bb: int) -> None:
    game = PushFoldGame((stack_bb * 20, stack_bb * 20), 10, 20, 0, (1.0,))
    solution = solve(game, 3_000)
    push, call = solution.push[0], solution.call[0, 1]
    mismatches = []
    for table, strategy, role in ((chart(PUSH), push, "push"), (chart(CALL), call, "call")):
        for name, threshold in table.items():
            # Skip hands with gaps and hands within 12 % of their threshold: the chart
            # rounds, simplifies mixed strategies, and our equities are sampled.
            if threshold is None or abs(threshold - stack_bb) < 0.12 * stack_bb:
                continue
            expected = threshold > stack_bb
            actual = strategy[HAND_CLASSES.index(name)] > 0.5
            if expected != actual:
                mismatches.append(f"{role} {name} (chart {threshold}, got {actual})")
    assert solution.max_gap < 1e-3
    # The published chart omits small gaps in some hands' strategies (e.g. 84s near 8 BB);
    # allow two of the 338 decisions to differ.
    assert len(mismatches) <= 2, mismatches


def test_six_max_icm_spot_converges_and_is_tighter_than_chip_ev() -> None:
    stacks = (200,) * 6
    icm = solve(PushFoldGame(stacks, 10, 20, 20, (0.65, 0.35)), 2_000)
    chips = solve(PushFoldGame(stacks, 10, 20, 20, (1.0,) + (0.0,) * 5), 2_000)
    share = weights()

    def rate(strategy: tuple[float, ...]) -> float:
        return sum(p * w for p, w in zip(strategy, share, strict=True))

    assert action_order(6) == (3, 4, 5, 0, 1, 2)
    assert icm.max_gap < 2e-3
    # A 65/35 payout punishes busting more than winner-take-all: the big blind calls less.
    assert rate(icm.call[0, 2]) < rate(chips.call[0, 2])
    assert sum(icm.values) == pytest.approx(1.0, abs=1e-6)


def test_bounties_widen_calls_against_a_covered_shover() -> None:
    stacks = (150, 400, 400)
    plain = solve(PushFoldGame(stacks, 10, 20, 20, (0.65, 0.35), heads=(0.0, 0.0, 0.0)), 2_000)
    pko = solve(PushFoldGame(stacks, 10, 20, 20, (0.65, 0.35), heads=(0.5, 0.5, 0.5)), 2_000)
    share = weights()

    def rate(strategy: tuple[float, ...]) -> float:
        return sum(p * w for p, w in zip(strategy, share, strict=True))

    # The button (150 chips) shoves; the big blind covers it and can win its bounty.
    assert rate(pko.call[0, 2]) > rate(plain.call[0, 2])
    assert pko.max_gap < 2e-3


def test_game_validation() -> None:
    with pytest.raises(ValueError):
        PushFoldGame((100,), 10, 20, 0, (1.0,))
    with pytest.raises(ValueError):
        PushFoldGame((100, 100), 10, 20, 0, (1.0,), heads=(0.5,))
