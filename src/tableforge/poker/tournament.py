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
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from functools import cache, lru_cache
from typing import Protocol, runtime_checkable

from tableforge.poker.table import TableAgent, TableHand, TableRunner

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
class TournamentConfig:
    """Everything that defines a tournament. The last blind level repeats forever."""

    players: int
    starting_stack_bb: float
    blind_levels: tuple[BlindLevel, ...]
    payout: tuple[float, ...]
    buy_in: float = 1.0
    placement_resolver: str = "stack_at_hand_start"
    name: str = ""

    def __post_init__(self) -> None:
        if not 2 <= self.players <= 6:
            raise ValueError("players must be between 2 and 6")
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
    def prize_pool(self) -> float:
        return self.buy_in * self.players

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


@runtime_checkable
class TournamentAware(Protocol):
    def begin_hand(self, info: TournamentHandInfo) -> None: ...


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


# --- engine -----------------------------------------------------------------------------


class Tournament:
    def __init__(self, config: TournamentConfig, seed: int | str) -> None:
        self.config = config
        self.seed = seed
        self.stacks = [config.starting_stack] * config.players
        self.places: list[int | None] = [None] * config.players
        self.prizes = [0.0] * config.players
        self.hand_number = 0
        self.button_player = random.Random(f"{seed}:button").randrange(config.players)
        self.elimination_order: list[tuple[int, ...]] = []

    @property
    def alive(self) -> list[int]:
        return [player for player in range(self.config.players) if self.stacks[player] > 0]

    @property
    def complete(self) -> bool:
        return len(self.alive) <= 1

    def run(
        self,
        agents: Sequence[TableAgent],
        record_hands: bool = False,
        runner: TableRunner | None = None,
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
        winner = self.alive[0]
        self.places[winner] = 1
        self.prizes[winner] = self.config.payout_for_place(1)
        self.elimination_order.append((winner,))
        return self.result(tuple(records))

    def play_hand(
        self, agents: Sequence[TableAgent], runner: TableRunner, record: bool = False
    ) -> HandRecord | None:
        config = self.config
        seat_players = tuple(self.alive)
        count = len(seat_players)
        if self.hand_number > 0:
            # Simplified rule (no dead button): the button moves to the next player who
            # still has chips.
            self.button_player = self._next_alive_after(self.button_player)
        level_number, level, left = config.level_at(self.hand_number)
        stacks_before = tuple(self.stacks[player] for player in seat_players)
        prizes = self._remaining_prizes(count)
        icm_before = tuple(icm_equities(stacks_before, prizes)) if record else ()
        button_seat = seat_players.index(self.button_player)
        hand = TableHand(
            stacks_before,
            button_seat,
            level.small_blind,
            level.big_blind,
            seed=f"{self.seed}:{self.hand_number}",
            big_blind_ante=level.ante,
        )
        seat_agents = [agents[player] for player in seat_players]
        for seat, agent in enumerate(seat_agents):
            if isinstance(agent, TournamentAware):
                agent.begin_hand(
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
                    )
                )
        result = runner.run(hand, seat_agents)
        for seat, player in enumerate(seat_players):
            self.stacks[player] = result.final_stacks[seat]
        if sum(self.stacks) != config.starting_stack * config.players:
            raise AssertionError("chip conservation violated across hands")
        busted = [
            Bust(player, stacks_before[seat])
            for seat, player in enumerate(seat_players)
            if self.stacks[player] == 0
        ]
        if busted:
            self._place(busted, count)
        self.hand_number += 1
        if not record:
            return None
        stacks_after = tuple(self.stacks[player] for player in seat_players)
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
        )

    def result(self, records: tuple[HandRecord, ...] = ()) -> TournamentResult:
        if any(place is None for place in self.places):
            raise AssertionError("tournament is not finished")
        places = tuple(int(place or 0) for place in self.places)
        prizes = tuple(self.prizes)
        bounties = (0.0,) * self.config.players
        results = tuple(
            prize + bounty - self.config.buy_in
            for prize, bounty in zip(prizes, bounties, strict=True)
        )
        if not math.isclose(sum(prizes), self.config.prize_pool, rel_tol=1e-9):
            raise AssertionError("prizes do not add up to the prize pool")
        return TournamentResult(
            places=places,
            prizes=prizes,
            bounties=bounties,
            results=results,
            hands=self.hand_number,
            final_level=self.config.level_at(max(self.hand_number - 1, 0))[0],
            elimination_order=tuple(self.elimination_order),
            records=records,
        )

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
            share = sum(self.config.payout_for_place(place) for place in places) / len(group)
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
) -> TournamentConfig:
    return TournamentConfig(
        players=players,
        starting_stack_bb=starting_stack_bb,
        blind_levels=standard_levels(hands_per_level),
        payout=PAYOUTS[payout],
        name=f"{players}max-{starting_stack_bb:g}bb-{payout}",
    )
