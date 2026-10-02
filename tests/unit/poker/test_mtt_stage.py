from __future__ import annotations

from dataclasses import replace

import pytest

np = pytest.importorskip("numpy")

from tableforge.poker.betting import Action  # noqa: E402
from tableforge.poker.field_icm import FieldIcm  # noqa: E402
from tableforge.poker.mtt_stage import STAGES, StageSpec, mtt_prizes  # noqa: E402
from tableforge.poker.tournament import (  # noqa: E402
    BountyConfig,
    Tournament,
    TournamentConfig,
    icm_equities,
    preset_config,
)
from tableforge.poker.tournament_sampler import TournamentSampler  # noqa: E402
from tableforge.poker.tournament_spec import TournamentSpec  # noqa: E402
from tableforge.worker.tournament import field_factory  # noqa: E402


def stage_config(stage: str = "bubble", pko: bool = True) -> TournamentConfig:
    return replace(
        preset_config(25, players=9, bounty=BountyConfig() if pko else None), stage=STAGES[stage]
    )


@pytest.mark.parametrize(("players", "paid"), [(10, 3), (12, 5), (13, 9), (11, 11)])
def test_field_icm_matches_exact_icm(players: int, paid: int) -> None:
    rng = np.random.default_rng(players)
    prizes = [float(value) for value in sorted(rng.uniform(1, 20, paid), reverse=True)]
    for _ in range(4):
        stacks = [int(value) for value in np.maximum(1, rng.lognormal(0, 0.8, players) * 1000)]
        exact = icm_equities(stacks, prizes)
        table = stacks[:9]
        approx = FieldIcm(stacks[9:], prizes).equities(table)
        assert approx == pytest.approx(exact[:9], abs=5e-4)


def test_field_icm_adds_up_to_the_prizes_left() -> None:
    stage = STAGES["bubble"]
    rng = np.random.default_rng(0)
    stacks = rng.lognormal(0, 0.7, stage.players_left) * 1000
    total = 0.0
    for start in range(0, stage.players_left, 9):
        rest = np.concatenate([stacks[:start], stacks[start + 9 :]])
        total += sum(FieldIcm(rest, stage.prizes).equities(stacks[start : start + 9]))
    assert total == pytest.approx(sum(stage.prizes[: stage.players_left]), rel=1e-5)


def test_table_ladder_shows_the_bubble() -> None:
    stage = STAGES["bubble"]
    rng = np.random.default_rng(1)
    others = rng.lognormal(0, 0.7, stage.players_left - 9) * 2500
    ladder = FieldIcm(others, stage.prizes).table_ladder([2500.0] * 9)
    deep = FieldIcm(others[:20], stage.prizes).table_ladder([2500.0] * 9)

    assert ladder == sorted(ladder, reverse=True)
    # Ten players off the money: the last place at our table is worth little more than 0.
    assert ladder[-1] < stage.prize(stage.paid)
    assert deep[-1] >= stage.prize(stage.paid) * 0.99


def test_stage_tournament_conserves_chips_and_bounties_and_ends_on_time() -> None:
    field = field_factory("mixed")
    for name, stage in STAGES.items():
        config = stage_config(name)
        for seed in range(5):
            tournament = Tournament(config, seed)
            chips = tournament.total_chips
            heads = sum(tournament.heads)
            result = tournament.run([field(player, seed * 16 + player) for player in range(9)])

            assert sum(tournament.stacks) == chips
            assert sum(result.bounties) + sum(tournament.heads) == pytest.approx(heads)
            assert tournament.field_left <= stage.end_players or len(tournament.alive) == 1
            assert all(1 <= place <= stage.players_left for place in result.places)
            for player, place in enumerate(result.places):
                if tournament.stacks[player] == 0:
                    assert result.prizes[player] == pytest.approx(stage.prize(place))


def test_stage_results_are_equity_won_or_lost() -> None:
    """Players who only fold lose blinds, but the field busting raises their equity."""

    class Folder:
        def act(self, observation, legal):  # type: ignore[no-untyped-def]
            return Action.check() if legal.can_check else Action.fold()

    config = replace(preset_config(60, players=9), stage=STAGES["bubble"])
    tournament = Tournament(config, 3)
    start = list(tournament.starting_equity)
    result = tournament.run([Folder() for _ in range(9)])

    assert sum(start) == pytest.approx(sum(tournament.starting_equity))
    for player in range(9):
        assert result.results[player] == pytest.approx(
            result.prizes[player] + result.bounties[player] - start[player]
        )
    # Nobody at our table busted; the field moved closer to the money.
    assert tournament.field is not None and len(tournament.field.busts) > 0
    assert sum(result.results) > 0


