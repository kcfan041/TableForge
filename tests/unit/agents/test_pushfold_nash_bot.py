from __future__ import annotations

import pytest

pytest.importorskip("numpy")

from tableforge.agents.rule.pushfold_nash import PushFoldNashBot, hand_class  # noqa: E402
from tableforge.poker.betting import ActionType  # noqa: E402
from tableforge.poker.cards import Card  # noqa: E402
from tableforge.poker.table import TableHand  # noqa: E402
from tableforge.poker.tournament import (  # noqa: E402
    BountyConfig,
    Tournament,
    TournamentHandInfo,
    preset_config,
)
from tableforge.worker.tournament import rule_bot  # noqa: E402


def cards(text: str) -> tuple[Card, Card]:
    first, second = text.split()
    return Card.parse(first), Card.parse(second)


def test_hand_classes() -> None:
    assert hand_class(cards("As Kd")) == "AKo"
    assert hand_class(cards("9h Th")) == "T9s"
    assert hand_class(cards("7c 7d")) == "77"


def info(players: int, stack_bb: float) -> TournamentHandInfo:
    return TournamentHandInfo(
        hand_number=0,
        level=1,
        small_blind=10,
        big_blind=20,
        ante=0,
        hands_to_next_level=10,
        hands_per_level=10,
        players_left=players,
        starting_stack=int(stack_bb * 20),
        payout=(1.0,),
        seat=0,
        player=0,
        seat_players=tuple(range(players)),
        prizes=(float(players),) + (0.0,) * (players - 1),
    )


def test_heads_up_decisions_follow_the_chart() -> None:
    bot = PushFoldNashBot()
    bot.begin_hand(info(2, 10))
    # 10 BB heads-up, winner takes all: the button shoves K2o, folds Q2o (chart 11.6 / 7.0).
    for hole, shove in (("Kc 2d", True), ("Qc 2d", False), ("Ac 7d", True)):
        hand = TableHand([200, 200], 0, 10, 20, seed=1)
        observation = hand.observe(0)
        from dataclasses import replace

        observation = replace(observation, hole_cards=cards(hole))
        action = bot.act(observation, hand.legal_actions(0))
        assert (action.type is ActionType.RAISE_TO) is shove, hole


def test_plays_whole_pko_tournaments() -> None:
    config = preset_config(10, players=6, bounty=BountyConfig())
    agents = [rule_bot("pushfold_nash", seed) for seed in range(6)]
    for seed in range(3):
        result = Tournament(config, seed).run(agents)
        assert sorted(result.places)[0] == 1
    from tableforge.agents.rule import pushfold_nash

    assert all(isinstance(agent, PushFoldNashBot) for agent in agents)
    assert len(pushfold_nash._CACHE) > 0
