"""Single-table poker tournaments (sit & go) on top of `TableHand`.

A tournament is one episode: stacks carry over between hands, busted players are
eliminated, the button moves to the next player still in, blinds rise by hand count,
and players are paid by finishing place. Everything is configurable through
`TournamentConfig`; presets only choose common values.

Amounts:
* chips are integers; the starting stack is `starting_stack_bb` big blinds of the first
  level;
* money (buy-in, prizes, bounties, results) is in buy-in units, so `buy_in` defaults to 1.

Per-hand ICM equity is recorded as telemetry only (see `icm_equities`). It is not a
reward and is not guaranteed to add up to the final result.

Progressive knockout (PKO) is enabled with `TournamentConfig.bounty` (see `BountyConfig`):
part of every buy-in becomes the player's starting bounty instead of going to the prize
pool; eliminating a player pays part of their bounty in cash and adds the rest to the
eliminator's own bounty. Bounties are tracked separately from ICM.
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from functools import cache, lru_cache
from typing import Protocol, runtime_checkable

from tableforge.poker.mtt_stage import (
    FieldState,
    StageSpec,
    stage_bounties,
    stage_table_stacks,
)
from tableforge.poker.table import (
    MAX_PLAYERS,
    PotAward,
    TableAgent,
    TableHand,
    TableResult,
    TableRunner,
)

CONFIG_SCHEMA = "tableforge.tournament-config.v1"
MAX_HANDS = 5_000


@dataclass(frozen=True, slots=True)
class BlindLevel:
    small_blind: int
    big_blind: int
    ante: int = 0
    hands: int = 10

    def __post_init__(self) -> None:
        if not 0 < self.small_blind < self.big_blind:
            raise ValueError("blinds must satisfy 0 < small_blind < big_blind")
        if self.ante < 0 or self.hands < 1:
            raise ValueError("ante cannot be negative and a level lasts at least one hand")


@dataclass(frozen=True, slots=True)
class BountyConfig:
    """Progressive knockout rules; every share is a fraction between 0 and 1.

    * `bounty_share`: part of the buy-in that becomes the starting bounty (the rest goes
      to the prize pool).
    * `cash_share`: part of an eliminated player's bounty paid to the eliminator at once;
      the rest is added to the eliminator's own bounty. 1.0 is a regular knockout.
    * `winner_keeps_own`: the winner also collects the bounty on their own head.
    """

    bounty_share: float = 0.5
    cash_share: float = 0.5
    winner_keeps_own: bool = True

    def __post_init__(self) -> None:
        if not 0 < self.bounty_share < 1:
            raise ValueError("bounty_share must be between 0 and 1 (exclusive)")
        if not 0 <= self.cash_share <= 1:
            raise ValueError("cash_share must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class TournamentConfig:
    """Everything that defines a tournament. The last blind level repeats forever."""

    players: int
    starting_stack_bb: float
    blind_levels: tuple[BlindLevel, ...]
    payout: tuple[float, ...]
    buy_in: float = 1.0
    placement_resolver: str = "stack_at_hand_start"
    name: str = ""
    bounty: BountyConfig | None = None
    stage: StageSpec | None = None
    """T5: our table is one table of a large MTT at this stage (`poker.mtt_stage`). Then
    `players` is our table, `starting_stack_bb` the field's average stack, `payout` is not
    used (prizes come from the stage) and the stage ends early (see `mtt_stage`)."""

    def __post_init__(self) -> None:
        if self.stage is not None and self.stage.players_left <= self.players:
            raise ValueError("a stage needs more players left than our table has")
        if not 2 <= self.players <= MAX_PLAYERS:
            raise ValueError(f"players must be between 2 and {MAX_PLAYERS}")
        if self.starting_stack_bb <= 0:
            raise ValueError("starting_stack_bb must be positive")
        if not self.blind_levels:
            raise ValueError("at least one blind level is required")
        if not self.payout or len(self.payout) > self.players:
            raise ValueError("payout must have between 1 and `players` places")
        if any(share < 0 for share in self.payout) or not math.isclose(sum(self.payout), 1.0):
            raise ValueError("payout shares must be non-negative and sum to 1")
        if self.buy_in <= 0:
            raise ValueError("buy_in must be positive")
        if self.placement_resolver not in PLACEMENT_RESOLVERS:
            raise ValueError(f"unknown placement resolver: {self.placement_resolver}")
        if self.starting_stack < self.blind_levels[0].big_blind:
            raise ValueError("the starting stack must cover the first big blind")

    @property
    def starting_stack(self) -> int:
        return round(self.starting_stack_bb * self.blind_levels[0].big_blind)

    @property
    def starting_bounty(self) -> float:
        """Bounty on every player's head at the start, in buy-ins (0 without PKO)."""
        return self.buy_in * self.bounty.bounty_share if self.bounty is not None else 0.0

    @property
    def prize_pool(self) -> float:
        return (self.buy_in - self.starting_bounty) * self.players

    def level_at(self, hand_index: int) -> tuple[int, BlindLevel, int]:
        """(level number from 1, level, hands left in it) for the hand with this index."""
        remaining = hand_index
        for number, level in enumerate(self.blind_levels, start=1):
            if number == len(self.blind_levels) or remaining < level.hands:
                left = level.hands - remaining if number < len(self.blind_levels) else 0
                return number, level, max(left, 0)
            remaining -= level.hands
        raise AssertionError("unreachable")

    def payout_for_place(self, place: int) -> float:
        return self.payout[place - 1] * self.prize_pool if place <= len(self.payout) else 0.0

    def to_json(self) -> str:
        data = asdict(self)
        if self.bounty is None:
            # Configs without PKO keep the exact JSON (and job idempotency) they had before.
            del data["bounty"]
        if self.stage is None:
            del data["stage"]
        else:
            data["stage"] = self.stage.to_dict()
        data["schema_version"] = CONFIG_SCHEMA
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> TournamentConfig:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("tournament config must be an object")
        if data.pop("schema_version", CONFIG_SCHEMA) != CONFIG_SCHEMA:
            raise ValueError("unsupported tournament config schema")
        try:
            data["blind_levels"] = tuple(BlindLevel(**level) for level in data["blind_levels"])
            data["payout"] = tuple(float(share) for share in data["payout"])
            if data.get("bounty") is not None:
                data["bounty"] = BountyConfig(**data["bounty"])
            if data.get("stage") is not None:
                data["stage"] = StageSpec.from_dict(data["stage"])
            return cls(**data)
        except (KeyError, TypeError) as error:
            raise ValueError(f"invalid tournament config: {error}") from error


