from __future__ import annotations

import hashlib
import os

import numpy as np
import pytest
import torch

from tableforge.learning.buffers import Transition
from tableforge.learning.ppo import (
    PpoAgent,
    PpoConfig,
    PpoTablePlayer,
    Rollout,
    checkpoint_manifest,
    clipped_loss,
    gae,
    magnet_kl,
)
from tableforge.learning.ppo_training import collect, ppo_self_play_train
from tableforge.poker.tournament import BountyConfig, preset_config
from tableforge.poker.tournament_benchmark import evaluate_tournament
from tableforge.worker.tournament import field_factory

V3 = {"observation_schema": "poker-nlhe-vector-v3", "action_schema": "nlhe-discrete-v2"}


def test_gae_matches_hand_computation() -> None:
    rewards = np.array([1.0, 0.0, 2.0, 5.0])
    values = np.array([0.5, 0.2, 1.0, 3.0])
    next_values = np.array([0.2, 1.0, 0.0, 0.0])
    done = np.array([False, False, True, True])
    gamma, lam = 0.9, 0.8

    advantages = gae(rewards, values, next_values, done, gamma, lam)

    delta2 = 2.0 - 1.0
    delta1 = 0.0 + gamma * 1.0 - 0.2
    delta0 = 1.0 + gamma * 0.2 - 0.5
    expected2 = delta2
    expected1 = delta1 + gamma * lam * expected2
    expected0 = delta0 + gamma * lam * expected1
    # The last episode is one decision; it does not bootstrap into the previous one.
    assert advantages == pytest.approx([expected0, expected1, expected2, 5.0 - 3.0])


def test_gae_with_lambda_one_is_the_return_minus_the_value() -> None:
    rewards = np.array([1.0, 2.0, 3.0])
    values = np.array([0.4, 0.3, 0.2])
    advantages = gae(
        rewards, values, np.array([0.3, 0.2, 0.0]), np.array([False, False, True]), 1.0, 1.0
    )

    assert advantages == pytest.approx([6.0 - 0.4, 5.0 - 0.3, 3.0 - 0.2])


def test_clipped_loss_stops_pushing_beyond_the_clip() -> None:
    old = torch.log(torch.tensor([0.5, 0.5]))
    advantages = torch.tensor([1.0, -1.0])
    inside = clipped_loss(torch.log(torch.tensor([0.55, 0.45])), old, advantages, 0.2)
    beyond = clipped_loss(torch.log(torch.tensor([0.9, 0.1])), old, advantages, 0.2)

    assert inside.item() == pytest.approx(-(1.1 * 1.0 + 0.9 * -1.0) / 2)
    # Ratios 1.8 and 0.2 are clipped to 1.2 and 0.8: the loss no longer improves.
    assert beyond.item() == pytest.approx(-(1.2 * 1.0 + 0.8 * -1.0) / 2)


def test_rollout_requires_finished_episodes() -> None:
    state = (0.0,) * 406
    mask = (True,) * 5
    unfinished = [Transition(state, mask, 1, 0.0, state, mask)]
    with pytest.raises(ValueError, match="not finished"):
        Rollout.of(unfinished, 406, 5)
    with pytest.raises(ValueError, match="no experience"):
        Rollout.of([], 406, 5)


def test_learn_moves_the_policy_towards_rewarded_actions() -> None:
    agent = PpoAgent(PpoConfig(seed=3, minibatch_size=64, entropy_coef=0.0))
    rng = np.random.default_rng(0)
    states = [tuple(float(value) for value in rng.normal(size=406)) for _ in range(256)]
    mask = (True,) * 5
    before = np.mean([agent.evaluate_state(state, mask)[0][1] for state in states])
    for _ in range(6):
        transitions = [
            Transition(state, mask, action, 1.0 if action == 1 else -1.0, None, None)
            for state in states
            for action in (agent.choose(state, mask),)
        ]
        agent.learn(Rollout.of(transitions, 406, 5))
    after = np.mean([agent.evaluate_state(state, mask)[0][1] for state in states])

    assert after > before + 0.3


def test_checkpoint_round_trip_continues_identically() -> None:
    config = preset_config(10, bounty=BountyConfig())
    agent = PpoAgent(PpoConfig(seed=2, hidden_size=16, minibatch_size=128))
    ppo_self_play_train(agent, config, 4, 100, 2, 0.75, True)
    bundle = agent.to_bundle({"round_index": 1})
    copy = PpoAgent.from_bundle(bundle)

    assert copy.to_bundle({"round_index": 1}) == bundle
    assert checkpoint_manifest(bundle)["algorithm"] == "ppo"
    assert checkpoint_manifest(bundle)["metadata"] == {"round_index": 1}
    ppo_self_play_train(agent, config, 2, 200, 2, 0.75, True)
    ppo_self_play_train(copy, config, 2, 200, 2, 0.75, True)
    assert (
        hashlib.sha256(agent.to_bundle()).hexdigest()
        == hashlib.sha256(copy.to_bundle()).hexdigest()
    )


