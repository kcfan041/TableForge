from __future__ import annotations

import json
import random
from collections.abc import Callable
from dataclasses import dataclass, field

from tableforge.agents.protocol import PokerAgent
from tableforge.domain.artifacts import ArtifactKind
from tableforge.domain.league import (
    MATCHUP_SCHEMA,
    LeagueMatchupPayload,
    LeagueMember,
    LeaguePopulation,
    MemberKind,
)
from tableforge.learning.executors import opponent_factory
from tableforge.learning.experience import verified
from tableforge.learning.nfsp import NfspAgent
from tableforge.learning.training import OpponentPicker, evaluate

Fetch = Callable[[str], bytes]


@dataclass
class MemberLoader:
    fetch_artifact: Fetch
    _cache: dict[str, NfspAgent] = field(default_factory=dict)

    def factory(self, member: LeagueMember, seed: int) -> Callable[[], PokerAgent]:
        if member.kind is MemberKind.RULE:
            return opponent_factory(member.rule, seed)
        if member.kind is MemberKind.CHECKPOINT:
            agent = self._cache.get(member.sha256)
            if agent is None:
                agent = NfspAgent.from_bundle(
                    verified(self.fetch_artifact(member.artifact_id), member.sha256),
                    inference_only=True,
                )
                self._cache[member.sha256] = agent
            loaded = agent
            return lambda: loaded
        raise ValueError("self member has no standalone agent")


def population_picker(
    population: LeaguePopulation, loader: MemberLoader, seed: int
) -> OpponentPicker:
    rng = random.Random(seed)
    weights = [member.weight for member in population.members]
    factories: dict[str, Callable[[], PokerAgent]] = {}

    def pick(index: int) -> PokerAgent | None:
        del index
        member = rng.choices(population.members, weights)[0]
        if member.kind is MemberKind.SELF:
            return None
        factory = factories.get(member.name)
        if factory is None:
            factory = loader.factory(member, seed + len(factories) * 1_000_003)
            factories[member.name] = factory
        return factory()

    return pick


@dataclass(frozen=True, slots=True)
class LeagueMatchupExecutor:
    fetch_artifact: Fetch
    output_kind: ArtifactKind = ArtifactKind.EVALUATION_RESULT
    output_media_type: str = "application/json"
    output_schema: str = MATCHUP_SCHEMA

    def execute(
        self, payload_bytes: bytes, on_progress: Callable[[int], None] | None = None
    ) -> bytes:
        payload = LeagueMatchupPayload.from_bytes(payload_bytes)
        loader = MemberLoader(self.fetch_artifact)
        first = loader.factory(payload.first, payload.seed_start + 1)()
        report = evaluate(
            first,
            loader.factory(payload.second, payload.seed_start + 2),
            payload.deals,
            payload.seed_start,
            on_progress,
        )
        return json.dumps(
            {
                "bb_per_100": round(report.bb_per_100, 6),
                "confidence_high": round(report.confidence_high, 6),
                "confidence_low": round(report.confidence_low, 6),
                "first": payload.first.name,
                "hands": report.hands,
                "schema_version": MATCHUP_SCHEMA,
                "second": payload.second.name,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