# --- placement --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Bust:
    player: int
    stack_at_hand_start: int


PlacementResolver = Callable[[Sequence[Bust]], list[list[int]]]


def stack_at_hand_start(busts: Sequence[Bust]) -> list[list[int]]:
    """Order players who busted in the same hand, best first.

    A larger stack at the start of the hand finishes higher; equal stacks tie and share
    the prizes of the places they occupy.
    """
    groups: dict[int, list[int]] = {}
    for bust in busts:
        groups.setdefault(bust.stack_at_hand_start, []).append(bust.player)
    return [sorted(groups[stack]) for stack in sorted(groups, reverse=True)]


PLACEMENT_RESOLVERS: dict[str, PlacementResolver] = {
    "stack_at_hand_start": stack_at_hand_start,
}


# --- ICM --------------------------------------------------------------------------------


def icm_equities(stacks: Sequence[int], prizes: Sequence[float]) -> list[float]:
    """Malmuth-Harville ICM: each player's expected prize from chip stacks.

    `prizes[i]` is paid for finishing place i+1 among these players. Players with no
    chips get the last remaining prize(s) and are handled by the caller.
    """
    count = len(stacks)
    if count == 0:
        return []
    padded = tuple(prizes[:count]) + (0.0,) * max(0, count - len(prizes))
    return list(_icm(tuple(stacks), padded))


