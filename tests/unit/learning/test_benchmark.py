from __future__ import annotations

import pytest

from tableforge.agents.rule import CallBot, TightBot
from tableforge.learning.benchmark import BENCHMARK_OPPONENTS, run_benchmark


def test_benchmark_is_reproducible_and_covers_every_rule_bot() -> None:
    first = run_benchmark(TightBot, deals=30)
    second = run_benchmark(TightBot, deals=30)

    assert [result.opponent for result in first.results] == list(BENCHMARK_OPPONENTS)
    assert first.summary() == second.summary()
    assert all(result.report.hands == 60 for result in first.results)


def test_mirror_match_is_not_significant() -> None:
    benchmark = run_benchmark(CallBot, deals=40, opponents=("call",))

    result = benchmark.by_opponent()["call"]
    assert result.report.bb_per_100 == pytest.approx(0.0, abs=1e-9)
    assert not result.significantly_winning
    assert not result.significantly_losing
    assert not benchmark.beats_all


def test_benchmark_rejects_too_few_deals() -> None:
    with pytest.raises(ValueError):
        run_benchmark(CallBot, deals=1)
