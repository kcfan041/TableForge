from __future__ import annotations

from pathlib import Path

import pytest

from tableforge.learning.explain import RuleBotExplainer
from tableforge.learning.hand_import import Coach, import_hands
from tableforge.poker.cards import Card
from tableforge.poker.hand_history import (
    HandHistoryError,
    parse_hand,
    read_hand_histories,
    split_hands,
)
from tableforge.poker.tournament import BountyConfig, preset_config
from tableforge.worker.tournament import rule_bot

SAMPLE = (Path(__file__).parents[2] / "fixtures" / "gg_hands.txt").read_text(encoding="utf-8")


def test_hands_are_split_and_read() -> None:
    chunks = split_hands(SAMPLE)
    first = parse_hand(chunks[0][1], chunks[0][0])

    assert len(chunks) == 5
    assert first.hand_id == "TM1000000001" and first.tournament_id == "200000001"
    assert (first.level, first.small_blind, first.big_blind, first.ante) == (3, 40, 80, 10)
    assert first.names[1] == "Hero" and first.stacks[2] == 2_000
    assert first.bounties[2] == "$8.10"
    assert first.hero_cards == (Card.parse("Ah"), Card.parse("Kd"))
    assert first.shown["0a1b2c3d"] == (Card.parse("Qs"), Card.parse("Qd"))
    assert [str(card) for card in first.board] == ["2c", "7d", "Jh", "Ks", "3s"]


def test_hands_replay_on_the_engine_with_the_recorded_results() -> None:
    result = read_hand_histories(SAMPLE)

    assert [hand.parsed.hand_id for hand in result.hands] == [
        "TM1000000001",
        "TM1000000002",
        "TM1000000003",
        "TM1000000004",
    ]
    # Each hand ends with the stacks the next hand starts with.
    for before, after in zip(result.hands, result.hands[1:3], strict=False):
        final = before.table.result
        assert final is not None
        assert list(final.final_stacks) == after.parsed.stacks
    heads_up = result.hands[3].table.result
    assert heads_up is not None and heads_up.final_stacks == (15_000, 0)
    # The cut-off hand is reported with its line, not skipped silently.
    assert len(result.problems) == 1 and "TM1000000005" in result.problems[0]
    assert "line" in result.problems[0]


def test_the_import_keeps_hidden_cards_hidden_and_checks_payouts() -> None:
    imported = import_hands(SAMPLE)
    replay = imported["replay"]
    assert isinstance(replay, dict)
    hands = replay["hands"]
    assert isinstance(hands, list)

    assert imported["verified_against_real_exports"] is False
    assert imported["hero"] == ["Hero"] and imported["payout_mismatches"] == []
    assert replay["labels"][1] == "Hero"  # type: ignore[index]
    folded = hands[1]
    hero = replay["labels"].index("Hero")  # type: ignore[attr-defined]
    seat = folded["players"].index(hero)
    assert folded["hole_cards"][seat] == ["7c", "2d"]
    assert all(cards == [] for index, cards in enumerate(folded["hole_cards"]) if index != seat)
    assert hands[0]["showdown"] and hands[0]["winners"] == [hero]


def test_a_coach_reviews_the_heros_decisions() -> None:
    config = preset_config(10, players=6, bounty=BountyConfig())
    coach = Coach(lambda: RuleBotExplainer(rule_bot("tight", 1)), "rule", "tight", config)

    imported = import_hands(SAMPLE, coach)
    report = imported["report"]

    assert isinstance(report, dict)
    overall = report["overall"]
    assert isinstance(overall, dict) and overall["decisions"] == 5
    assert set(report["by"]["position"]) <= {"SB", "BB", "BTN", "UTG", "HJ", "CO"}  # type: ignore[index]


@pytest.mark.parametrize(
    "text, message",
    [
        ("hello", "no GG hands"),
        (SAMPLE.split("\n\n\n\n")[0].replace("Tournament #200000001, ", ""), "tournament"),
        (SAMPLE.split("\n\n\n\n")[0].replace("Level3(40/80(10))", "Level3"), "Level"),
        (
            SAMPLE.split("\n\n\n\n")[0].replace(
                "Hero: raises 990 to 1,190", "Hero: raises 990 to 9,190"
            ),
            "larger than the stack",
        ),
    ],
)
def test_problems_are_reported(text: str, message: str) -> None:
    try:
        result = read_hand_histories(text)
    except HandHistoryError as error:
        assert message in str(error)
        return
    assert result.hands == [] and message in result.problems[0]