@lru_cache(maxsize=4096)
def _icm(stacks: tuple[int, ...], prizes: tuple[float, ...]) -> tuple[float, ...]:
    count = len(stacks)

    @cache
    def expected(remaining: int, place: int) -> tuple[float, ...]:
        """Expected prize per player for places >= `place` among `remaining` (bitmask)."""
        values = [0.0] * count
        members = [index for index in range(count) if remaining >> index & 1]
        if place >= len(prizes) or not members:
            return tuple(values)
        total = sum(stacks[index] for index in members)
        if total == 0:
            share = sum(prizes[place : place + len(members)]) / len(members)
            for index in members:
                values[index] = share
            return tuple(values)
        for index in members:
            probability = stacks[index] / total
            if probability == 0:
                continue
            values[index] += probability * prizes[place]
            rest = expected(remaining & ~(1 << index), place + 1)
            for other in members:
                if other != index:
                    values[other] += probability * rest[other]
        return tuple(values)

    return expected((1 << count) - 1, 0)


# --- hand information for tournament-aware agents ---------------------------------------


@dataclass(frozen=True, slots=True)
class TournamentHandInfo:
    """What every player knows about the tournament at the start of a hand."""

    hand_number: int
    level: int
    small_blind: int
    big_blind: int
    ante: int
    hands_to_next_level: int
    hands_per_level: int
    players_left: int
    starting_stack: int
    payout: tuple[float, ...]
    seat: int
    player: int
    seat_players: tuple[int, ...]
    bounties: tuple[float, ...] = ()
    """Bounty on each seat's head in buy-ins (PKO only; empty otherwise)."""
    starting_bounty: float = 0.0
    prizes: tuple[float, ...] = ()
    """Prize for each remaining place (1st, 2nd, ... among the players left), in buy-ins."""
    bounty_cash_share: float = 0.5
    """PKO: part of an eliminated player's bounty paid in cash (the rest is added)."""
    winner_keeps_own: bool = True
    field_size: int = 0
    """Players who started the tournament (0: unknown, use `players_left`)."""
    bounty_share: float = 0.0
    """PKO: part of the buy-in that is bounty (0 without bounties)."""
    blind_growth: float = 0.0
    """Big blind of the next level over this level's (0: unknown)."""
    stage: StageInfo | None = None
    """T5: where the whole MTT stands (None for a single-table tournament). Then `prizes`
    is the field's payout as seen from our table (`FieldIcm.table_ladder`)."""


@dataclass(frozen=True, slots=True)
class StageInfo:
    """The MTT around our table at the start of a hand (T5)."""

    field_size: int
    """Players who started the tournament."""
    field_left: int
    """Players left in the whole tournament."""
    paid: int
    """Places paid in the whole tournament."""
    field_average_stack: float
    """Average stack in the whole field, in chips."""
    min_cash: float
    """Prize of the last paid place, in buy-ins."""
    next_prize: float
    """Prize of the next place to be decided (0 when it is not paid: off the money)."""

    @property
    def to_money(self) -> int:
        """Players who must bust before everyone left is paid (0 when in the money)."""
        return max(0, self.field_left - self.paid)


class HandRunner(Protocol):
    """Plays one hand to the end (`TableRunner`, or a wrapper that records it)."""

    def run(self, hand: TableHand, agents: Sequence[TableAgent]) -> TableResult: ...


@runtime_checkable
class TournamentAware(Protocol):
    def begin_hand(self, info: TournamentHandInfo) -> None: ...


@dataclass(frozen=True, slots=True)
class StartedHand:
    """A dealt tournament hand (`Tournament.begin_hand`); seats are table seats."""

    hand: TableHand
    agents: tuple[TableAgent, ...]
    infos: tuple[TournamentHandInfo, ...]
    level: int
    seat_players: tuple[int, ...]
    stacks_before: tuple[int, ...]
    bounties_before: tuple[float, ...]
    icm_before: tuple[float, ...]
    record: bool


