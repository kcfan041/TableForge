"""PPO on tournaments: collect on-policy experience, then learn from it (A1).

`collect` plays training tournaments with the current weights (the same lineups, rewards and
opponent pools as NFSP, `tournament_training.play_training_tournament`) and keeps each
learning seat's decisions together, so every episode is consecutive in the rollout.
`ppo_self_play_train` alternates collection and learning for local experiments; the
cluster pipeline splits the same two steps into actor and learner jobs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tableforge.learning.buffers import Transition
from tableforge.learning.opponents import OpponentPool
from tableforge.learning.ppo import PpoAgent, Rollout
from tableforge.learning.tournament_training import play_training_tournament
from tableforge.poker.tournament import TournamentConfig
from tableforge.poker.tournament_sampler import TournamentSampler


def collect(
    agent: PpoAgent,
    config: TournamentConfig | TournamentSampler,
    tournaments: int,
    seed_start: int,
    self_play_share: float = 0.5,
    hand_horizon: bool = False,
    opponents: OpponentPool | None = None,
    on_progress: Callable[[int], None] | None = None,
) -> tuple[list[Transition], int]:
    """Experience of `tournaments` training tournaments, episode by episode; and the hands."""
    transitions: list[Transition] = []
    hands = 0
    for index in range(tournaments):
        seats: dict[int, list[Transition]] = {}

        def seat_sink(
            player: int, seats: dict[int, list[Transition]] = seats
        ) -> Callable[[Transition], None]:
            return seats.setdefault(player, []).append

        episode = play_training_tournament(
            agent,
            config,
            seed_start + index,
            transitions.append,
            self_play_share,
            hand_horizon=hand_horizon,
            opponents=opponents,
            seat_sink=seat_sink,
        )
        hands += episode.hands
        for player in sorted(seats):
            transitions.extend(seats[player])
        if on_progress is not None and (index + 1) % 10 == 0:
            on_progress(index + 1)
    return transitions, hands


@dataclass(frozen=True, slots=True)
class PpoTrainingReport:
    tournaments: int
    hands: int
    updates: int
    train_steps: int
    decisions: int
    last_metrics: dict[str, float]


def ppo_self_play_train(
    agent: PpoAgent,
    config: TournamentConfig | TournamentSampler,
    tournaments: int,
    seed_start: int,
    tournaments_per_update: int = 250,
    self_play_share: float = 0.5,
    hand_horizon: bool = False,
    opponents: OpponentPool | None = None,
    on_progress: Callable[[int], None] | None = None,
) -> PpoTrainingReport:
    """Alternate: play `tournaments_per_update` tournaments, then one PPO update on them."""
    if tournaments_per_update < 1:
        raise ValueError("tournaments_per_update must be positive")
    played = hands = updates = decisions = 0
    metrics: dict[str, float] = {}
    features = agent.encoder.feature_size
    while played < tournaments:
        batch = min(tournaments_per_update, tournaments - played)
        transitions, batch_hands = collect(
            agent,
            config,
            batch,
            seed_start + played,
            self_play_share,
            hand_horizon,
            opponents,
        )
        played += batch
        hands += batch_hands
        if transitions:
            decisions += len(transitions)
            metrics = agent.learn(Rollout.of(transitions, features, agent.actions.count))
            updates += 1
        if on_progress is not None:
            on_progress(played)
    return PpoTrainingReport(
        tournaments=played,
        hands=hands,
        updates=updates,
        train_steps=agent.train_steps,
        decisions=decisions,
        last_metrics=metrics,
    )
