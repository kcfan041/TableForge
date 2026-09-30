"""NFSP training on tournaments (T3).

One learning agent plays whole tournaments. The learner always has one seat (which rotates
with the seed); every other seat is, independently, the same agent (self-play) with
probability `self_play_share`, or a fixed opponent drawn from `OPPONENT_POOL` (rule bots)
or, when an `OpponentPool` is given, from that pool (rule bots, frozen models from the
model library and earlier versions of the learner, optionally weighted by PFSP).

Reward (docs/architecture/08-tournament.md, section 6): after every hand each learning
seat receives the change in its tournament equity (`Tournament.equity`: ICM share of the
remaining prizes plus bounty cash collected). Transitions are linked across hands and only
busting or the end of the tournament is terminal, so one seat's rewards add up to its
final prize plus bounty cash minus its starting equity: the final result plus a constant.
This is potential-based shaping; it does not change which strategy is best, it only
reports progress after every hand instead of once per tournament.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from tableforge.learning.buffers import Transition
from tableforge.learning.nfsp import NfspAgent
from tableforge.learning.opponents import OpponentPool, PoolMember
from tableforge.poker.betting import Action, LegalActions
from tableforge.poker.table import TableAgent, TableObservation, TableRunner
from tableforge.poker.tournament import Tournament, TournamentConfig, TournamentHandInfo
from tableforge.worker.tournament import rule_bot

OPPONENT_POOL = ("random", "call", "tight", "aggro", "pushfold", "hunter")


@dataclass
class _Pending:
    state: tuple[float, ...]
    mask: tuple[bool, ...]
    action: int


@dataclass
class LearningSeat:
    """One seat played by the shared learning agent; records its own transitions."""

    agent: NfspAgent
    player: int
    sink: Callable[[Transition], None]
    info: TournamentHandInfo | None = None
    pending: _Pending | None = None
    reward: float = 0.0
    """Equity change accumulated since `pending` was chosen."""
    equity: float = 0.0
    done: bool = False
    rewards: list[float] = field(default_factory=list)
    hand_horizon: bool = False
    """End every hand's last decision as terminal (learn the equity change of one hand)."""

    def begin_hand(self, info: TournamentHandInfo) -> None:
        self.info = info

    def act(self, observation: TableObservation, legal: LegalActions) -> Action:
        self.agent.hand_info = self.info
        action = self.agent.act(observation, legal)
        if self.agent.last_choice is None or self.agent.last_state is None:
            raise AssertionError("agent must record its decision")
        state, mask = self.agent.last_state
        if self.pending is not None:
            self._emit(state, mask)
        self.pending = _Pending(state, mask, int(self.agent.last_choice))
        return action

    def after_hand(self, equity: float, finished: bool) -> None:
        self.reward += equity - self.equity
        self.equity = equity
        if self.hand_horizon and not finished and self.pending is not None:
            self._emit(None, None)
            return
        if finished and not self.done:
            self.done = True
            if self.pending is not None:
                self._emit(None, None)
            else:
                # Busted without making a decision (e.g. all in from the blinds).
                self.rewards.append(self.reward)
                self.reward = 0.0

    def _emit(self, state: tuple[float, ...] | None, mask: tuple[bool, ...] | None) -> None:
        assert self.pending is not None
        self.sink(
            Transition(
                self.pending.state,
                self.pending.mask,
                self.pending.action,
                self.reward,
                state,
                mask,
            )
        )
        self.rewards.append(self.reward)
        self.reward = 0.0
        self.pending = None


@dataclass(frozen=True, slots=True)
class TournamentEpisode:
    learner_players: tuple[int, ...]
    lineup: tuple[str, ...]
    """Kind of every player id; "self" for learning seats."""
    hands: int
    results: tuple[float, ...]
    rewards: dict[int, tuple[float, ...]]
    starting_equity: float