# --- results ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HandRecord:
    hand_number: int
    level: int
    small_blind: int
    big_blind: int
    ante: int
    button_player: int
    seat_players: tuple[int, ...]
    stacks_before: tuple[int, ...]
    stacks_after: tuple[int, ...]
    busted: tuple[int, ...]
    icm_before: tuple[float, ...]
    icm_after: tuple[float, ...]
    bounties_before: tuple[float, ...] = ()
    bounties_after: tuple[float, ...] = ()
    """Bounty on each seat's head (PKO telemetry, kept apart from ICM)."""
    knockouts: tuple[Knockout, ...] = ()


@dataclass(frozen=True, slots=True)
class Knockout:
    """One player's share of one eliminated player's bounty."""

    eliminated: int
    collector: int
    cash: float
    added_to_bounty: float


@dataclass(frozen=True, slots=True)
class TournamentResult:
    """Outcome per player id. Money is in buy-in units."""

    places: tuple[int, ...]
    prizes: tuple[float, ...]
    bounties: tuple[float, ...]
    results: tuple[float, ...]
    hands: int
    final_level: int
    elimination_order: tuple[tuple[int, ...], ...]
    records: tuple[HandRecord, ...] = field(default=())
    knockouts: tuple[Knockout, ...] = ()


# --- engine -----------------------------------------------------------------------------


