from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from tableforge.agents.protocol import PokerAgent
from tableforge.learning.actions import ActionTranslator
from tableforge.learning.buffers import PolicySample, Transition
from tableforge.learning.nfsp import NfspAgent
from tableforge.poker.betting import Action, LegalActions, Seat
from tableforge.poker.environment import HeadsUpHand
from tableforge.poker.observation import Observation
from tableforge.poker.runner import HandRunner

STACK = 200
SMALL_BLIND = 1
BIG_BLIND = 2


@dataclass
class _Pending:
    state: tuple[float, ...]
    mask: tuple[bool, ...]
    action: int


class _RecordingSeat:
    def __init__(self, agent: NfspAgent, sink: Callable[[Transition], None]) -> None:
        self.agent = agent
        self.sink = sink
        self.translator = ActionTranslator()
        self.pending: _Pending | None = None

    def act(self, observation: Observation, legal: LegalActions) -> Action:
        state = tuple(self.agent.encoder.encode(observation, self.agent.config.big_blind))
        mask = self.translator.legal_mask(legal)
        if self.pending is not None:
            self.sink(
                Transition(
                    self.pending.state,
                    self.pending.mask,
                    self.pending.action,
                    0.0,
                    state,
                    mask,
                )
            )
        action = self.agent.act(observation, legal)
        if self.agent.last_choice is None:
            raise AssertionError("agent must record its abstract choice")
        self.pending = _Pending(state, mask, int(self.agent.last_choice))
        return action

    def finish(self, reward_bb: float) -> None:
        if self.pending is not None:
            self.sink(
                Transition(
                    self.pending.state,
                    self.pending.mask,
                    self.pending.action,
                    reward_bb,
                    None,
                    None,
                )
            )
        self.pending = None


OpponentPicker = Callable[[int], PokerAgent | None]


