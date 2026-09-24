from __future__ import annotations

import pytest

from tableforge.domain.league import (
    RULE_BOTS,
    HistoricalCheckpoint,
    LeagueMatchupPayload,
    LeagueMember,
    LeaguePopulation,
    MemberKind,
    build_matrix,
    build_population,
)

SHA = "a" * 64


def test_population_without_history_gives_history_weight_to_self() -> None:
    population = build_population(1, SHA, ())
    weights = {member.name: member.weight for member in population.members}

    assert weights["self"] == pytest.approx(0.7)
    assert sum(weights.values()) == pytest.approx(1.0)
    assert {f"rule:{rule}" for rule in RULE_BOTS} <= set(weights)


def test_population_splits_history_weight_and_round_trips() -> None:
    historical = (
        HistoricalCheckpoint("round-0", "art-0", "b" * 64),
        HistoricalCheckpoint("round-1", "art-1", "c" * 64),
    )
    population = build_population(3, SHA, historical)

    assert LeaguePopulation.from_bytes(population.to_bytes()) == population
    weights = {member.name: member.weight for member in population.members}
    assert weights["round-0"] == pytest.approx(0.15)
    assert weights["self"] == pytest.approx(0.4)


def test_population_rejects_duplicate_names() -> None:
    member = LeagueMember("dup", MemberKind.RULE, 1.0, rule="call")
    with pytest.raises(ValueError, match="unique"):
        LeaguePopulation(1, SHA, (member, member))


def test_matchup_payload_rejects_self_and_mirror() -> None:
    rule = LeagueMember("rule:call", MemberKind.RULE, rule="call")
    with pytest.raises(ValueError):
        LeagueMatchupPayload("1.0", rule, rule, 10, 0)
    with pytest.raises(ValueError):
        LeagueMatchupPayload("1.0", LeagueMember("self", MemberKind.SELF), rule, 10, 0)


def test_matrix_is_antisymmetric() -> None:
    matrix = build_matrix(
        ("a", "b"),
        [
            {
                "first": "a",
                "second": "b",
                "bb_per_100": 12.0,
                "confidence_low": -3.0,
                "confidence_high": 27.0,
                "hands": 400,
            }
        ],
    )

    assert matrix["a"]["b"].bb_per_100 == 12.0
    assert matrix["b"]["a"].bb_per_100 == -12.0
    assert (matrix["b"]["a"].confidence_low, matrix["b"]["a"].confidence_high) == (-27.0, 3.0)