def test_equity_reward_matches_the_final_result() -> None:
    field = field_factory("mixed")
    config = stage_config("final_tables")
    tournament = Tournament(config, 4)
    agents = [field(player, player) for player in range(9)]
    from tableforge.poker.table import TableRunner

    runner = TableRunner()
    while not tournament.complete:
        tournament.play_hand(agents, runner)
    before = [tournament.equity(player) for player in range(9)]
    result = tournament.finish()
    for player in range(9):
        final = result.prizes[player] + result.bounties[player]
        # Bounties still on heads are not in the equity; everything else matches.
        assert final == pytest.approx(before[player], abs=1e-9)


def test_hand_info_describes_the_field() -> None:
    config = stage_config("bubble")
    tournament = Tournament(config, 5)
    infos = []

    class Recorder:
        def begin_hand(self, info):  # type: ignore[no-untyped-def]
            infos.append(info)

        def act(self, observation, legal):  # type: ignore[no-untyped-def]
            return Action.check() if legal.can_check else Action.fold()

    from tableforge.poker.table import TableRunner

    tournament.play_hand([Recorder() for _ in range(9)], TableRunner())
    info = infos[0]
    assert info.stage is not None
    assert info.stage.field_left == 160 and info.stage.paid == 150
    assert info.stage.to_money == 10 and info.stage.next_prize == 0.0
    assert len(info.prizes) == 9 and info.prizes == tuple(sorted(info.prizes, reverse=True))


def test_single_table_tournaments_are_unchanged() -> None:
    config = preset_config(10, bounty=BountyConfig())
    assert '"stage"' not in config.to_json()
    assert TournamentConfig.from_json(config.to_json()) == config
    staged = stage_config()
    assert TournamentConfig.from_json(staged.to_json()) == staged


def test_spec_and_sampler_accept_stages() -> None:
    spec = TournamentSpec.from_dict({"players": 9, "stage": "itm", "starting_stack_bb": 30})
    custom = TournamentSpec.from_dict(
        {"players": 8, "stage": {"base": "bubble", "players_left": 200, "end_players": 150}}
    )
    assert spec.to_config().stage == STAGES["itm"]
    assert custom.to_config().stage is not None
    assert custom.to_config().stage.players_left == 200
    with pytest.raises(ValueError, match="unknown stage"):
        TournamentSpec.from_dict({"stage": "day2"})

    plain = TournamentSampler(players=(6,))
    assert "stage" not in plain.to_json()
    mixed = TournamentSampler(players=(6, 9), stages=("bubble",), stage_share=0.5)
    configs = [mixed.config(seed) for seed in range(40)]
    staged = [config for config in configs if config.stage is not None]
    assert 5 < len(staged) < 35 and all(config.players == 9 for config in staged)
    # Seeds that are not stages get the same structure as without stages.
    same_players = TournamentSampler(players=(6, 9))
    for seed, config in enumerate(configs):
        if config.stage is None:
            assert config == same_players.config(seed)
    assert TournamentSampler.from_json(mixed.to_json()) == mixed
    with pytest.raises(ValueError):
        TournamentSampler(stages=("day2",), stage_share=0.5)
    # Samplers without stages draw exactly what they drew before stages existed.
    before = TournamentSampler(players=(2, 9))
    assert [before.config(seed).name for seed in range(5)] == [f"mixed:{seed}" for seed in range(5)]


def test_stage_spec_is_checked() -> None:
    with pytest.raises(ValueError):
        StageSpec(1_001, 160, mtt_prizes(1_000), end_players=100)
    with pytest.raises(ValueError):
        StageSpec(1_000, 160, (1.0, 2.0), end_players=100)
    with pytest.raises(ValueError):
        StageSpec(1_000, 160, mtt_prizes(1_000), end_players=160)
    assert sum(mtt_prizes(1_000)) == pytest.approx(1_000, rel=1e-6)


def test_pushfold_nash_is_tighter_on_the_bubble() -> None:
    """The ICM Push/Fold Nash baseline calls all-ins less on the bubble than with chip EV."""
    from tableforge.learning.pushfold_nash import PushFoldGame, solve, weights

    stage = STAGES["bubble"]
    rng = np.random.default_rng(3)
    table = [1000, 3000, 3000, 2500, 2500, 2000, 2500, 3000, 2500]
    others = rng.lognormal(0, 0.7, stage.players_left - 9) * 2500
    bubble = tuple(FieldIcm(others, stage.prizes).table_ladder(table))
    chip_ev = (1.0,) + (0.0,) * 8
    w = np.array(weights())

    def call_share(ladder: tuple[float, ...]) -> float:
        solution = solve(PushFoldGame(tuple(table), 50, 100, 100, ladder), 600, 0.0005)
        return float(np.array(solution.call[(0, 1)]) @ w / w.sum())

    assert call_share(bubble) < call_share(chip_ev) - 0.05
