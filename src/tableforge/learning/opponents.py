"""Opponent pools for tournament training and benchmarks.

A pool is a list of members, each a rule bot or a frozen model (a checkpoint from the model
library, or an earlier version of the learner). Seats that are not played by the learner
draw a member by weight.

PFSP (prioritised fictitious self-play) weights members by how hard they are: after each
tournament the learner's result against the members at the table is recorded, and a
member's weight is ``f(win rate)``. With ``hard`` weighting, members the learner beats
rarely are drawn more often; ``variance`` weighting prefers members that are close to even.
Weights never drop below a floor, so no member disappears from training.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from tableforge.poker.table import TableAgent

WEIGHTINGS = ("uniform", "hard", "variance")


@dataclass
class PoolMember:
    """One kind of opponent. `build(seed)` returns a fresh player for one seat."""

    name: str
    build: Callable[[int], TableAgent]
    kind: str = "rule"
    """``rule``, ``model`` or ``history``."""
    base_weight: float = 1.0
    games: float = 0.0
    wins: float = 0.0
    """Tournaments where the learner beat this member, counted fractionally per seat."""

    @property
    def win_rate(self) -> float:
        """The learner's win rate against this member (0.5 before any games)."""
        return (self.wins + 1) / (self.games + 2)


@dataclass
class OpponentPool:
    members: list[PoolMember]
    weighting: str = "uniform"
    exponent: float = 2.0
    floor: float = 0.1
    history_limit: int = 5
    _history: list[PoolMember] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.weighting not in WEIGHTINGS:
            raise ValueError(f"weighting must be one of {', '.join(WEIGHTINGS)}")
        if not self.members:
            raise ValueError("an opponent pool needs at least one member")

    def all_members(self) -> list[PoolMember]:
        return [*self.members, *self._history]

    def weight(self, member: PoolMember) -> float:
        if self.weighting == "uniform":
            factor = 1.0
        elif self.weighting == "hard":
            factor = (1 - member.win_rate) ** self.exponent
        else:
            factor = member.win_rate * (1 - member.win_rate) * 4
        return member.base_weight * max(self.floor, factor)

    def draw(self, rng: random.Random) -> PoolMember:
        members = self.all_members()
        return rng.choices(members, weights=[self.weight(member) for member in members])[0]

    def record(self, member: PoolMember, learner_result: float, member_result: float) -> None:
        """One tournament where this member sat at the learner's table."""
        member.games += 1
        if learner_result > member_result:
            member.wins += 1
        elif math.isclose(learner_result, member_result):
            member.wins += 0.5

    def add_history(self, member: PoolMember) -> None:
        """Add an earlier version of the learner; only the newest `history_limit` are kept."""
        if self.history_limit < 1:
            return
        self._history.append(member)
        del self._history[: -self.history_limit]

    def summary(self) -> list[dict[str, object]]:
        return [
            {
                "name": member.name,
                "kind": member.kind,
                "games": round(member.games, 1),
                "learner_win_rate": round(member.win_rate, 4),
                "weight": round(self.weight(member), 4),
            }
            for member in self.all_members()
        ]


def rule_members(names: Sequence[str]) -> list[PoolMember]:
    from tableforge.worker.tournament import rule_bot

    def builder(name: str) -> Callable[[int], TableAgent]:
        return lambda seed: rule_bot(name, seed)

    return [PoolMember(f"rule:{name}", builder(name), "rule") for name in names]


def model_member(
    name: str, bundle: bytes, kind: str = "model", mode: str = "average"
) -> PoolMember:
    """A frozen model; every seat shares one set of weights (acting is stateless per seat)."""
    from tableforge.learning.nfsp import NfspAgent
    from tableforge.learning.tournament_training import (
        FrozenTournamentPlayer,
        GreedyTournamentPlayer,
    )

    if mode == "best_response":
        greedy = NfspAgent.from_actor_bundle(bundle)

        def build(seed: int) -> TableAgent:
            del seed
            return GreedyTournamentPlayer(greedy)
    else:
        frozen = NfspAgent.from_bundle(bundle, inference_only=True)

        def build(seed: int) -> TableAgent:
            del seed
            return FrozenTournamentPlayer(frozen)

    return PoolMember(name, build, kind)