class Tournament:
    def __init__(self, config: TournamentConfig, seed: int | str) -> None:
        self.config = config
        self.seed = seed
        self.stacks = [config.starting_stack] * config.players
        self.places: list[int | None] = [None] * config.players
        self.prizes = [0.0] * config.players
        self.heads = [config.starting_bounty] * config.players
        """Bounty currently on each player's head (PKO)."""
        self.bounty_cash = [0.0] * config.players
        """Bounty money each player has received."""
        self.knockouts: list[Knockout] = []
        self.hand_number = 0
        self.button_player = random.Random(f"{seed}:button").randrange(config.players)
        self.elimination_order: list[tuple[int, ...]] = []
        self.field: FieldState | None = None
        """T5: the other tables of the MTT (None for a single-table tournament)."""
        stage = config.stage
        if stage is not None:
            self.stacks = stage_table_stacks(
                stage, config.players, config.starting_stack * config.players, str(seed)
            )
            self.field = FieldState(stage, config.players, config.starting_stack, str(seed))
            if config.bounty is not None:
                self.heads = stage_bounties(
                    stage, config.players, config.starting_bounty, str(seed)
                )
        self.total_chips = sum(self.stacks)
        """Chips at our table (conserved hand to hand)."""
        self.bounties_in = sum(self.heads)
        self.total_field_chips = float(
            self.total_chips + (sum(self.field.stacks) if self.field is not None else 0.0)
        )
        self._equity_cache: tuple[tuple[object, ...], list[float]] | None = None
        self.starting_equity: list[float] = [config.buy_in] * config.players
        """What each player's result is measured from: the buy-in, or for a stage the field
        ICM equity when the stage starts (a stage result is the equity won or lost)."""
        if self.field is not None:
            self.starting_equity = self.stage_equities()

    @property
    def alive(self) -> list[int]:
        return [player for player in range(self.config.players) if self.stacks[player] > 0]

    @property
    def field_left(self) -> int:
        """Players left in the whole tournament (our table only without a stage)."""
        return len(self.alive) + (self.field.count if self.field is not None else 0)

    @property
    def complete(self) -> bool:
        stage = self.config.stage
        if stage is not None and self.field_left <= stage.end_players:
            return True
        return len(self.alive) <= 1

    def _prize(self, place: int) -> float:
        stage = self.config.stage
        return stage.prize(place) if stage is not None else self.config.payout_for_place(place)

    def stage_equities(self) -> list[float]:
        """Field ICM equity of our table's players still in (stage tournaments only)."""
        assert self.field is not None
        key = (tuple(self.stacks), self.field.count)
        if self._equity_cache is None or self._equity_cache[0] != key:
            table = [self.stacks[player] for player in self.alive]
            self._equity_cache = (key, self.field.icm().equities(table))
        return self._equity_cache[1]

    def stage_ladder(self) -> list[float]:
        """The field payout as seen from our table: prize per table place (`FieldIcm`)."""
        assert self.field is not None
        return self.field.icm().table_ladder([self.stacks[player] for player in self.alive])

    def run(
        self,
        agents: Sequence[TableAgent],
        record_hands: bool = False,
        runner: HandRunner | None = None,
    ) -> TournamentResult:
        """Play until one player has all the chips. `agents[i]` plays player id i."""
        if len(agents) != self.config.players:
            raise ValueError("one agent is required per player")
        runner = runner or TableRunner()
        records: list[HandRecord] = []
        while not self.complete:
            record = self.play_hand(agents, runner, record_hands)
            if record is not None:
                records.append(record)
            if self.hand_number > MAX_HANDS:
                raise RuntimeError(f"tournament did not finish within {MAX_HANDS} hands")
        return self.finish(tuple(records))

    def finish(self, records: tuple[HandRecord, ...] = ()) -> TournamentResult:
        """Place the last player standing and return the result (after the final hand)."""
        if not self.complete:
            raise AssertionError("tournament is not over yet")
        if self.field is not None:
            return self._finish_stage(records)
        winner = self.alive[0]
        self.places[winner] = 1
        self.prizes[winner] = self.config.payout_for_place(1)
        bounty = self.config.bounty
        if bounty is not None and bounty.winner_keeps_own:
            self.bounty_cash[winner] += self.heads[winner]
            self.heads[winner] = 0.0
        self.elimination_order.append((winner,))
        return self.result(tuple(records))

    def _finish_stage(self, records: tuple[HandRecord, ...]) -> TournamentResult:
        """End of a stage: players still in get their field ICM equity as their prize.

        Their place is where their stack ranks in the whole field (for ITM statistics);
        bounties still on heads stay there (they are only worth something when won).
        """
        assert self.field is not None
        alive = self.alive
        equities = self.stage_equities()
        field = self.field.stacks
        for player, equity in zip(alive, equities, strict=True):
            stack = self.stacks[player]
            ahead = sum(1 for other in field if other > stack) + sum(
                1 for other in alive if self.stacks[other] > stack
            )
            self.places[player] = ahead + 1
            self.prizes[player] = equity
        self.elimination_order.append(tuple(alive))
        return self.result(tuple(records))

    def equity(self, player: int) -> float:
        """Money this player can expect from here, in buy-ins, for reward shaping.

        Busted players get their prize. Players still in get their ICM share of the
        remaining prizes, plus bounty cash already collected, plus the bounty on their own
        head when the winner keeps it and they are the last player left. Other bounties
        still in play are not valued (they are worth something only when won). The value
        at the end of the tournament is exactly the player's prize plus bounty cash.
        """
        cash = self.bounty_cash[player]
        if self.stacks[player] == 0:
            return self.prizes[player] + cash
        alive = self.alive
        if self.field is not None:
            return self.stage_equities()[alive.index(player)] + cash
        if len(alive) == 1:
            own = self.heads[player]
            bounty = self.config.bounty
            keeps = bounty is not None and bounty.winner_keeps_own
            return self.config.payout_for_place(1) + cash + (own if keeps else 0.0)
        shares = icm_equities(
            [self.stacks[other] for other in alive], self._remaining_prizes(len(alive))
        )
        return shares[alive.index(player)] + cash

    def play_hand(
        self, agents: Sequence[TableAgent], runner: HandRunner, record: bool = False
    ) -> HandRecord | None:
        started = self.begin_hand(agents, record)
        result = runner.run(started.hand, started.agents)
        return self.finish_hand(started, result)

    def begin_hand(self, agents: Sequence[TableAgent], record: bool = False) -> StartedHand:
        """Deal the next hand and tell tournament-aware agents about it.

        `play_hand` is `begin_hand`, playing `StartedHand.hand` to the end, then
        `finish_hand`. The split lets a caller play the hand one action at a time (for
        example while waiting for a human).
        """
        if self.complete:
            raise AssertionError("tournament is over")
        config = self.config
        seat_players = tuple(self.alive)
        count = len(seat_players)
        if self.hand_number > 0:
            # Simplified rule (no dead button): the button moves to the next player who
            # still has chips.
            self.button_player = self._next_alive_after(self.button_player)
        level_number, level, left = config.level_at(self.hand_number)
        stacks_before = tuple(self.stacks[player] for player in seat_players)
        stage_info: StageInfo | None = None
        if self.field is not None:
            # Players and agents see the field's payout through our table: what each table
            # place is worth (the bubble shows as a step).
            prizes = self.stage_ladder()
            icm_before = tuple(self.stage_equities()) if record else ()
            stage = config.stage
            assert stage is not None
            field_left = self.field_left
            stage_info = StageInfo(
                field_size=stage.field_size,
                field_left=field_left,
                paid=stage.paid,
                field_average_stack=self.total_field_chips / field_left,
                min_cash=stage.prize(stage.paid),
                next_prize=stage.prize(field_left - 1) if field_left - 1 <= stage.paid else 0.0,
            )
        else:
            prizes = self._remaining_prizes(count)
            icm_before = tuple(icm_equities(stacks_before, prizes)) if record else ()
        button_seat = seat_players.index(self.button_player)
        bounties_before = (
            tuple(self.heads[player] for player in seat_players)
            if config.bounty is not None
            else ()
        )
        hand = TableHand(
            stacks_before,
            button_seat,
            level.small_blind,
            level.big_blind,
            seed=f"{self.seed}:{self.hand_number}",
            big_blind_ante=level.ante,
        )
        seat_agents = tuple(agents[player] for player in seat_players)
        infos = tuple(
            TournamentHandInfo(
                hand_number=self.hand_number,
                level=level_number,
                small_blind=level.small_blind,
                big_blind=level.big_blind,
                ante=level.ante,
                hands_to_next_level=left,
                hands_per_level=level.hands,
                players_left=count,
                starting_stack=config.starting_stack,
                payout=config.payout,
                seat=seat,
                player=seat_players[seat],
                seat_players=seat_players,
                bounties=bounties_before,
                starting_bounty=config.starting_bounty,
                prizes=tuple(prizes),
                bounty_cash_share=config.bounty.cash_share if config.bounty is not None else 0.5,
                winner_keeps_own=config.bounty.winner_keeps_own
                if config.bounty is not None
                else True,
                field_size=config.players,
                bounty_share=config.bounty.bounty_share if config.bounty is not None else 0.0,
                blind_growth=config.level_at(self.hand_number + left)[1].big_blind / level.big_blind
                if left
                else 1.0,
                stage=stage_info,
            )
            for seat in range(count)
        )
        for seat, agent in enumerate(seat_agents):
            if isinstance(agent, TournamentAware):
                agent.begin_hand(infos[seat])
        return StartedHand(
            hand=hand,
            agents=seat_agents,
            infos=infos,
            level=level_number,
            seat_players=seat_players,
            stacks_before=stacks_before,
            bounties_before=bounties_before,
            icm_before=icm_before,
            record=record,
        )

    def finish_hand(self, started: StartedHand, result: TableResult) -> HandRecord | None:
        """Apply a finished hand from `begin_hand` to the tournament."""
        config = self.config
        seat_players = started.seat_players
        stacks_before = started.stacks_before
        count = len(seat_players)
        level = config.level_at(self.hand_number)[1]
        for seat, player in enumerate(seat_players):
            self.stacks[player] = result.final_stacks[seat]
        if sum(self.stacks) != self.total_chips:
            raise AssertionError("chip conservation violated across hands")
        busted = [
            Bust(player, stacks_before[seat])
            for seat, player in enumerate(seat_players)
            if self.stacks[player] == 0
        ]
        knockouts: list[Knockout] = []
        field_before = self.field_left + len(busted)
        if busted:
            if config.bounty is not None:
                knockouts = self._collect_bounties(busted, seat_players, result.pots)
            if self.field is not None:
                # Places in the whole field: just below everyone still in.
                self._place(busted, field_before)
            else:
                self._place(busted, count)
        if self.field is not None:
            stage = config.stage
            assert stage is not None
            # The other tables play their hand too (after ours: ties go to our table).
            self.field.step(self.field_left, self.field_left - stage.end_players)
        self.hand_number += 1
        if not started.record:
            return None
        level_number = started.level
        icm_before = started.icm_before
        bounties_before = started.bounties_before
        stacks_after = tuple(self.stacks[player] for player in seat_players)
        if self.field is not None:
            alive = self.alive
            equities = dict(zip(alive, self.stage_equities(), strict=True)) if alive else {}
            icm_after = tuple(
                self.prizes[player] if self.stacks[player] == 0 else equities[player]
                for player in seat_players
            )
        else:
            icm_after = tuple(
                self.prizes[player]
                if self.stacks[player] == 0
                else icm_equities(
                    [self.stacks[p] for p in self.alive], self._remaining_prizes(len(self.alive))
                )[self.alive.index(player)]
                for player in seat_players
            )
        return HandRecord(
            hand_number=self.hand_number - 1,
            level=level_number,
            small_blind=level.small_blind,
            big_blind=level.big_blind,
            ante=level.ante,
            button_player=self.button_player,
            seat_players=seat_players,
            stacks_before=stacks_before,
            stacks_after=stacks_after,
            busted=tuple(bust.player for bust in busted),
            icm_before=icm_before,
            icm_after=icm_after,
            bounties_before=bounties_before,
            bounties_after=(
                tuple(self.heads[player] for player in seat_players)
                if config.bounty is not None
                else ()
            ),
            knockouts=tuple(knockouts),
        )

    def result(self, records: tuple[HandRecord, ...] = ()) -> TournamentResult:
        if any(place is None for place in self.places):
            raise AssertionError("tournament is not finished")
        places = tuple(int(place or 0) for place in self.places)
        prizes = tuple(self.prizes)
        bounties = tuple(self.bounty_cash)
        results = tuple(
            prize + bounty - start
            for prize, bounty, start in zip(prizes, bounties, self.starting_equity, strict=True)
        )
        if self.field is None and not math.isclose(
            sum(prizes), self.config.prize_pool, rel_tol=1e-9
        ):
            raise AssertionError("prizes do not add up to the prize pool")
        paid_in = self.bounties_in
        if not math.isclose(sum(bounties) + sum(self.heads), paid_in, abs_tol=1e-9):
            raise AssertionError("bounties paid plus bounties left must equal bounties paid in")
        return TournamentResult(
            places=places,
            prizes=prizes,
            bounties=bounties,
            results=results,
            hands=self.hand_number,
            final_level=self.config.level_at(max(self.hand_number - 1, 0))[0],
            elimination_order=tuple(self.elimination_order),
            records=records,
            knockouts=tuple(self.knockouts),
        )

    def _collect_bounties(
        self,
        busted: Sequence[Bust],
        seat_players: Sequence[int],
        pots: Sequence[PotAward],
    ) -> list[Knockout]:
        """Pay each eliminated player's bounty to whoever won the pot they were all in for.

        That is the highest pot the eliminated player was eligible for: everyone else
        eligible for it put in at least as many chips, so the winner covered them. Tied
        winners split the bounty. After a fold the player who did not fold collects.
        """
        bounty = self.config.bounty
        if bounty is None:
            return []
        knockouts: list[Knockout] = []
        seat_of = {player: seat for seat, player in enumerate(seat_players)}
        for bust in busted:
            seat = seat_of[bust.player]
            contested = [pot for pot in pots if seat in pot.eligible]
            collectors = contested[-1].winners if contested else pots[-1].winners
            winners = [seat_players[index] for index in collectors if index != seat]
            if not winners:
                raise AssertionError("an eliminated player's bounty must have a collector")
            head = self.heads[bust.player]
            self.heads[bust.player] = 0.0
            share = head / len(winners)
            for collector in winners:
                cash = share * bounty.cash_share
                added = share - cash
                self.bounty_cash[collector] += cash
                self.heads[collector] += added
                knockout = Knockout(bust.player, collector, cash, added)
                knockouts.append(knockout)
                self.knockouts.append(knockout)
        return knockouts

    def _next_alive_after(self, player: int) -> int:
        for offset in range(1, self.config.players + 1):
            candidate = (player + offset) % self.config.players
            if self.stacks[candidate] > 0:
                return candidate
        raise AssertionError("no player has chips")

    def _remaining_prizes(self, count: int) -> list[float]:
        return [self.config.payout_for_place(place) for place in range(1, count + 1)]

    def _place(self, busted: Sequence[Bust], players_before: int) -> None:
        """Give this hand's busted players the places just below the survivors.

        The resolver lists groups best first; the best group gets the best open place.
        A group of tied players all get the group's best place number and share the
        prizes of every place the group occupies.
        """
        resolver = PLACEMENT_RESOLVERS[self.config.placement_resolver]
        groups = resolver(busted)
        if sorted(player for group in groups for player in group) != sorted(
            bust.player for bust in busted
        ):
            raise AssertionError("placement resolver must place every busted player once")
        next_place = players_before - len(busted) + 1
        for group in groups:
            places = range(next_place, next_place + len(group))
            share = sum(self._prize(place) for place in places) / len(group)
            for player in group:
                self.places[player] = next_place
                self.prizes[player] = share
            next_place += len(group)
        self.elimination_order.append(tuple(sorted(bust.player for bust in busted)))