def _play(
    agent: NfspAgent,
    hands: int,
    seed_start: int,
    sink: Callable[[Transition], None],
    after_hand: Callable[[int], None],
    pick_opponent: OpponentPicker | None = None,
) -> None:
    agent.training = True
    runner = HandRunner()
    for index in range(hands):
        opponent = pick_opponent(index) if pick_opponent is not None else None
        agent.start_hand()
        hand = HeadsUpHand(
            (STACK, STACK), Seat(index % 2), SMALL_BLIND, BIG_BLIND, seed=seed_start + index
        )
        if opponent is None:
            seats: tuple[_RecordingSeat, ...] = (
                _RecordingSeat(agent, sink),
                _RecordingSeat(agent, sink),
            )
            result = runner.run(hand, (seats[0], seats[1]))
            for seat in Seat:
                seats[int(seat)].finish((result.final_stacks[int(seat)] - STACK) / BIG_BLIND)
        else:
            learner_seat = Seat((index // 2) % 2)
            learner = _RecordingSeat(agent, sink)
            agents: list[PokerAgent] = [opponent, opponent]
            agents[int(learner_seat)] = learner
            result = runner.run(hand, (agents[0], agents[1]))
            learner.finish((result.final_stacks[int(learner_seat)] - STACK) / BIG_BLIND)
        after_hand(index + 1)


@dataclass(frozen=True, slots=True)
class TrainingReport:
    hands: int
    train_steps: int
    transitions: int
    policy_samples: int
    last_metrics: dict[str, float]


def self_play_train(
    agent: NfspAgent,
    hands: int,
    seed_start: int,
    train_every: int = 4,
    on_progress: Callable[[int], None] | None = None,
) -> TrainingReport:
    metrics: dict[str, float] = {}

    def after_hand(completed: int) -> None:
        nonlocal metrics
        if completed % train_every == 0:
            metrics = agent.train_step() or metrics
        if on_progress is not None and completed % 100 == 0:
            on_progress(completed)

    agent.policy_sink = None
    _play(agent, hands, seed_start, agent.rl_buffer.add, after_hand)
    return TrainingReport(
        hands=hands,
        train_steps=agent.train_steps,
        transitions=len(agent.rl_buffer),
        policy_samples=len(agent.sl_buffer),
        last_metrics=metrics,
    )


def collect_experience(
    agent: NfspAgent,
    hands: int,
    seed_start: int,
    on_progress: Callable[[int], None] | None = None,
    pick_opponent: OpponentPicker | None = None,
) -> tuple[list[Transition], list[PolicySample]]:
    transitions: list[Transition] = []
    samples: list[PolicySample] = []

    def after_hand(completed: int) -> None:
        if on_progress is not None and completed % 100 == 0:
            on_progress(completed)

    agent.policy_sink = samples.append
    try:
        _play(agent, hands, seed_start, transitions.append, after_hand, pick_opponent)
    finally:
        agent.policy_sink = None
    return transitions, samples


def learn_from_experience(
    agent: NfspAgent,
    transitions: list[Transition],
    samples: list[PolicySample],
    steps: int,
    on_progress: Callable[[int], None] | None = None,
    batch_size_limit: int | None = None,
) -> TrainingReport:
    agent.training = True
    if batch_size_limit is not None:
        if batch_size_limit < 1:
            raise ValueError("batch_size_limit must be positive")
        agent.batch_size = min(agent.config.batch_size, batch_size_limit)
    agent.rl_buffer.items.clear()
    agent.rl_buffer.position = 0
    for transition in transitions:
        agent.rl_buffer.add(transition)
    for sample in samples:
        agent.sl_buffer.add(sample, agent.rng)
    metrics: dict[str, float] = {}
    for step in range(steps):
        metrics = agent.train_step() or metrics
        if on_progress is not None and (step + 1) % 100 == 0:
            on_progress(step + 1)
    agent.rl_buffer.items.clear()
    agent.rl_buffer.position = 0
    return TrainingReport(
        hands=0,
        train_steps=agent.train_steps,
        transitions=len(transitions),
        policy_samples=len(agent.sl_buffer),
        last_metrics=metrics,
    )


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    hands: int
    bb_per_100: float
    standard_error: float
    confidence_low: float
    confidence_high: float


def _deal_result(
    candidate: PokerAgent,
    opponent_factory: Callable[[], PokerAgent],
    seed: int,
    runner: HandRunner,
) -> float:
    total = 0.0
    for candidate_seat in Seat:
        agents: list[PokerAgent] = [opponent_factory(), opponent_factory()]
        agents[int(candidate_seat)] = candidate
        hand = HeadsUpHand((STACK, STACK), Seat.PLAYER_0, SMALL_BLIND, BIG_BLIND, seed=seed)
        result = runner.run(hand, (agents[0], agents[1]))
        total += (result.final_stacks[int(candidate_seat)] - STACK) / BIG_BLIND
    return total / 2


def _report(results: list[float]) -> EvaluationReport:
    mean = sum(results) / len(results)
    variance = sum((value - mean) ** 2 for value in results) / (len(results) - 1)
    error = math.sqrt(variance / len(results))
    return EvaluationReport(
        hands=len(results) * 2,
        bb_per_100=mean * 100,
        standard_error=error * 100,
        confidence_low=(mean - 1.96 * error) * 100,
        confidence_high=(mean + 1.96 * error) * 100,
    )


def evaluate(
    candidate: PokerAgent,
    opponent_factory: Callable[[], PokerAgent],
    deals: int,
    seed_start: int,
    on_progress: Callable[[int], None] | None = None,
) -> EvaluationReport:
    return evaluate_until(
        candidate,
        opponent_factory,
        seed_start,
        min_deals=deals,
        max_deals=deals,
        batch_deals=deals,
        target_width=0.0,
        on_progress=on_progress,
    )


def evaluate_until(
    candidate: PokerAgent,
    opponent_factory: Callable[[], PokerAgent],
    seed_start: int,
    min_deals: int,
    max_deals: int,
    batch_deals: int,
    target_width: float,
    on_progress: Callable[[int], None] | None = None,
) -> EvaluationReport:
    if min_deals < 2 or max_deals < min_deals or batch_deals < 1:
        raise ValueError("invalid evaluation deal limits")
    runner = HandRunner()
    results: list[float] = []
    while len(results) < max_deals:
        target = len(results) + (min_deals if not results else batch_deals)
        target = min(target, max_deals)
        while len(results) < target:
            results.append(
                _deal_result(candidate, opponent_factory, seed_start + len(results), runner)
            )
            if on_progress is not None and len(results) % 50 == 0:
                on_progress(len(results))
        report = _report(results)
        if report.confidence_high - report.confidence_low <= target_width:
            return report
    return _report(results)