def test_collect_keeps_each_seat_episode_together() -> None:
    config = preset_config(10, bounty=BountyConfig())
    agent = PpoAgent(PpoConfig(seed=4, hidden_size=16))
    transitions, hands = collect(agent, config, 3, 500, self_play_share=1.0)

    assert hands > 0
    rollout = Rollout.of(transitions, 406, 5)
    # Without the hand horizon, an episode is a seat's whole tournament: 6 seats x 3.
    assert int(rollout.done.sum()) <= 18
    assert bool(rollout.done[-1])


def test_v3_agent_plays_nine_handed_with_the_finer_bet_sizes() -> None:
    from tableforge.poker.tournament_spec import TournamentSpec

    config = TournamentSpec(players=9, starting_stack_bb=25).to_config()
    agent = PpoAgent(PpoConfig(seed=5, hidden_size=16, **V3))
    transitions, _ = collect(agent, config, 1, 7, self_play_share=1.0, hand_horizon=True)

    assert transitions and len(transitions[0].mask) == 10
    assert len(transitions[0].state) == agent.encoder.feature_size


def test_six_max_encoder_refuses_nine_players() -> None:
    from tableforge.poker.tournament_spec import TournamentSpec

    config = TournamentSpec(players=9, starting_stack_bb=25).to_config()
    agent = PpoAgent(PpoConfig(seed=5, hidden_size=16))
    with pytest.raises(ValueError, match="seats"):
        collect(agent, config, 1, 7)


def test_greedy_and_sampled_players_finish_a_benchmark() -> None:
    config = preset_config(10, bounty=BountyConfig())
    agent = PpoAgent(PpoConfig(seed=6, hidden_size=16))
    for greedy in (False, True):
        summary = evaluate_tournament(
            lambda greedy=greedy: PpoTablePlayer(agent, greedy),
            field_factory("mixed"),
            config,
            seeds=2,
            seed_start=11,
        ).summary()
        assert summary["tournaments"] == 12


def test_config_rejects_bad_values() -> None:
    with pytest.raises(ValueError):
        PpoConfig(clip=0)
    with pytest.raises(ValueError):
        PpoConfig(action_schema="nope")
    with pytest.raises(ValueError):
        PpoConfig(entropy_coef=-1)


# --- MMD (magnetic mirror descent) ------------------------------------------------------


def random_batch(agent: PpoAgent, count: int = 128, seed: int = 0) -> list[Transition]:
    rng = np.random.default_rng(seed)
    mask = (True,) * 5
    transitions = []
    for _ in range(count):
        state = tuple(float(value) for value in rng.normal(size=406))
        action = agent.choose(state, mask)
        transitions.append(Transition(state, mask, action, float(rng.normal()), None, None))
    return transitions


def test_magnet_kl_is_zero_for_the_same_policy_and_ignores_illegal_actions() -> None:
    log_probs = torch.log(torch.tensor([[0.5, 0.5, 0.0 + 1e-12]]))
    other = torch.log(torch.tensor([[0.25, 0.75, 1e-12]]))
    legal = torch.tensor([[True, True, False]])

    assert magnet_kl(log_probs, log_probs, legal).item() == pytest.approx(0.0)
    expected = 0.5 * np.log(0.5 / 0.25) + 0.5 * np.log(0.5 / 0.75)
    assert magnet_kl(log_probs, other, legal).item() == pytest.approx(expected, abs=1e-6)


def test_magnet_zero_is_plain_ppo() -> None:
    plain = PpoAgent(PpoConfig(seed=8, hidden_size=16, minibatch_size=64))
    explicit = PpoAgent(PpoConfig(seed=8, hidden_size=16, minibatch_size=64, magnet_coef=0.0))
    batch = random_batch(PpoAgent(PpoConfig(seed=8, hidden_size=16)))
    for agent in (plain, explicit):
        agent.learn(Rollout.of(batch, 406, 5))

    assert plain.magnet_network is None and explicit.magnet_network is None
    assert plain.to_bundle() == explicit.to_bundle()
    assert "magnet_coef" not in checkpoint_manifest(plain.to_bundle())["config"]


def test_magnet_pulls_the_policy_back() -> None:
    def drift(magnet_coef: float) -> float:
        agent = PpoAgent(
            PpoConfig(
                seed=9,
                hidden_size=16,
                minibatch_size=64,
                learning_rate=3e-3,
                entropy_coef=0.0,
                magnet_coef=magnet_coef,
                magnet_every=1_000,
            )
        )
        start = PpoAgent.from_bundle(agent.to_bundle())
        states = random_batch(agent, 64, seed=1)
        # Strongly reward action 1 so the policy wants to move away from where it started.
        for _ in range(15):
            batch = [
                Transition(item.state, item.mask, action, 1.0 if action == 1 else -1.0, None, None)
                for item in states
                for action in (agent.choose(item.state, item.mask),)
            ]
            agent.learn(Rollout.of(batch, 406, 5), epochs=4)
        mask = (True,) * 5
        distance = 0.0
        for item in states:
            now, _ = agent.evaluate_state(item.state, mask)
            before, _ = start.evaluate_state(item.state, mask)
            distance += sum(abs(a - b) for a, b in zip(now, before, strict=True))
        return distance / len(states)

    assert drift(5.0) < drift(0.0) * 0.7


