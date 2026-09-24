from __future__ import annotations

import pytest

from tableforge.poker.soak import run_soak


def test_soak_runner_is_deterministic_except_timing() -> None:
    first = run_soak(100, seed_start=10)
    second = run_soak(100, seed_start=10)

    assert first.hands == second.hands
    assert first.actions == second.actions
    assert first.showdowns == second.showdowns
    assert first.folds == second.folds
    assert first.showdowns + first.folds == first.hands


def test_soak_runner_rejects_negative_hands() -> None:
    with pytest.raises(ValueError):
        run_soak(-1)
