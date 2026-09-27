from __future__ import annotations

import math
from dataclasses import replace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tableforge.agents.rule import CallBot
from tableforge.poker.table import PotAward, TableRunner
from tableforge.poker.tournament import (
    PAYOUTS,
    BountyConfig,
    Bust,
    Tournament,
    TournamentConfig,
    preset_config,
    run_tournament,
    standard_levels,
)
from tableforge.poker.tournament_benchmark import evaluate_tournament
from tests.unit.poker.test_tournament import lineup


def pko(players: int = 6, stack_bb: float = 10, **bounty: object) -> TournamentConfig:
    return preset_config(stack_bb, "65-35", players, bounty=BountyConfig(**bounty))  # type: ignore[arg-type]


def test_bounty_config_validation_and_json() -> None:
    for bad in ({"bounty_share": 0}, {"bounty_share": 1}, {"cash_share": 1.5}):
        with pytest.raises(ValueError):
            BountyConfig(**bad)  # type: ignore[arg-type]
    config = pko(cash_share=0.4, winner_keeps_own=False)

    assert TournamentConfig.from_json(config.to_json()) == config
    assert config.starting_bounty == 0.5
    assert math.isclose(config.prize_pool, 3.0)
    assert "bounty" not in preset_config(10).to_json()


def run_hand(
    config: TournamentConfig, stacks: list[int], busted: list[int], pots: list[PotAward]
) -> Tournament:
    tournament = Tournament(config, seed=1)
    tournament.stacks = stacks
    seat_players = tuple(range(config.players))
    tournament._collect_bounties([Bust(player, 100) for player in busted], seat_players, pots)
    return tournament


def test_knockout_pays_half_in_cash_and_adds_half_to_the_winners_bounty() -> None:
    config = pko(players=3)
    tournament = run_hand(config, [300, 0, 200], [1], [PotAward(200, (0, 1), (0,))])

    assert tournament.bounty_cash == [0.25, 0.0, 0.0]
    assert tournament.heads == [0.75, 0.0, 0.5]
    assert tournament.knockouts[0].eliminated == 1 and tournament.knockouts[0].collector == 0


def test_bounty_goes_to_the_winner_of_the_pot_the_player_was_all_in_for() -> None:
    config = pko(players=3)
    # Seat 1 (short) was all in for the main pot won by seat 2; seat 0 won the side pot.
    tournament = run_hand(
        config,
        [400, 0, 100],
        [1],
        [PotAward(150, (0, 1, 2), (2,)), PotAward(200, (0, 2), (0,))],
    )

    assert tournament.bounty_cash == [0.0, 0.0, 0.25]


def test_split_pot_splits_the_bounty() -> None:
    config = pko(players=3, cash_share=1.0)
    tournament = run_hand(config, [150, 0, 150], [1], [PotAward(300, (0, 1, 2), (0, 2))])

    assert tournament.bounty_cash == [0.25, 0.0, 0.25]
    assert tournament.heads == [0.5, 0.0, 0.5]


@given(
    players=st.integers(2, 6),
    stack_bb=st.sampled_from([3, 10, 25]),
    payout=st.sampled_from(sorted(PAYOUTS)),
    bounty_share=st.sampled_from([0.25, 0.5, 0.75]),
    cash_share=st.sampled_from([0.0, 0.5, 1.0]),
    keeps=st.booleans(),
    seed=st.integers(0, 2**32),
)
@settings(max_examples=150, deadline=None)
def test_bounties_are_conserved(
    players: int,
    stack_bb: int,
    payout: str,
    bounty_share: float,
    cash_share: float,
    keeps: bool,
    seed: int,
) -> None:
    shares = PAYOUTS[payout][:players]
    shares = tuple(share / sum(shares) for share in shares)
    config = TournamentConfig(
        players,
        stack_bb,
        standard_levels(3),
        shares,
        bounty=BountyConfig(bounty_share, cash_share, keeps),
    )

    result = run_tournament(config, seed, lineup(players, seed), record_hands=True)  # type: ignore[arg-type]

    paid_in = config.starting_bounty * players
    winner = result.places.index(1)
    final = result.records[-1]
    left_on_winner = 0.0 if keeps else final.bounties_after[final.seat_players.index(winner)]
    # Bounties collected plus bounty left on the winner's head equal what was paid in.
    assert math.isclose(sum(result.bounties) + left_on_winner, paid_in, abs_tol=1e-9)
    # All money paid in comes back out when the winner keeps their own bounty.
    assert math.isclose(sum(result.results), -left_on_winner, abs_tol=1e-9)
    assert all(value >= 0 for value in result.bounties)
    assert {item.eliminated for item in result.knockouts} == set(range(players)) - {winner}
    cash = 0.0
    for record in result.records:
        cash += sum(knockout.cash for knockout in record.knockouts)
        # At every point: cash paid so far + bounties on heads = bounties paid in.
        assert math.isclose(cash + sum(record.bounties_after), paid_in, abs_tol=1e-9)
        for knockout in record.knockouts:
            assert knockout.collector not in record.busted
            assert knockout.eliminated in record.busted
            assert record.stacks_after[record.seat_players.index(knockout.collector)] > 0
    # Busted players hold no bounty afterwards.
    for record in result.records:
        for player in record.busted:
            assert record.bounties_after[record.seat_players.index(player)] == 0


def test_no_bounty_config_gives_the_same_tournament_as_before() -> None:
    config = preset_config(10)
    agents = lineup(6, 5)

    result = run_tournament(config, 5, agents)  # type: ignore[arg-type]

    assert result.bounties == (0.0,) * 6 and result.knockouts == ()
    assert math.isclose(config.prize_pool, 6.0)


def test_hand_info_reports_bounties_by_seat() -> None:
    seen: list[tuple[float, ...]] = []

    class Recorder(CallBot):
        def begin_hand(self, info: object) -> None:
            seen.append(info.bounties)  # type: ignore[attr-defined]

    run_tournament(pko(players=3), 1, [Recorder(), CallBot(), CallBot()])

    assert seen[0] == (0.5, 0.5, 0.5)


def test_bounty_ev_is_reported_separately() -> None:
    config = pko()
    report = evaluate_tournament(
        lambda: CallBot(),
        lambda player, seed: lineup(6, seed)[player],  # type: ignore[arg-type,return-value]
        config,
        seeds=10,
    )

    assert report.bounty_ev.mean > 0
    assert math.isclose(
        report.total_ev.mean,
        report.prize_ev.mean + report.bounty_ev.mean - config.buy_in,
        abs_tol=1e-9,
    )


def test_table_runner_and_replace_are_compatible() -> None:
    config = replace(pko(), name="x")
    assert Tournament(config, 1).run([CallBot() for _ in range(6)], runner=TableRunner()).hands > 0
