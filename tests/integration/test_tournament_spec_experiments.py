from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tableforge.composition import build_services
from tableforge.domain.experiments import BUILTIN_PRESETS
from tableforge.poker.tournament import BountyConfig, TournamentConfig, preset_config
from tableforge.poker.tournament_benchmark import EvaluationProtocol, evaluate_with
from tableforge.settings import Settings
from tableforge.worker.tournament import field_factory, rule_bot

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def preset(key: str) -> object:
    return next(item for item in BUILTIN_PRESETS if item.key == key)


def test_existing_10bb_training_tournament_is_unchanged() -> None:
    new = preset("6max-pko-10bb-train").tournament_json  # type: ignore[attr-defined]

    assert new == preset_config(10, bounty=BountyConfig()).to_json()


@pytest.mark.parametrize(
    "key,stack,payout",
    [
        ("6max-pko-25bb-train", 25, (0.65, 0.35)),
        ("6max-pko-50bb-train", 50, (0.65, 0.35)),
        ("6max-pko-25bb-top3-train", 25, (0.5, 0.3, 0.2)),
        ("6max-pko-50bb", 50, (0.65, 0.35)),
    ],
)
def test_deeper_presets(key: str, stack: int, payout: tuple[float, ...]) -> None:
    config = TournamentConfig.from_json(preset(key).tournament_json)  # type: ignore[attr-defined]

    assert config.starting_stack_bb == stack and config.payout == payout
    assert config.bounty == BountyConfig(0.5, 0.5, True)


def test_experiments_can_choose_any_tournament(tmp_path: Path) -> None:
    experiments = build_services(Settings(data_dir=tmp_path)).experiments

    training = experiments.create(
        "6max-pko-10bb-train",
        NOW,
        {
            "tournament": {"players": 4, "starting_stack_bb": 40, "payout": "50/30/20"},
            "opponents": "hunter",
            "rounds": 2,
        },
    )
    benchmark = experiments.create(
        "6max-sng-10bb", NOW, {"tournament": {"starting_stack_bb": 50, "cash_share": 1.0}}
    )

    config = TournamentConfig.from_json(training.config.tournament_json)
    assert (config.players, config.starting_stack_bb, config.payout) == (4, 40, (0.5, 0.3, 0.2))
    assert training.config.players == 4 and training.config.opponents == "hunter"
    assert training.config.rounds == 2
    bounty = TournamentConfig.from_json(benchmark.config.tournament_json).bounty
    assert bounty == BountyConfig(0.5, 1.0, True)
    for bad in (
        {"tournament": {"players": 9}},
        {"tournament": {"payout": "60/30"}},
        {"opponents": "aliens"},
    ):
        with pytest.raises(ValueError):
            experiments.create("6max-pko-10bb-train", NOW, bad)
    with pytest.raises(ValueError, match="tournament presets"):
        experiments.create("hu-nfsp-v2-train", NOW, {"tournament": {}})


def test_every_player_is_evaluated_under_one_protocol() -> None:
    protocol = EvaluationProtocol(preset_config(25, bounty=BountyConfig()).to_json(), seeds=4)

    first = evaluate_with(protocol, lambda: rule_bot("tight", 0), field_factory)
    again = evaluate_with(protocol, lambda: rule_bot("tight", 0), field_factory)
    other = evaluate_with(protocol, lambda: rule_bot("call", 0), field_factory)

    assert first == again
    assert first.tournaments == other.tournaments == 24
    description = protocol.describe()
    assert description["tournaments"] == 24 and description["field"] == "mixed"
    assert len(str(description["config_sha256"])) == 64
    assert json.loads(protocol.config_json)["starting_stack_bb"] == 25
