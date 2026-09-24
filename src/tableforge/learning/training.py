from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from tableforge.agents.protocol import PokerAgent
from tableforge.learning.actions import ActionTranslator
from tableforge.learning.buffers import Transition
from tableforge.learning.encoding import encode
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
    def __init__(self, agent: NfspAgent) -> None:
        self.agent = agent
        self.translator = ActionTranslator()
        self.pending: _Pending | None = None

    def act(self, observation: Observation, legal: LegalActions) -> Action:
        state = tuple(encode(observation, self.agent.config.big_blind))
        mask = self.translator.legal_mask(legal)
        if self.pending is not None:
            self.agent.rl_buffer.add(
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
            self.agent.rl_buffer.add(
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
    agent.training = True
    runner = HandRunner()
    metrics: dict[str, float] = {}
    for index in range(hands):
        seats = (_RecordingSeat(agent), _RecordingSeat(agent))
        agent.start_hand()
        hand = HeadsUpHand(
            (STACK, STACK), Seat(index % 2), SMALL_BLIND, BIG_BLIND, seed=seed_start + index
        )
        result = runner.run(hand, seats)
        for seat in Seat:
            seats[int(seat)].finish((result.final_stacks[int(seat)] - STACK) / BIG_BLIND)
        if (index + 1) % train_every == 0:
            metrics = agent.train_step() or metrics
        if on_progress is not None and (index + 1) % 100 == 0:
            on_progress(index + 1)
    return TrainingReport(
        hands=hands,
        train_steps=agent.train_steps,
        transitions=len(agent.rl_buffer),
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


def evaluate(
    candidate: PokerAgent,
    opponent_factory: Callable[[], PokerAgent],
    deals: int,
    seed_start: int,
    on_progress: Callable[[int], None] | None = None,
) -> EvaluationReport:
    if deals < 2:
        raise ValueError("at least two deals are required")
    runner = HandRunner()
    results: list[float] = []
    for index in range(deals):
        seed = seed_start + index
        total = 0.0
        for candidate_seat in Seat:
            agents: list[PokerAgent] = [opponent_factory(), opponent_factory()]
            agents[int(candidate_seat)] = candidate
            hand = HeadsUpHand((STACK, STACK), Seat.PLAYER_0, SMALL_BLIND, BIG_BLIND, seed=seed)
            result = runner.run(hand, (agents[0], agents[1]))
            total += (result.final_stacks[int(candidate_seat)] - STACK) / BIG_BLIND
        results.append(total / 2)
        if on_progress is not None and (index + 1) % 50 == 0:
            on_progress(index + 1)
    mean = sum(results) / len(results)
    variance = sum((value - mean) ** 2 for value in results) / (len(results) - 1)
    error = math.sqrt(variance / len(results))
    return EvaluationReport(
        hands=deals * 2,
        bb_per_100=mean * 100,
        standard_error=error * 100,
        confidence_low=(mean - 1.96 * error) * 100,
        confidence_high=(mean + 1.96 * error) * 100,
    )
