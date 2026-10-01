from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tableforge.agents.rule import AggroBot, CallBot, HeuristicPushFoldBot, RandomBot, TightBot
from tableforge.poker.betting import Action
from tableforge.poker.table import TableHand, TableRunner
from tableforge.poker.tournament import (
    PAYOUTS,
    BlindLevel,
    Bust,
    Tournament,
    TournamentConfig,
    icm_equities,
    preset_config,
    run_tournament,
    stack_at_hand_start,
    standard_levels,
)
from tableforge.poker.tournament_benchmark import Estimate, evaluate_tournament

# --- big blind ante ----------------------------------------------------------------------


def test_big_blind_ante_is_dead_money_won_by_the_pot_winner() -> None:
    hand = TableHand([100, 100, 100], 0, 1, 2, seed=1, big_blind_ante=2)

    assert hand.betting.pot == 5
    assert hand.betting.seats[2].stack == 96
    assert hand.betting.current_bet == 2
    hand.act(0, Action.fold())
    hand.act(1, Action.fold())

    assert hand.result is not None
    assert hand.result.final_stacks == (100, 99, 101)


def test_short_big_blind_posts_blind_before_ante() -> None:
    hand = TableHand([100, 100, 3], 0, 1, 2, seed=2, big_blind_ante=2)

    assert hand.betting.seats[2].street_contribution == 2
    assert hand.betting.dead_money == 1
    assert hand.betting.seats[2].all_in


def test_no_ante_behaves_exactly_as_before() -> None:
    with_default = TableHand([100, 90, 80], 1, 1, 2, seed=5)
    with_zero = TableHand([100, 90, 80], 1, 1, 2, seed=5, big_blind_ante=0)
    runner = TableRunner()

    first = runner.run(with_default, [RandomBot(index) for index in range(3)])
    second = runner.run(with_zero, [RandomBot(index) for index in range(3)])

    assert first == second


# --- config ------------------------------------------------------------------------------


def test_config_validation_and_round_trip() -> None:
    config = preset_config(25, "50-30-20")

    assert config.starting_stack == 25 * 20
    assert TournamentConfig.from_json(config.to_json()) == config
    with pytest.raises(ValueError):
        TournamentConfig(6, 10, standard_levels(), (0.6, 0.3))
    with pytest.raises(ValueError):
        TournamentConfig(6, 10, standard_levels(), (0.5,) * 7)
    with pytest.raises(ValueError):
        TournamentConfig(6, 10, (), (1.0,))
    with pytest.raises(ValueError):
        TournamentConfig(6, 10, standard_levels(), (1.0,), placement_resolver="coin_flip")
    with pytest.raises(ValueError):
        TournamentConfig(6, 0.5, standard_levels(), (1.0,))


@pytest.mark.parametrize("stack_bb", [10, 25, 50, 7.5])
def test_starting_stack_is_configurable(stack_bb: float) -> None:
    config = preset_config(stack_bb, "65-35")

    tournament = Tournament(config, seed=1)

    assert tournament.stacks == [round(stack_bb * 20)] * 6


def test_blinds_rise_by_hand_count_and_the_last_level_repeats() -> None:
    levels = (BlindLevel(1, 2, 0, 3), BlindLevel(2, 4, 4, 2), BlindLevel(5, 10, 10, 1))
    config = TournamentConfig(6, 50, levels, (1.0,))

    seen = [config.level_at(index) for index in range(8)]

    assert [number for number, _, _ in seen] == [1, 1, 1, 2, 2, 3, 3, 3]
    assert [left for _, _, left in seen[:5]] == [3, 2, 1, 2, 1]
    assert seen[4][1].ante == 4


# --- placement and ICM -------------------------------------------------------------------


def test_simultaneous_busts_are_ordered_by_starting_stack_and_tie() -> None:
    groups = stack_at_hand_start([Bust(1, 50), Bust(4, 80), Bust(2, 50)])

    assert groups == [[4], [1, 2]]


def test_tied_busts_share_the_prizes_of_their_places() -> None:
    config = TournamentConfig(4, 10, standard_levels(), (0.5, 0.3, 0.2))
    tournament = Tournament(config, seed=0)
    tournament.stacks = [100, 0, 0, 0]
    tournament._place([Bust(1, 60), Bust(2, 40), Bust(3, 40)], players_before=4)  # noqa: SLF001

    assert tournament.places[1:] == [2, 3, 3]
    assert tournament.prizes[1] == pytest.approx(0.3 * 4)
    assert tournament.prizes[2] == tournament.prizes[3] == pytest.approx(0.2 * 4 / 2)


def test_icm_matches_known_values() -> None:
    assert icm_equities([100, 100], [65, 35]) == pytest.approx([50, 50])
    equities = icm_equities([500, 300, 200], [50, 30, 20])
    assert sum(equities) == pytest.approx(100)
    # Hand-computed Malmuth-Harville value: 0.5*50 + 0.3*(30/0.5) ... = 38.3929.
    assert equities[0] == pytest.approx(
        25
        + 0.3 * (500 / 700) * 30
        + 0.3 * (200 / 700) * 20
        + 0.2 * (500 / 800) * 30
        + 0.2 * (300 / 800) * 20
    )
    assert equities[0] > equities[1] > equities[2]
    assert icm_equities([1000], [100]) == pytest.approx([100])


# --- whole tournaments -------------------------------------------------------------------

BOTS = (TightBot, CallBot, AggroBot, HeuristicPushFoldBot)


def lineup(players: int, seed: int) -> list[object]:
    agents: list[object] = []
    for player in range(players):
        choice = (seed + player) % 5
        agents.append(RandomBot(seed * 10 + player) if choice == 4 else BOTS[choice]())
    return agents


