from __future__ import annotations

import math

import pytest

pytest.importorskip("torch")

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from tableforge.learning import encoding_v2 as v2  # noqa: E402
from tableforge.learning.buffers import Transition  # noqa: E402
from tableforge.learning.nfsp import NfspAgent, NfspConfig  # noqa: E402
from tableforge.learning.tournament_training import (  # noqa: E402
    FrozenTournamentPlayer,
    frozen_player,
    play_training_tournament,
    tournament_self_play_train,
)
from tableforge.poker.tournament import (  # noqa: E402
    BountyConfig,
    Tournament,
    preset_config,
)
from tableforge.poker.tournament_benchmark import evaluate_tournament  # noqa: E402
from tableforge.worker.tournament import field_factory  # noqa: E402


def agent(seed: int = 1, schema: str = v2.SCHEMA) -> NfspAgent:
    return NfspAgent(
        NfspConfig(
            hidden_size=16,
            batch_size=16,
            rl_capacity=5_000,
            sl_capacity=5_000,
            seed=seed,
            observation_schema=schema,
            reward_scale=1.0,
        )
    )


def pko(stack_bb: float = 10) -> object:
    return preset_config(stack_bb, bounty=BountyConfig())


def test_equity_starts_at_the_fair_share_and_ends_at_the_money_won() -> None:
    config = preset_config(10, bounty=BountyConfig())
    tournament = Tournament(config, 3)

    start = [tournament.equity(player) for player in range(6)]

    assert all(math.isclose(value, config.prize_pool / 6) for value in start)


@given(
    seed=st.integers(0, 10_000),
    share=st.sampled_from([0.0, 0.5, 1.0]),
    pko_rules=st.booleans(),
    stack=st.sampled_from([5, 10, 25]),
)
@settings(max_examples=25, deadline=None)
def test_rewards_of_every_learning_seat_add_up_to_its_final_result(
    seed: int, share: float, pko_rules: bool, stack: int
) -> None:
    config = preset_config(stack, bounty=BountyConfig() if pko_rules else None)
    transitions: list[Transition] = []

    episode = play_training_tournament(agent(seed), config, seed, transitions.append, share)

    assert "self" in episode.lineup and seed % 6 in episode.learner_players
    for player in episode.learner_players:
        total = sum(episode.rewards[player])
        final = episode.results[player] + config.buy_in
        assert math.isclose(total, final - episode.starting_equity, abs_tol=1e-9)
    # Transitions are linked across hands: at most one terminal transition per seat.
    terminal = [item for item in transitions if item.next_state is None]
    assert len(terminal) <= len(episode.learner_players)
    assert all(len(item.state) == v2.FEATURE_SIZE for item in transitions)
    # Every reward that reached a transition is one of the seats' rewards; seats that busted
    # before ever deciding add a reward with no transition.
    no_decision = len([p for p in episode.learner_players if len(episode.rewards[p])]) - len(
        terminal
    )
    assert len(transitions) + no_decision == sum(
        len(episode.rewards[p]) for p in episode.learner_players
    )


def test_tournament_fields_are_encoded_for_the_learner() -> None:
    learner = agent()
    seen: list[list[float]] = []
    original = learner.encode

    def spy(observation: object) -> list[float]:
        encoded = original(observation)  # type: ignore[arg-type]
        seen.append(encoded)
        return encoded

    learner.encode = spy  # type: ignore[method-assign]
    play_training_tournament(learner, pko(), 4, lambda item: None, 0.0)

    names = v2.feature_names()
    own_bounty = names.index("own_bounty")
    players_left = names.index("players_left")
    assert seen and all(vector[own_bounty] >= 1.0 - 1e-9 for vector in seen)
    assert {round(vector[players_left] * 6) for vector in seen} <= set(range(2, 7))
    assert max(vector[players_left] for vector in seen) == 1.0


def test_hand_horizon_ends_every_hand_and_keeps_the_rewards() -> None:
    terminals = []
    for seed in range(8):
        transitions: list[Transition] = []
        episode = play_training_tournament(
            agent(), pko(), seed, transitions.append, 0.0, hand_horizon=True
        )
        learner = episode.learner_players[0]
        assert math.isclose(
            sum(episode.rewards[learner]),
            episode.results[learner] + 1.0 - episode.starting_equity,
            abs_tol=1e-9,
        )
        # Every transition ends its hand: none links to a later hand.
        assert all(item.next_state is None for item in transitions[-1:])
        terminals.append(sum(1 for item in transitions if item.next_state is None))
    assert max(terminals) > 1


def test_double_q_trains() -> None:
    from dataclasses import replace

    learner = agent()
    learner = NfspAgent(replace(learner.config, double_q=True))

    report = tournament_self_play_train(learner, pko(), tournaments=3, seed_start=0)

    assert report.train_steps > 0
    assert NfspAgent.from_bundle(learner.to_bundle()).config.double_q is True


def test_greedy_player_uses_the_q_network_deterministically() -> None:
    from tableforge.learning.tournament_training import GreedyTournamentPlayer
    from tableforge.poker.tournament import run_tournament

    learner = agent()
    tournament_self_play_train(learner, pko(), tournaments=2, seed_start=0)
    bundle = learner.to_bundle()

    def play() -> tuple[float, ...]:
        players = [GreedyTournamentPlayer(NfspAgent.from_bundle(bundle)) for _ in range(6)]
        return run_tournament(pko(), 11, players).results

    assert play() == play()


def test_v1_agents_cannot_play_tournaments() -> None:
    with pytest.raises(ValueError):
        play_training_tournament(agent(schema="poker-hunlhe-vector-v1"), pko(), 1, print)


def test_training_runs_and_the_frozen_policy_plays_benchmarks() -> None:
    learner = agent()

    report = tournament_self_play_train(learner, pko(), tournaments=6, seed_start=0)
    player = frozen_player(learner.to_bundle())
    benchmark = evaluate_tournament(
        lambda: FrozenTournamentPlayer(player.agent), field_factory("mixed"), pko(), seeds=2
    )

    assert report.tournaments == 6 and report.hands > 0 and report.train_steps == report.hands
    assert report.transitions > 0 and report.policy_samples > 0
    assert benchmark.tournaments == 12
    assert math.isclose(
        benchmark.total_ev.mean,
        benchmark.prize_ev.mean + benchmark.bounty_ev.mean - 1.0,
        abs_tol=1e-9,
    )
