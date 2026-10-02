"""Tournament benchmark: how much money a candidate makes in a field of opponents.

Every tournament seed is replayed once per starting seat, with the candidate in that
seat and the same cards and opponents otherwise, so luck of the deal and of the seat
averages out. Metrics are per tournament, in buy-in units, with 95% confidence intervals
computed over seeds (the average over one seed's seat rotations is one observation).

`collect_samples` returns raw per-seed values that can be merged across jobs; the
summary is computed from the merged samples.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from tableforge.poker.table import TableAgent
from tableforge.poker.tournament import TournamentConfig, run_tournament

AgentFactory = Callable[[], TableAgent]
FieldFactory = Callable[[int, int], TableAgent]
"""`field(player, seed)` builds the opponent for a player id; rebuilt for each tournament."""

SAMPLES_SCHEMA = "tableforge.tournament-samples.v1"


@dataclass(frozen=True, slots=True)
class Estimate:
    mean: float
    low: float
    high: float

    @classmethod
    def of(cls, values: Sequence[float]) -> Estimate:
        if not values:
            return cls(0.0, 0.0, 0.0)
        mean = sum(values) / len(values)
        if len(values) < 2:
            return cls(mean, -math.inf, math.inf)
        variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
        error = math.sqrt(variance / len(values))
        return cls(mean, mean - 1.96 * error, mean + 1.96 * error)

    def as_dict(self, digits: int = 4) -> dict[str, float]:
        return {
            "mean": round(self.mean, digits),
            "low": round(self.low, digits),
            "high": round(self.high, digits),
        }


@dataclass(frozen=True, slots=True)
class TournamentSamples:
    """Per-seed averages (over seat rotations) and totals; merge by concatenation."""

    players: int
    buy_in: float
    tournaments: int = 0
    hands: int = 0
    prize: tuple[float, ...] = ()
    bounty: tuple[float, ...] = ()
    total: tuple[float, ...] = ()
    place: tuple[float, ...] = ()
    itm: tuple[float, ...] = ()
    place_counts: tuple[int, ...] = field(default=())

    def merge(self, other: TournamentSamples) -> TournamentSamples:
        if (self.players, self.buy_in) != (other.players, other.buy_in):
            raise ValueError("cannot merge samples from different tournament settings")
        counts = self.place_counts or (0,) * self.players
        other_counts = other.place_counts or (0,) * self.players
        return TournamentSamples(
            players=self.players,
            buy_in=self.buy_in,
            tournaments=self.tournaments + other.tournaments,
            hands=self.hands + other.hands,
            prize=self.prize + other.prize,
            bounty=self.bounty + other.bounty,
            total=self.total + other.total,
            place=self.place + other.place,
            itm=self.itm + other.itm,
            place_counts=tuple(a + b for a, b in zip(counts, other_counts, strict=True)),
        )

    def to_json(self) -> bytes:
        return json.dumps(
            {
                "schema_version": SAMPLES_SCHEMA,
                "players": self.players,
                "buy_in": self.buy_in,
                "tournaments": self.tournaments,
                "hands": self.hands,
                "prize": list(self.prize),
                "bounty": list(self.bounty),
                "total": list(self.total),
                "place": list(self.place),
                "itm": list(self.itm),
                "place_counts": list(self.place_counts),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    @classmethod
    def from_json(cls, content: bytes) -> TournamentSamples:
        data = json.loads(content)
        if data.get("schema_version") != SAMPLES_SCHEMA:
            raise ValueError("unsupported tournament samples schema")
        return cls(
            players=int(data["players"]),
            buy_in=float(data["buy_in"]),
            tournaments=int(data["tournaments"]),
            hands=int(data["hands"]),
            prize=tuple(float(value) for value in data["prize"]),
            bounty=tuple(float(value) for value in data["bounty"]),
            total=tuple(float(value) for value in data["total"]),
            place=tuple(float(value) for value in data["place"]),
            itm=tuple(float(value) for value in data["itm"]),
            place_counts=tuple(int(value) for value in data["place_counts"]),
        )


@dataclass(frozen=True, slots=True)
class TournamentBenchmark:
    config_name: str
    seeds: int
    tournaments: int
    prize_ev: Estimate
    bounty_ev: Estimate
    total_ev: Estimate
    roi: Estimate
    average_place: Estimate
    itm: Estimate
    place_share: tuple[float, ...]
    average_hands: float

    @classmethod
    def from_samples(cls, samples: TournamentSamples, config_name: str = "") -> TournamentBenchmark:
        tournaments = max(samples.tournaments, 1)
        return cls(
            config_name=config_name,
            seeds=len(samples.total),
            tournaments=samples.tournaments,
            prize_ev=Estimate.of(samples.prize),
            bounty_ev=Estimate.of(samples.bounty),
            total_ev=Estimate.of(samples.total),
            roi=Estimate.of([value / samples.buy_in for value in samples.total]),
            average_place=Estimate.of(samples.place),
            itm=Estimate.of(samples.itm),
            place_share=tuple(count / tournaments for count in samples.place_counts),
            average_hands=samples.hands / tournaments,
        )

    def summary(self) -> dict[str, object]:
        return {
            "config": self.config_name,
            "seeds": self.seeds,
            "tournaments": self.tournaments,
            "prize_ev": self.prize_ev.as_dict(),
            "bounty_ev": self.bounty_ev.as_dict(),
            "total_ev": self.total_ev.as_dict(),
            "roi": self.roi.as_dict(),
            "average_place": self.average_place.as_dict(3),
            "itm": self.itm.as_dict(),
            "place_share": [round(share, 4) for share in self.place_share],
            "average_hands": round(self.average_hands, 2),
        }


def collect_samples(
    candidate: AgentFactory,
    field_factory: FieldFactory,
    config: TournamentConfig,
    seeds: int,
    seed_start: int = 0,
    rotate_seats: bool = True,
    on_progress: Callable[[int], None] | None = None,
) -> TournamentSamples:
    players = config.players
    seats = range(players) if rotate_seats else range(1)
    paid_places = (
        config.stage.paid
        if config.stage is not None
        else sum(1 for share in config.payout if share > 0)
    )
    prize: list[float] = []
    bounty: list[float] = []
    total: list[float] = []
    place_values: list[float] = []
    itm: list[float] = []
    place_counts = [0] * players
    hands = 0
    for index in range(seeds):
        seed = seed_start + index
        sums = [0.0] * 5
        for seat in seats:
            player = seat if rotate_seats else seed % players
            agents = [
                candidate() if other == player else field_factory(other, seed * 16 + other)
                for other in range(players)
            ]
            result = run_tournament(config, seed, agents)
            hands += result.hands
            place = result.places[player]
            # In an MTT stage places are in the whole field: count the place at our table.
            table_place = 1 + sum(1 for other in result.places if other < place)
            place_counts[min(table_place, players) - 1] += 1
            sums[0] += result.prizes[player]
            sums[1] += result.bounties[player]
            sums[2] += result.results[player]
            sums[3] += place
            sums[4] += 1.0 if place <= paid_places else 0.0
        count = len(seats)
        prize.append(sums[0] / count)
        bounty.append(sums[1] / count)
        total.append(sums[2] / count)
        place_values.append(sums[3] / count)
        itm.append(sums[4] / count)
        if on_progress is not None:
            on_progress(index + 1)
    return TournamentSamples(
        players=players,
        buy_in=config.buy_in,
        tournaments=seeds * len(seats),
        hands=hands,
        prize=tuple(prize),
        bounty=tuple(bounty),
        total=tuple(total),
        place=tuple(place_values),
        itm=tuple(itm),
        place_counts=tuple(place_counts),
    )


@dataclass(frozen=True, slots=True)
class EvaluationProtocol:
    """Everything that must be identical when two players' ROIs are compared.

    A model and a baseline are only comparable when they play the same tournament config,
    against the same field, with the same seeds and the same seat rotation. Build one
    protocol and evaluate every candidate with it (`evaluate_with`).
    """

    config_json: str
    field: str = "mixed"
    seeds: int = 1_000
    seed_start: int = 1_500_000_000
    rotate_seats: bool = True

    def __post_init__(self) -> None:
        TournamentConfig.from_json(self.config_json)
        if self.seeds < 2:
            raise ValueError("a benchmark needs at least two seeds")

    @property
    def config(self) -> TournamentConfig:
        return TournamentConfig.from_json(self.config_json)

    def describe(self) -> dict[str, object]:
        config = self.config
        return {
            "tournament": config.name,
            "config_sha256": _sha256(self.config_json),
            "field": self.field,
            "seeds": self.seeds,
            "seed_start": self.seed_start,
            "rotate_seats": self.rotate_seats,
            "tournaments": self.seeds * (config.players if self.rotate_seats else 1),
        }


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


def evaluate_with(
    protocol: EvaluationProtocol,
    candidate: AgentFactory,
    field_factory: Callable[[str], FieldFactory],
    on_progress: Callable[[int], None] | None = None,
) -> TournamentBenchmark:
    """Evaluate one candidate under `protocol` (`field_factory(name)` builds the field)."""
    config = protocol.config
    samples = collect_samples(
        candidate,
        field_factory(protocol.field),
        config,
        protocol.seeds,
        protocol.seed_start,
        protocol.rotate_seats,
        on_progress,
    )
    return TournamentBenchmark.from_samples(samples, config.name)


def evaluate_tournament(
    candidate: AgentFactory,
    field: FieldFactory,
    config: TournamentConfig,
    seeds: int,
    seed_start: int = 0,
    rotate_seats: bool = True,
    on_progress: Callable[[int], None] | None = None,
) -> TournamentBenchmark:
    """Play `seeds` x (players if rotating seats, else 1) tournaments for `candidate`."""
    if seeds < 2:
        raise ValueError("a benchmark needs at least two seeds")
    samples = collect_samples(
        candidate, field, config, seeds, seed_start, rotate_seats, on_progress
    )
    return TournamentBenchmark.from_samples(samples, config.name)
