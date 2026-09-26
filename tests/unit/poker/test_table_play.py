from __future__ import annotations

import json

import pytest

from tableforge.agents.rule import AggroBot, CallBot, TightBot
from tableforge.domain.experiments import BUILTIN_PRESETS, ExperimentConfig, ExperimentKind
from tableforge.domain.simulation import SimulationJobPayload
from tableforge.poker.table_play import (
    evaluate_against_rule_bots,
    evaluate_table,
    rule_bot,
    simulate_table,
)
from tableforge.worker.simulation import SimulationExecutor


@pytest.mark.parametrize("players", [2, 3, 4, 5, 6])
def test_simulation_conserves_chips_for_every_table_size(players: int) -> None:
    summary = simulate_table(players, 400, seed_start=17)

    assert summary.hands == 400
    assert sum(summary.winnings) == 0
    assert summary.showdowns + summary.folds == 400
    assert summary.actions > 400


def test_simulation_is_deterministic() -> None:
    first = simulate_table(6, 200, seed_start=3)
    second = simulate_table(6, 200, seed_start=3)

    assert (first.winnings, first.actions, first.side_pots) == (
        second.winnings,
        second.actions,
        second.side_pots,
    )


def test_six_max_simulation_creates_side_pots() -> None:
    summary = simulate_table(6, 2_000, seed_start=0, varied_stacks=True)

    assert summary.side_pots > 0


def test_payload_keeps_heads_up_encoding_and_adds_players_only_when_needed() -> None:
    heads_up = SimulationJobPayload("1.0", hands=10, seed_start=0)
    six = SimulationJobPayload("1.0", hands=10, seed_start=0, players=6)

    assert "players" not in json.loads(heads_up.to_bytes())
    assert SimulationJobPayload.from_bytes(six.to_bytes()).players == 6
    with pytest.raises(ValueError):
        SimulationJobPayload("1.0", hands=10, seed_start=0, players=7)


def test_executor_runs_six_max_payload() -> None:
    payload = SimulationJobPayload("1.0", hands=120, seed_start=5, players=6).to_bytes()
    progress: list[int] = []

    summary = json.loads(
        SimulationExecutor(progress_interval_hands=50).execute(payload, progress.append)
    )

    assert summary["players"] == 6
    assert summary["hands"] == 120
    assert len(summary["winnings"]) == 6 and sum(summary["winnings"]) == 0
    assert progress == [50, 100]


def test_heads_up_executor_output_is_unchanged() -> None:
    payload = SimulationJobPayload("1.0", hands=40, seed_start=1).to_bytes()

    summary = json.loads(SimulationExecutor().execute(payload))

    assert "players" not in summary and len(summary["winnings"]) == 2


def test_six_max_presets_exist_and_only_rule_simulations_go_multi_player() -> None:
    keys = {preset.key: preset for preset in BUILTIN_PRESETS}

    assert keys["6max-rule-smoke"].config().players == 6
    assert keys["6max-rule-soak"].config().hands == 1_000_000
    with pytest.raises(ValueError):
        ExperimentConfig(
            "1.0",
            "x",
            "1",
            hands=10,
            hands_per_job=10,
            seed_start=0,
            kind=ExperimentKind.NFSP_PIPELINE,
            evaluation_deals=10,
            players=6,
        )


def test_evaluation_reports_per_seat_and_is_zero_for_identical_bots() -> None:
    report = evaluate_table(
        CallBot, lambda seat, seed: CallBot(), players=6, deals=60, seed_start=1
    )

    assert report.hands == 360
    assert len(report.per_seat) == 6
    assert report.bb_per_100 == pytest.approx(0.0, abs=1e-9)


def test_tight_beats_a_table_of_callers() -> None:
    report = evaluate_table(
        TightBot, lambda seat, seed: rule_bot("call", seed), players=6, deals=400, seed_start=2
    )

    assert report.significantly_winning


def test_rule_bot_benchmark_covers_every_bot() -> None:
    reports = evaluate_against_rule_bots(AggroBot, players=3, deals=20)

    assert set(reports) == {"random", "call", "tight", "aggro"}
    assert all(report.hands == 60 for report in reports.values())
