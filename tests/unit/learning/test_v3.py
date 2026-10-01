from __future__ import annotations

import pytest

from tableforge.learning import encoding_v3 as v3
from tableforge.learning.actions import V2, Actions, Spot
from tableforge.poker.betting import Action, Street
from tableforge.poker.table import TableHand
from tableforge.poker.tournament_sampler import TournamentSampler


def first_decision(players: int, stack: int = 2_000) -> tuple[TableHand, int]:
    hand = TableHand([stack] * players, 0, 10, 20, seed=5)
    actor = hand.betting.current_actor
    assert actor is not None
    return hand, actor


def test_preflop_sizes_open_and_reraise_in_big_blinds() -> None:
    hand, actor = first_decision(6)
    legal = hand.legal_actions(actor)
    mask = V2.legal_mask(legal, Street.PREFLOP)
    spot = Spot(Street.PREFLOP, hand.betting.pot, hand.betting.current_bet, 0, 20)

    assert mask[:3] == (True, True, True)
    assert mask[3:6] == (True, True, True) and mask[6:] == (False,) * 4
    assert [V2.to_action(index, legal, spot).amount for index in (3, 4, 5)] == [40, 50, 60]
    hand.act(actor, Action.raise_to(50))
    nxt = hand.betting.current_actor
    assert nxt is not None
    legal = hand.legal_actions(nxt)
    spot = Spot(Street.PREFLOP, hand.betting.pot, hand.betting.current_bet, 0, 20)
    # Facing a raise to 50: 2x is below the minimum re-raise (80), so it is clamped.
    assert [V2.to_action(index, legal, spot).amount for index in (3, 4, 5)] == [100, 125, 150]


def test_postflop_sizes_are_fractions_of_the_pot() -> None:
    hand, actor = first_decision(2)
    hand.act(actor, Action.call())
    hand.act(hand.betting.current_actor or 0, Action.check())
    actor = hand.betting.current_actor
    assert actor is not None and hand.betting.street is Street.FLOP
    legal = hand.legal_actions(actor)
    mask = V2.legal_mask(legal, Street.FLOP)
    spot = Spot(Street.FLOP, hand.betting.pot, 0, 0, 20)

    assert mask[3:6] == (False,) * 3 and mask[6:] == (True,) * 4
    amounts = [V2.to_action(index, legal, spot).amount for index in range(6, 10)]
    assert amounts == [20, 26, 40, 60]  # 33% (clamped to the 1 BB minimum), 66, 100, 150 of 40


def test_v1_actions_are_unchanged() -> None:
    actions = Actions("nlhe-discrete-v1")
    assert actions.count == 5
    assert actions.canonical == ("fold", "check_call", "raise", "raise", "all_in")
    assert Actions("nlhe-discrete-v2").count == 10
    with pytest.raises(ValueError):
        Actions("nlhe-discrete-v9")


@pytest.mark.parametrize("players", [2, 6, 9])
def test_v3_encodes_every_table_size(players: int) -> None:
    hand, actor = first_decision(players)
    context = v3.StructureContext(
        starting_stack=2_000,
        level=1,
        hands_to_next_level=10,
        hands_per_level=10,
        players_left=players,
        field_size=players,
        payout=(0.5, 0.3, 0.2),
        bounties=(1.0,) * players,
        bounty_share=0.5,
        cash_share=0.5,
    )
    features = v3.encode(hand.observe(actor), 20, 10, context)
    cash = v3.encode(hand.observe(actor), 20, 10, None)

    assert len(features) == v3.FEATURE_SIZE == len(cash)
    assert all(abs(value) <= 25 for value in features)
    assert features != cash


def test_v3_tells_structures_apart() -> None:
    hand, actor = first_decision(6)
    observation = hand.observe(actor)

    def encoded(**changes: object) -> list[float]:
        values: dict[str, object] = {
            "starting_stack": 2_000,
            "level": 1,
            "hands_to_next_level": 10,
            "hands_per_level": 10,
            "players_left": 6,
            "field_size": 6,
            "payout": (0.65, 0.35),
        }
        values.update(changes)
        return v3.encode(observation, 20, 10, v3.StructureContext(**values))  # type: ignore[arg-type]

    base = encoded()
    for change in (
        {"hands_per_level": 5},
        {"blind_growth": 1.5},
        {"payout": (1.0,)},
        {"bounty_share": 0.5, "cash_share": 0.5},
        {"field_size": 9},
    ):
        assert encoded(**change) != base, change


def test_sampler_is_reproducible_and_covers_its_ranges() -> None:
    sampler = TournamentSampler(players=(2, 9), starting_stack_bb=(10, 40))
    configs = [sampler.config(seed) for seed in range(200)]

    assert sampler.config(7) == sampler.config(7)
    assert {config.players for config in configs} == {2, 9}
    stacks = [config.starting_stack_bb for config in configs]
    assert min(stacks) >= 10 and max(stacks) <= 40 and max(stacks) - min(stacks) > 20
    assert any(config.bounty is None for config in configs)
    assert any(config.bounty is not None for config in configs)
    for config in configs:
        bigs = [level.big_blind for level in config.blind_levels]
        assert bigs == sorted(bigs) and len(set(bigs)) == len(bigs)
        assert len(config.payout) <= config.players
    for bad in ({"players": [10]}, {"starting_stack_bb": [40, 10]}, {"payouts": [[0.5, 0.4]]}):
        with pytest.raises(ValueError):
            TournamentSampler.from_dict(bad)
