"""MTT stages on one table (T5): our table plays, the rest of the field is simulated.

A large multi-table tournament is modelled from one table's point of view at a given stage
(the bubble, just in the money, the last tables). `StageSpec` describes the field at that
moment; `Tournament` plays our table as usual and `FieldState` moves the other tables
along: every hand each other table busts a player with a small probability (short stacks
more often, their chips going to another player of the field), so the bubble comes closer
while we play. Nobody moves to our table (no table balancing in this version), so our
table only gets shorter-handed.

Equity is ICM (Malmuth-Harville) over the whole field (`field_icm`, NumPy, imported only
when needed). A stage ends when the field is down to `end_players` or our table has one
player left; players still in then get their field ICM equity as their prize (the same
quantity the training reward follows, so nothing is biased).
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tableforge.poker.field_icm import FieldIcm

MAX_FIELD = 1_000


@dataclass(frozen=True, slots=True)
class StageSpec:
    """The field at the start of a stage. Prizes are in buy-ins, per field place."""

    field_size: int
    """Players who started the tournament."""
    players_left: int
    """Players left in the whole field when the stage starts (our table included)."""
    prizes: tuple[float, ...]
    """Prize of field places 1, 2, ... (the paid places), in buy-ins."""
    end_players: int
    """The stage ends when the field is down to this many players."""
    stack_spread: float = 0.7
    """Spread of stacks (sigma of a log-normal around the average stack)."""
    table_size: int = 9
    """Players per table in the rest of the field (for the bust rate)."""
    bust_rate: float = 0.1
    """Chance per hand that a given other table loses a player (the field's pace)."""
    bounty_spread: float = 0.6
    """PKO: spread of the bounties on heads (log-normal around the average bounty)."""
    bounty_growth: float = 2.0
    """PKO: average bounty on a head, relative to the starting bounty."""
    name: str = ""

    def __post_init__(self) -> None:
        if not 2 <= self.players_left <= self.field_size <= MAX_FIELD:
            raise ValueError(f"a stage needs 2 <= players left <= field size <= {MAX_FIELD}")
        if not self.prizes or len(self.prizes) > self.field_size:
            raise ValueError("prizes must have between 1 and field_size places")
        if any(prize < 0 for prize in self.prizes) or any(
            later > earlier + 1e-12
            for earlier, later in zip(self.prizes, self.prizes[1:], strict=False)
        ):
            raise ValueError("prizes must be non-negative and not increase with the place")
        if not 1 <= self.end_players < self.players_left:
            raise ValueError("end_players must be below players_left")
        if not 0 <= self.stack_spread <= 3 or not 0 <= self.bounty_spread <= 3:
            raise ValueError("spreads must be between 0 and 3")
        if not 2 <= self.table_size <= 10 or not 0 <= self.bust_rate <= 1:
            raise ValueError("invalid table size or bust rate")
        if self.bounty_growth <= 0:
            raise ValueError("bounty_growth must be positive")

    @property
    def paid(self) -> int:
        return len(self.prizes)

    def prize(self, place: int) -> float:
        return self.prizes[place - 1] if 1 <= place <= len(self.prizes) else 0.0

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["prizes"] = list(self.prizes)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> StageSpec:
        values = dict(data)
        unknown = set(values) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown stage settings: {', '.join(sorted(unknown))}")
        try:
            prizes = values["prizes"]
            if not isinstance(prizes, list | tuple):
                raise ValueError("prizes must be a list")
            values["prizes"] = tuple(float(item) for item in prizes)
            return cls(**values)  # type: ignore[arg-type]
        except (KeyError, TypeError) as error:
            raise ValueError(f"invalid stage: {error}") from error

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def mtt_prizes(
    field_size: int, paid_share: float = 0.15, min_cash: float = 1.5, steepness: float = 1.05
) -> tuple[float, ...]:
    """A typical MTT payout in buy-ins (prize pool = field size; no bounty part).

    About `paid_share` of the field is paid, the last paid place gets `min_cash` buy-ins and
    the rest of the pool is spread with prizes falling like 1 / place^steepness.
    """
    paid = max(1, round(field_size * paid_share))
    if min_cash * paid >= field_size:
        raise ValueError("the min-cashes alone exceed the prize pool")
    raw = [1.0 / place**steepness for place in range(1, paid + 1)]
    total = sum(raw)
    return tuple(
        round(min_cash + (field_size - min_cash * paid) * value / total, 6) for value in raw
    )


STAGES: dict[str, StageSpec] = {
    # 1,000 players, 150 paid: ten players off the money; the stage ends 20 places into it.
    "bubble": StageSpec(1_000, 160, mtt_prizes(1_000), end_players=130, name="bubble"),
    "itm": StageSpec(1_000, 140, mtt_prizes(1_000), end_players=100, name="itm"),
    # Three tables left; ends at the final table.
    "final_tables": StageSpec(1_000, 27, mtt_prizes(1_000), end_players=9, name="final_tables"),
}
"""Named stages for presets and the training sampler."""


@dataclass(frozen=True, slots=True)
class FieldBust:
    stack: float
    place: int
    prize: float


class FieldState:
    """The other tables: their stacks, and the players they lose while we play."""

    def __init__(
        self, spec: StageSpec, table_players: int, average_stack: float, seed: str
    ) -> None:
        if spec.players_left <= table_players:
            raise ValueError("the stage must have more players left than our table")
        self.spec = spec
        self.rng = random.Random(f"{seed}:field")
        others = spec.players_left - table_players
        raw = [self.rng.lognormvariate(0.0, spec.stack_spread) for _ in range(others)]
        scale = average_stack * others / sum(raw)
        self.stacks = [max(1.0, value * scale) for value in raw]
        self.busts: list[FieldBust] = []
        self._icm: FieldIcm | None = None

    @property
    def count(self) -> int:
        return len(self.stacks)

    def icm(self) -> FieldIcm:
        if self._icm is None:
            from tableforge.poker.field_icm import FieldIcm

            self._icm = FieldIcm(self.stacks, self.spec.prizes)
        return self._icm

    def step(self, players_left: int, limit: int) -> int:
        """One hand at every other table; returns how many players they busted.

        `players_left` is the whole field before these busts (for their places); at most
        `limit` players bust (the stage ends at `end_players`). The rate is fixed by the
        stage (`bust_rate`), not by our table: the field's pace is part of the scenario,
        the same for every player tested at our table.
        """
        tables = math.ceil(self.count / self.spec.table_size) if self.count else 0
        busted = sum(1 for _ in range(tables) if self.rng.random() < self.spec.bust_rate)
        busted = min(busted, self.count - 1 if self.count > 1 else 0, max(limit, 0))
        for _ in range(busted):
            weights = [1.0 / stack for stack in self.stacks]
            victim = self.rng.choices(range(self.count), weights)[0]
            chips = self.stacks.pop(victim)
            winner = self.rng.randrange(self.count)
            self.stacks[winner] += chips
            self.busts.append(FieldBust(chips, players_left, self.spec.prize(players_left)))
            players_left -= 1
        if busted:
            self._icm = None
        return busted


def stage_table_stacks(spec: StageSpec, players: int, total_chips: int, seed: str) -> list[int]:
    """Our table's stacks: log-normal like the field, adding up to exactly `total_chips`."""
    rng = random.Random(f"{seed}:table")
    raw = [rng.lognormvariate(0.0, spec.stack_spread) for _ in range(players)]
    scale = total_chips / sum(raw)
    stacks = [max(1, int(value * scale)) for value in raw]
    stacks[stacks.index(max(stacks))] += total_chips - sum(stacks)
    return stacks


def stage_bounties(spec: StageSpec, players: int, starting_bounty: float, seed: str) -> list[float]:
    """PKO: bounties already on our table's heads (log-normal, mean `bounty_growth`)."""
    rng = random.Random(f"{seed}:bounties")
    average = starting_bounty * spec.bounty_growth
    return [
        round(average * rng.lognormvariate(-(spec.bounty_spread**2) / 2, spec.bounty_spread), 6)
        for _ in range(players)
    ]
