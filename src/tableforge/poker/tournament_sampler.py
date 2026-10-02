"""Many tournament structures for one model (S3).

A `TournamentSampler` describes ranges instead of one tournament: table sizes, starting
stacks, blind speeds (hands per level and growth per level), antes, payouts and bounty
rules. `config(seed)` draws one concrete `TournamentConfig` from those ranges, always the
same one for the same seed, so training with a sampler stays reproducible and every actor
job sees a different mix of structures.

With `stages` some draws are MTT stages (T5: our table inside a large field, see
`mtt_stage`); those need the v4 encoder, which sees the field.

Training with a sampler needs an encoder that sees the structure (`poker-nlhe-vector-v3`);
benchmarks still use one fixed tournament each, so a multi-structure model is compared
with a single-structure model on that model's own tournament.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from typing import Any

from tableforge.poker.table import MAX_PLAYERS
from tableforge.poker.tournament import BlindLevel, BountyConfig, TournamentConfig

SAMPLER_SCHEMA = "tableforge.tournament-sampler.v1"
_FIRST_BIG_BLIND = 20
_LEVELS = 30


@dataclass(frozen=True, slots=True)
class TournamentSampler:
    players: tuple[int, ...] = (6,)
    """Table sizes to choose from (uniformly)."""
    starting_stack_bb: tuple[float, float] = (8.0, 60.0)
    """Range of the starting stack in big blinds (drawn log-uniformly)."""
    hands_per_level: tuple[int, ...] = (6, 8, 10, 15)
    blind_growth: tuple[float, float] = (1.2, 1.5)
    """Big blind of a level over the previous one (uniform in this range)."""
    ante_share: tuple[float, ...] = (0.0, 0.125, 1.0)
    """Big blind ante as a share of the big blind (0 = no ante)."""
    payouts: tuple[tuple[float, ...], ...] = ((0.65, 0.35), (0.5, 0.3, 0.2), (1.0,))
    pko_share: float = 0.75
    """Chance that a tournament is a progressive knockout."""
    bounty_share: tuple[float, ...] = (0.5,)
    cash_share: tuple[float, ...] = (0.5,)
    name: str = "mixed"
    stages: tuple[str, ...] = ()
    """T5: named MTT stages (`mtt_stage.STAGES`) to draw from; needs the v4 encoder."""
    stage_share: float = 0.0
    """Chance that a drawn tournament is one of `stages` (our table in a large MTT)."""
    extra: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        if not self.players or any(not 2 <= value <= MAX_PLAYERS for value in self.players):
            raise ValueError(f"table sizes must be between 2 and {MAX_PLAYERS}")
        low, high = self.starting_stack_bb
        if not 2 <= low <= high <= 500:
            raise ValueError("starting_stack_bb must be a range within 2 to 500")
        if not self.hands_per_level or any(not 1 <= value <= 200 for value in self.hands_per_level):
            raise ValueError("hands_per_level must be between 1 and 200")
        growth_low, growth_high = self.blind_growth
        if not 1.05 <= growth_low <= growth_high <= 3:
            raise ValueError("blind_growth must be a range within 1.05 to 3")
        if not self.ante_share or any(not 0 <= value <= 2 for value in self.ante_share):
            raise ValueError("ante_share must be between 0 and 2")
        if not self.payouts:
            raise ValueError("give at least one payout")
        for payout in self.payouts:
            if not payout or abs(sum(payout) - 1) > 1e-6 or any(share < 0 for share in payout):
                raise ValueError("every payout must be non-negative shares adding up to 1")
        if not 0 <= self.pko_share <= 1:
            raise ValueError("pko_share must be between 0 and 1")
        if any(not 0 < value < 1 for value in self.bounty_share):
            raise ValueError("bounty_share must be between 0 and 1")
        if any(not 0 <= value <= 1 for value in self.cash_share):
            raise ValueError("cash_share must be between 0 and 1")
        if self.extra:
            raise ValueError(f"unknown sampler settings: {', '.join(sorted(self.extra))}")
        from tableforge.poker.mtt_stage import STAGES

        unknown = set(self.stages) - set(STAGES)
        if unknown:
            raise ValueError(f"unknown stages: {', '.join(sorted(unknown))}")
        if not 0 <= self.stage_share <= 1 or (self.stage_share > 0 and not self.stages):
            raise ValueError("stage_share must be between 0 and 1 and needs stages")

    def config(self, seed: int) -> TournamentConfig:
        """The tournament of training seed `seed` (the same for the same seed)."""
        rng = random.Random(f"{seed}:structure")
        players = rng.choice(self.players)
        low, high = self.starting_stack_bb
        stack_bb = round(low * (high / low) ** rng.random(), 1)
        hands = rng.choice(self.hands_per_level)
        growth = rng.uniform(*self.blind_growth)
        ante_share = rng.choice(self.ante_share)
        payout = rng.choice([item for item in self.payouts if len(item) <= players] or [(1.0,)])
        pko = rng.random() < self.pko_share
        bounty = (
            BountyConfig(rng.choice(self.bounty_share), rng.choice(self.cash_share))
            if pko
            else None
        )
        levels: list[BlindLevel] = []
        big = float(_FIRST_BIG_BLIND)
        for _ in range(_LEVELS):
            big_blind = max(2, int(round(big / 2)) * 2)
            if levels and big_blind <= levels[-1].big_blind:
                big_blind = levels[-1].big_blind + 2
            levels.append(
                BlindLevel(big_blind // 2, big_blind, int(round(big_blind * ante_share)), hands)
            )
            big *= growth
        stage = None
        # Drawn last, with its own generator, so samplers without stages give exactly the
        # tournaments they gave before.
        stage_rng = random.Random(f"{seed}:stage")
        if self.stages and stage_rng.random() < self.stage_share:
            from tableforge.poker.mtt_stage import STAGES

            stage = STAGES[stage_rng.choice(self.stages)]
            # A table in a large field is full; the stage's own payout replaces this one.
            players = min(stage.table_size, MAX_PLAYERS, self.max_players)
            payout = (1.0,)
        return TournamentConfig(
            players=players,
            starting_stack_bb=stack_bb,
            blind_levels=tuple(levels),
            payout=tuple(payout),
            name=f"{self.name}:{seed}" + (f":{stage.name}" if stage is not None else ""),
            bounty=bounty,
            stage=stage,
        )

    def to_json(self) -> str:
        data = asdict(self)
        del data["extra"]
        if not self.stages:
            del data["stages"]
            del data["stage_share"]
        data["schema_version"] = SAMPLER_SCHEMA
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> TournamentSampler:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("a tournament sampler must be an object")
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TournamentSampler:
        data = dict(data)
        if data.pop("schema_version", SAMPLER_SCHEMA) != SAMPLER_SCHEMA:
            raise ValueError("unsupported tournament sampler schema")
        known = set(cls.__dataclass_fields__) - {"extra"}
        extra = {key: data.pop(key) for key in list(data) if key not in known}
        try:
            values: dict[str, Any] = {}
            for key in ("players", "hands_per_level"):
                if key in data:
                    values[key] = tuple(int(item) for item in data[key])
            for key in ("ante_share", "bounty_share", "cash_share"):
                if key in data:
                    values[key] = tuple(float(item) for item in data[key])
            for key in ("starting_stack_bb", "blind_growth"):
                if key in data:
                    first, second = data[key]
                    values[key] = (float(first), float(second))
            if "payouts" in data:
                values["payouts"] = tuple(
                    tuple(float(share) for share in payout) for payout in data["payouts"]
                )
            if "pko_share" in data:
                values["pko_share"] = float(data["pko_share"])
            if "stages" in data:
                values["stages"] = tuple(str(item) for item in data["stages"])
            if "stage_share" in data:
                values["stage_share"] = float(data["stage_share"])
            if "name" in data:
                values["name"] = str(data["name"])[:40]
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid tournament sampler: {error}") from error
        return cls(**values, extra=extra)

    @property
    def max_players(self) -> int:
        return max(self.players)