def test_magnet_moves_to_the_policy_every_n_updates() -> None:
    agent = PpoAgent(
        PpoConfig(seed=10, hidden_size=16, minibatch_size=64, magnet_coef=0.1, magnet_every=2)
    )
    assert agent.magnet_network is not None
    batch = Rollout.of(random_batch(agent), 406, 5)

    def same() -> bool:
        assert agent.magnet_network is not None
        return all(
            torch.equal(a, b)
            for a, b in zip(
                agent.magnet_network.state_dict().values(),
                agent.policy_network.state_dict().values(),
                strict=True,
            )
        )

    assert same()
    metrics = agent.learn(batch)
    assert "magnet_kl" in metrics and agent.updates == 1 and not same()
    agent.learn(batch)
    assert agent.updates == 2 and same()


def test_mmd_checkpoint_round_trip_and_old_checkpoints_load_with_mmd() -> None:
    agent = PpoAgent(
        PpoConfig(seed=11, hidden_size=16, minibatch_size=64, magnet_coef=0.2, magnet_every=3)
    )
    batch = Rollout.of(random_batch(agent), 406, 5)
    agent.learn(batch)
    bundle = agent.to_bundle()
    copy = PpoAgent.from_bundle(bundle)

    assert copy.to_bundle() == bundle
    assert copy.updates == 1
    assert checkpoint_manifest(bundle)["config"]["magnet_coef"] == 0.2
    agent.learn(batch)
    copy.learn(batch)
    assert agent.to_bundle() == copy.to_bundle()
    with pytest.raises(ValueError):
        PpoConfig(magnet_coef=-1)
    with pytest.raises(ValueError):
        PpoConfig(magnet_every=0)


def test_diagnostics_are_reported_without_changing_training() -> None:
    """Diagnostics only read; the checkpoint of a fixed training run is unchanged."""
    import sys

    agent = PpoAgent(PpoConfig(seed=12, hidden_size=16, minibatch_size=64))
    batch = Rollout.of(random_batch(agent, 96, seed=4), 406, 5)
    agent.learn(batch)
    diagnostics = agent.last_diagnostics

    assert diagnostics["approx_kl"] >= 0
    assert 0 <= diagnostics["clip_fraction"] <= 1
    assert diagnostics["decisions"] == 96
    assert {"explained_variance", "advantage_std", "policy_grad_norm", "value_grad_norm"} <= set(
        diagnostics
    )
    if sys.platform == "win32":
        # Recorded before diagnostics existed (same run, same machine).
        config = preset_config(10, bounty=BountyConfig())
        for magnet, expected in (
            (0.0, "ceca261318fe6b7d083718dbf32233b9103ce243f0215dfb3fa975117b129053"),
            (0.2, "5b38065701231e124e0c906822af425133cb25ba5a72eecb2cc4381ee4a00cfc"),
        ):
            torch.set_num_threads(1)
            trained = PpoAgent(
                PpoConfig(
                    seed=7, hidden_size=16, minibatch_size=128, magnet_coef=magnet, magnet_every=2
                )
            )
            report = ppo_self_play_train(trained, config, 6, 300, 2, 0.75, True)
            assert len(report.diagnostics) == 3
            digest = hashlib.sha256(trained.to_bundle()).hexdigest()
            if os.environ.get("CI"):
                continue
            assert digest == expected


def test_separate_value_network_fits_returns_better_and_round_trips() -> None:
    rng = np.random.default_rng(5)
    mask = (True,) * 5
    states = [tuple(float(value) for value in rng.normal(size=406)) for _ in range(256)]
    # The return depends on the first feature, plus a lot of noise (like all-in luck).
    batch = Rollout.of(
        [
            Transition(state, mask, 1, 0.3 * (2 * state[0] + float(rng.normal() * 2)), None, None)
            for state in states
        ],
        406,
        5,
    )

    def fit(mode: str) -> float:
        agent = PpoAgent(PpoConfig(seed=3, hidden_size=32, minibatch_size=64, value_mode=mode))
        for _ in range(6):
            agent.learn(batch)
        return agent.last_diagnostics["explained_variance_after"]

    assert fit("separate") > fit("shared")

    agent = PpoAgent(PpoConfig(seed=4, hidden_size=16, minibatch_size=64, value_mode="separate"))
    agent.learn(batch)
    bundle = agent.to_bundle()
    copy = PpoAgent.from_bundle(bundle)
    assert copy.to_bundle() == bundle
    assert checkpoint_manifest(bundle)["config"]["value_mode"] == "separate"
    agent.learn(batch)
    copy.learn(batch)
    assert agent.to_bundle() == copy.to_bundle()
    with pytest.raises(ValueError):
        PpoConfig(value_mode="other")