def run_tournament(
    config: TournamentConfig,
    seed: int | str,
    agents: Sequence[TableAgent],
    record_hands: bool = False,
) -> TournamentResult:
    return Tournament(config, seed).run(agents, record_hands)


# --- presets ----------------------------------------------------------------------------

_BLIND_STEPS = (
    (10, 20),
    (15, 30),
    (20, 40),
    (25, 50),
    (30, 60),
    (40, 80),
    (50, 100),
    (60, 120),
    (80, 160),
    (100, 200),
    (125, 250),
    (150, 300),
    (200, 400),
    (250, 500),
    (300, 600),
    (400, 800),
    (500, 1_000),
    (600, 1_200),
    (800, 1_600),
    (1_000, 2_000),
)


def standard_levels(
    hands_per_level: int = 10, big_blind_ante: bool = True
) -> tuple[BlindLevel, ...]:
    """Blinds from 10/20, rising roughly 25% per level, with a big blind ante of one BB."""
    return tuple(
        BlindLevel(small, big, big if big_blind_ante else 0, hands_per_level)
        for small, big in _BLIND_STEPS
    )


PAYOUTS: Mapping[str, tuple[float, ...]] = {
    "65-35": (0.65, 0.35),
    "50-30-20": (0.5, 0.3, 0.2),
    "winner-take-all": (1.0,),
}


def preset_config(
    starting_stack_bb: float = 10,
    payout: str = "65-35",
    players: int = 6,
    hands_per_level: int = 10,
    bounty: BountyConfig | None = None,
) -> TournamentConfig:
    """Common settings; pass `bounty=BountyConfig()` for a 50/50 progressive knockout."""
    kind = "pko-" if bounty is not None else ""
    return TournamentConfig(
        players=players,
        starting_stack_bb=starting_stack_bb,
        blind_levels=standard_levels(hands_per_level),
        payout=PAYOUTS[payout],
        name=f"{players}max-{kind}{starting_stack_bb:g}bb-{payout}",
        bounty=bounty,
    )