@given(
    players=st.integers(2, 9),
    stack_bb=st.sampled_from([3, 10, 25]),
    payout=st.sampled_from(sorted(PAYOUTS)),
    ante=st.booleans(),
    seed=st.integers(0, 2**32),
)
@settings(max_examples=150, deadline=None)
def test_tournament_invariants(
    players: int, stack_bb: int, payout: str, ante: bool, seed: int
) -> None:
    shares = PAYOUTS[payout][:players]
    shares = tuple(share / sum(shares) for share in shares)
    config = TournamentConfig(players, stack_bb, standard_levels(3, big_blind_ante=ante), shares)

    result = run_tournament(config, seed, lineup(players, seed), record_hands=True)  # type: ignore[arg-type]

    assert sorted(result.places) == sorted(result.places)
    assert result.places.count(1) == 1
    assert math.isclose(sum(result.prizes), config.prize_pool)
    assert math.isclose(sum(result.results), 0.0, abs_tol=1e-9)
    assert all(prize >= 0 for prize in result.prizes)
    order = [player for group in result.elimination_order for player in group]
    assert sorted(order) == list(range(players))
    assert result.elimination_order[-1] == (result.places.index(1),)
    for record in result.records:
        assert sum(record.stacks_before) == config.starting_stack * players
        assert sum(record.stacks_after) == config.starting_stack * players
        number, level, _ = config.level_at(record.hand_number)
        assert (record.level, record.big_blind) == (number, level.big_blind)
        assert math.isclose(
            sum(record.icm_before),
            sum(config.payout_for_place(place) for place in range(1, len(record.seat_players) + 1)),
        )
    # A player who finished better never busted before a player who finished worse.
    for earlier, later in zip(result.elimination_order, result.elimination_order[1:], strict=False):
        assert max(result.places[player] for player in earlier) >= min(
            result.places[player] for player in later
        )


@given(seed=st.integers(0, 2**32))
@settings(max_examples=40, deadline=None)
def test_replay_is_deterministic(seed: int) -> None:
    config = preset_config(10, "65-35")

    first = run_tournament(config, seed, lineup(6, seed), record_hands=True)  # type: ignore[arg-type]
    second = run_tournament(config, seed, lineup(6, seed), record_hands=True)  # type: ignore[arg-type]

    assert first == second


def test_button_skips_busted_players() -> None:
    config = TournamentConfig(4, 10, standard_levels(), (1.0,))
    tournament = Tournament(config, seed=3)
    tournament.button_player = 0
    tournament.hand_number = 1
    tournament.stacks = [300, 0, 350, 150]
    tournament.places[1] = 4
    tournament.play_hand([CallBot() for _ in range(4)], TableRunner())

    assert tournament.button_player == 2


def test_push_fold_bot_only_shoves_or_folds_preflop() -> None:
    config = preset_config(10, "65-35")
    tournament = Tournament(config, seed=4)
    bot = HeuristicPushFoldBot()
    agents: list[object] = [bot, CallBot(), CallBot(), CallBot(), CallBot(), CallBot()]
    seen: list[str] = []

    class Spy:
        def act(self, observation, legal):  # type: ignore[no-untyped-def]
            action = bot.act(observation, legal)
            if observation.street.name == "PREFLOP" and action.type.value == "raise_to":
                assert action.amount == legal.max_raise_to
            seen.append(action.type.value)
            return action

        def begin_hand(self, info):  # type: ignore[no-untyped-def]
            bot.begin_hand(info)

    agents[0] = Spy()
    while not tournament.complete and tournament.hand_number < 30:
        tournament.play_hand(agents, TableRunner())  # type: ignore[arg-type]

    assert set(seen) <= {"raise_to", "fold", "check", "call"}


# --- benchmark ---------------------------------------------------------------------------


def test_estimate_interval() -> None:
    estimate = Estimate.of([1.0, 2.0, 3.0, 4.0])

    assert estimate.mean == 2.5
    assert estimate.low < 2.5 < estimate.high


def test_identical_players_break_even_on_average() -> None:
    config = preset_config(10, "65-35")

    report = evaluate_tournament(CallBot, lambda player, seed: CallBot(), config, seeds=30)

    # Every seat of every seed is played by an identical CallBot, so the candidate's seat
    # rotation covers all six results of each tournament: the prize total is exactly the
    # pool. Places average slightly below 3.5 because tied busts share a place number.
    assert report.tournaments == 180
    assert report.total_ev.mean == pytest.approx(0.0, abs=1e-9)
    assert report.average_place.mean <= 3.5
    assert sum(report.place_share) == pytest.approx(1.0)
    assert report.prize_ev.mean == pytest.approx(1.0)
    assert report.bounty_ev.mean == 0.0


def test_benchmark_reports_every_metric_for_both_payouts() -> None:
    def field(player: int, seed: int) -> object:
        return (TightBot(), CallBot(), AggroBot(), RandomBot(seed), TightBot(), CallBot())[player]

    for payout in ("65-35", "50-30-20"):
        config = preset_config(10, payout)
        report = evaluate_tournament(HeuristicPushFoldBot, field, config, seeds=20)  # type: ignore[arg-type]
        summary = report.summary()
        for key in ("prize_ev", "bounty_ev", "total_ev", "roi", "average_place", "itm"):
            assert set(summary[key]) == {"mean", "low", "high"}  # type: ignore[arg-type]
        assert report.total_ev.mean == pytest.approx(report.prize_ev.mean - 1.0)
        assert report.roi.mean == pytest.approx(report.total_ev.mean)
