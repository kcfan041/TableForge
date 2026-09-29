"""Tournament specifications: the user-facing way to describe a tournament.

A `TournamentSpec` is what a person chooses (table size, starting stack in big blinds,
blind structure, payout, bounty rules, buy-in); `to_config()` turns it into the engine's
`TournamentConfig`. Nothing about a specific site is hard-coded: Natural8 and other
sites run PKOs with many different structures, so presets only pick common values and a
real event is modelled by entering its lobby structure (custom blind levels and payout).

Defaults: 6 players, 10 BB, standard blinds (10/20 rising about 25% per level, big blind
ante of one BB, 10 hands per level), 65/35 payout, PKO with the common 50/50 progressive
bounty (half the buy-in is the starting bounty; half of each knockout is paid in cash).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any

from tableforge.poker.tournament import (
    PAYOUTS,
    BlindLevel,
    BountyConfig,
    TournamentConfig,
    standard_levels,
)

SPEC_SCHEMA = "tableforge.tournament-spec.v1"
MAX_LEVELS = 60


@dataclass(frozen=True, slots=True)
class TournamentSpec:
    players: int = 6
    starting_stack_bb: float = 10.0
    hands_per_level: int = 10
    ante: bool = True
    payout: tuple[float, ...] = PAYOUTS["65-35"]
    pko: bool = True
    bounty_share: float = 0.5
    cash_share: float = 0.5
    winner_keeps_own: bool = True
    buy_in: float = 1.0
    blind_levels: tuple[BlindLevel, ...] = field(default=())
    """Custom blind structure (e.g. copied from a tournament lobby); empty = standard."""
    name: str = ""

    def __post_init__(self) -> None:
        if len(self.blind_levels) > MAX_LEVELS:
            raise ValueError(f"at most {MAX_LEVELS} blind levels")
        if not 1 <= self.hands_per_level <= 1_000:
            raise ValueError("hands_per_level must be between 1 and 1,000")
        if not 1 <= self.starting_stack_bb <= 1_000:
            raise ValueError("starting_stack_bb must be between 1 and 1,000")
        self.to_config()

    def levels(self) -> tuple[BlindLevel, ...]:
        if self.blind_levels:
            return self.blind_levels
        return standard_levels(self.hands_per_level, big_blind_ante=self.ante)

    def label(self) -> str:
        if self.name:
            return self.name
        kind = "pko" if self.pko else "sng"
        payout = "-".join(f"{share * 100:g}" for share in self.payout)
        return f"{self.players}max-{kind}-{self.starting_stack_bb:g}bb-{payout}"

    def to_config(self) -> TournamentConfig:
        bounty = (
            BountyConfig(self.bounty_share, self.cash_share, self.winner_keeps_own)
            if self.pko
            else None
        )
        return TournamentConfig(
            players=self.players,
            starting_stack_bb=self.starting_stack_bb,
            blind_levels=self.levels(),
            payout=self.payout,
            buy_in=self.buy_in,
            name=self.label(),
            bounty=bounty,
        )

    def to_json(self) -> str:
        data = asdict(self)
        data["schema_version"] = SPEC_SCHEMA
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TournamentSpec:
        """Build from user input (JSON object); unknown keys and bad values are errors."""
        data = dict(data)
        if data.pop("schema_version", SPEC_SCHEMA) != SPEC_SCHEMA:
            raise ValueError("unsupported tournament spec schema")
        allowed = set(cls.__dataclass_fields__)
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown tournament settings: {', '.join(sorted(unknown))}")
        values: dict[str, Any] = {}
        try:
            for key in ("players", "hands_per_level"):
                if key in data:
                    values[key] = _integer(data[key], key)
            for key in ("starting_stack_bb", "bounty_share", "cash_share", "buy_in"):
                if key in data:
                    values[key] = float(data[key])
                    if not math.isfinite(values[key]):
                        raise ValueError(f"{key} must be a finite number")
            for key in ("ante", "pko", "winner_keeps_own"):
                if key in data:
                    if not isinstance(data[key], bool):
                        raise ValueError(f"{key} must be true or false")
                    values[key] = data[key]
            if "payout" in data:
                values["payout"] = parse_payout(data["payout"])
            if "blind_levels" in data and data["blind_levels"]:
                values["blind_levels"] = tuple(
                    BlindLevel(
                        _integer(level["small_blind"], "small_blind"),
                        _integer(level["big_blind"], "big_blind"),
                        _integer(level.get("ante", 0), "ante"),
                        _integer(level.get("hands", 10), "hands"),
                    )
                    for level in data["blind_levels"]
                )
            if "name" in data:
                values["name"] = str(data["name"])[:80]
        except (KeyError, TypeError) as error:
            raise ValueError(f"invalid tournament settings: {error}") from error
        return cls(**values)

    @classmethod
    def from_json(cls, text: str) -> TournamentSpec:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("tournament spec must be an object")
        return cls.from_dict(data)


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{name} must be a whole number")
    number = float(value)
    if not number.is_integer():
        raise ValueError(f"{name} must be a whole number")
    return int(number)


def parse_payout(value: object) -> tuple[float, ...]:
    """A payout: a preset name (``65-35``), percentages (``50/30/20``) or a list of shares.

    Shares are normalised when given as percentages; they must add up to 1 (or 100).
    """
    if isinstance(value, str):
        if value in PAYOUTS:
            return PAYOUTS[value]
        parts = [part for part in value.replace(",", "/").replace("-", "/").split("/") if part]
        shares = [float(part) for part in parts]
    elif isinstance(value, (list, tuple)):
        shares = [float(part) for part in value]
    else:
        raise ValueError("payout must be a name, a list of shares, or like 50/30/20")
    if not shares or any(share < 0 or not math.isfinite(share) for share in shares):
        raise ValueError("payout shares must be non-negative numbers")
    total = sum(shares)
    if math.isclose(total, 100, abs_tol=1e-6):
        shares = [share / 100 for share in shares]
    elif not math.isclose(total, 1, abs_tol=1e-6):
        raise ValueError("payout shares must add up to 1 (or 100%)")
    return tuple(round(share, 10) for share in shares)


def spec_from_config(config: TournamentConfig) -> dict[str, object]:
    """A readable summary of an engine config (for UIs and model descriptions)."""
    return {
        "players": config.players,
        "starting_stack_bb": config.starting_stack_bb,
        "levels": len(config.blind_levels),
        "first_level": asdict(config.blind_levels[0]),
        "hands_per_level": config.blind_levels[0].hands,
        "payout": list(config.payout),
        "pko": config.bounty is not None,
        "bounty_share": config.bounty.bounty_share if config.bounty else 0.0,
        "cash_share": config.bounty.cash_share if config.bounty else 0.0,
        "buy_in": config.buy_in,
        "name": config.name,
    }
