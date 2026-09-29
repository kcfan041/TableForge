from __future__ import annotations

import json

import pytest

from tableforge.poker.tournament import BlindLevel, BountyConfig, preset_config
from tableforge.poker.tournament_spec import TournamentSpec, parse_payout, spec_from_config


def test_defaults_are_the_existing_10bb_pko() -> None:
    spec = TournamentSpec()

    config = spec.to_config()

    assert config.players == 6 and config.starting_stack_bb == 10
    assert config.bounty == BountyConfig(0.5, 0.5, True)
    assert config.payout == (0.65, 0.35)
    # Same engine settings as the existing preset (only the display name differs).
    expected = preset_config(10, bounty=BountyConfig())
    assert config.blind_levels == expected.blind_levels
    assert config.starting_stack == expected.starting_stack


@pytest.mark.parametrize("stack", [10, 25, 50])
def test_stack_depth_table_size_and_payout_are_parameters(stack: int) -> None:
    spec = TournamentSpec.from_dict(
        {"players": 4, "starting_stack_bb": stack, "payout": "50/30/20", "pko": False}
    )

    config = spec.to_config()

    assert config.players == 4
    assert config.starting_stack == stack * 20
    assert config.payout == (0.5, 0.3, 0.2)
    assert config.bounty is None
    assert TournamentSpec.from_json(spec.to_json()) == spec


def test_custom_blind_levels_and_bounty_split() -> None:
    spec = TournamentSpec.from_dict(
        {
            "starting_stack_bb": 50,
            "bounty_share": 0.4,
            "cash_share": 1.0,
            "blind_levels": [
                {"small_blind": 50, "big_blind": 100, "ante": 12, "hands": 8},
                {"small_blind": 75, "big_blind": 150, "ante": 18, "hands": 8},
            ],
            "name": "lobby-event",
        }
    )

    config = spec.to_config()

    assert config.blind_levels == (BlindLevel(50, 100, 12, 8), BlindLevel(75, 150, 18, 8))
    assert config.starting_stack == 5_000
    assert config.bounty == BountyConfig(0.4, 1.0, True)
    assert config.name == "lobby-event"
    assert spec_from_config(config)["first_level"] == {
        "small_blind": 50,
        "big_blind": 100,
        "ante": 12,
        "hands": 8,
    }


def test_payout_formats() -> None:
    assert parse_payout("65-35") == (0.65, 0.35)
    assert parse_payout("50/30/20") == (0.5, 0.3, 0.2)
    assert parse_payout([0.7, 0.3]) == (0.7, 0.3)
    assert parse_payout("100") == (1.0,)
    for bad in ("60/30", [0.5, -0.1, 0.6], "abc", 3):
        with pytest.raises(ValueError):
            parse_payout(bad)


@pytest.mark.parametrize(
    "bad",
    [
        {"players": 7},
        {"players": 1},
        {"starting_stack_bb": 0},
        {"payout": "40/30/20/10/0/0/0"},
        {"bounty_share": 1.0},
        {"cash_share": 1.5},
        {"hands_per_level": 2.5},
        {"ante": "yes"},
        {"surprise": 1},
        {"blind_levels": [{"small_blind": 20, "big_blind": 10}]},
        {"starting_stack_bb": float("nan")},
    ],
)
def test_invalid_settings_are_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        TournamentSpec.from_dict(bad)


def test_json_is_stable() -> None:
    first = TournamentSpec.from_dict({"starting_stack_bb": 25}).to_json()
    second = TournamentSpec.from_json(first).to_json()

    assert first == second
    assert json.loads(first)["schema_version"] == "tableforge.tournament-spec.v1"