def play_training_tournament(
    agent: NfspAgent,
    config: TournamentConfig,
    seed: int,
    sink: Callable[[Transition], None],
    self_play_share: float = 0.5,
    after_hand: Callable[[], None] | None = None,
    pool: Sequence[str] = OPPONENT_POOL,
    hand_horizon: bool = False,
    opponents: OpponentPool | None = None,
) -> TournamentEpisode:
    """Play one tournament with the learning agent in one or more seats."""
    if not 0 <= self_play_share <= 1:
        raise ValueError("self_play_share must be between 0 and 1")
    if not agent.encoder.supports_tables:
        raise ValueError(f"{agent.encoder.schema} cannot play tournaments")
    rng = random.Random(f"{seed}:lineup")
    learner = seed % config.players
    lineup: list[str] = []
    agents: list[TableAgent] = []
    seats: list[LearningSeat] = []
    drawn: dict[int, PoolMember] = {}
    for player in range(config.players):
        if player == learner or rng.random() < self_play_share:
            seat = LearningSeat(agent, player, sink, hand_horizon=hand_horizon)
            seats.append(seat)
            agents.append(seat)
            lineup.append("self")
        elif opponents is not None:
            member = opponents.draw(rng)
            drawn[player] = member
            agents.append(member.build(seed * 16 + player))
            lineup.append(member.name)
        else:
            kind = rng.choice(list(pool))
            agents.append(rule_bot(kind, seed * 16 + player))
            lineup.append(kind)
    tournament = Tournament(config, seed)
    for seat in seats:
        seat.equity = tournament.equity(seat.player)
    starting_equity = seats[0].equity
    runner = TableRunner()
    agent.training = True
    while not tournament.complete:
        agent.start_hand()
        tournament.play_hand(agents, runner)
        if not tournament.complete:
            for seat in seats:
                if not seat.done:
                    seat.after_hand(
                        tournament.equity(seat.player), tournament.stacks[seat.player] == 0
                    )
        if after_hand is not None:
            after_hand()
    # The final hand: place the winner (paying their own bounty) before the last rewards.
    result = tournament.finish()
    for seat in seats:
        if not seat.done:
            seat.after_hand(tournament.equity(seat.player), True)
    agent.hand_info = None
    if opponents is not None:
        for player, member in drawn.items():
            opponents.record(member, result.results[learner], result.results[player])
    return TournamentEpisode(
        learner_players=tuple(seat.player for seat in seats),
        lineup=tuple(lineup),
        hands=result.hands,
        results=result.results,
        rewards={seat.player: tuple(seat.rewards) for seat in seats},
        starting_equity=starting_equity,
    )


@dataclass(frozen=True, slots=True)
class TournamentTrainingReport:
    tournaments: int
    hands: int
    train_steps: int
    transitions: int
    policy_samples: int
    last_metrics: dict[str, float]


def tournament_self_play_train(
    agent: NfspAgent,
    config: TournamentConfig,
    tournaments: int,
    seed_start: int,
    train_every_hands: int = 1,
    self_play_share: float = 0.5,
    on_progress: Callable[[int], None] | None = None,
    hand_horizon: bool = False,
    opponents: OpponentPool | None = None,
    history_every: int = 0,
) -> TournamentTrainingReport:
    """Play `tournaments` training tournaments, training after every few hands.

    With `opponents` and `history_every`, a frozen copy of the learner's average policy
    joins the pool every `history_every` tournaments (the pool keeps the newest few).
    """
    if train_every_hands < 1:
        raise ValueError("train_every_hands must be positive")
    metrics: dict[str, float] = {}
    hands = 0

    def after_hand() -> None:
        nonlocal hands, metrics
        hands += 1
        if hands % train_every_hands == 0:
            metrics = agent.train_step() or metrics

    agent.policy_sink = None
    for index in range(tournaments):
        play_training_tournament(
            agent,
            config,
            seed_start + index,
            agent.rl_buffer.add,
            self_play_share,
            after_hand,
            hand_horizon=hand_horizon,
            opponents=opponents,
        )
        if (
            opponents is not None
            and history_every
            and (seed_start + index + 1) % history_every == 0
        ):
            opponents.add_history(snapshot_member(agent, f"history:{seed_start + index + 1}"))
        if on_progress is not None:
            on_progress(index + 1)
    return TournamentTrainingReport(
        tournaments=tournaments,
        hands=hands,
        train_steps=agent.train_steps,
        transitions=len(agent.rl_buffer),
        policy_samples=len(agent.sl_buffer),
        last_metrics=metrics,
    )


@dataclass
class FrozenTournamentPlayer:
    """A trained agent playing, not learning, in a tournament benchmark."""

    agent: NfspAgent

    def begin_hand(self, info: TournamentHandInfo) -> None:
        self.agent.begin_hand(info)

    def act(self, observation: TableObservation, legal: LegalActions) -> Action:
        return self.agent.act(observation, legal)


@dataclass
class GreedyTournamentPlayer:
    """A trained agent's best response: acts greedily with its Q network, no exploration.

    NFSP plays the average policy by default; in a fixed field of opponents the best
    response it has learned can be stronger, so benchmarks may report both.
    """

    agent: NfspAgent

    def __post_init__(self) -> None:
        self.agent.training = False

    def begin_hand(self, info: TournamentHandInfo) -> None:
        self.agent.begin_hand(info)

    def act(self, observation: TableObservation, legal: LegalActions) -> Action:
        self.agent.best_response_mode = True
        return self.agent.act(observation, legal)


def snapshot_member(agent: NfspAgent, name: str) -> PoolMember:
    """A frozen copy of the learner's current average policy (no buffers, no optimizer)."""
    copy = NfspAgent(agent.config, device=str(agent.device))
    copy.policy_network.load_state_dict(agent.policy_network.state_dict())
    copy.training = False
    return PoolMember(name, lambda seed: FrozenTournamentPlayer(copy), "history")


def frozen_player(bundle: bytes) -> FrozenTournamentPlayer:
    """Load the average policy of a checkpoint for tournament benchmarks."""
    return FrozenTournamentPlayer(NfspAgent.from_bundle(bundle, inference_only=True))
