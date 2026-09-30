"""Benchmarks against a pool of trained models.

* **Pool**: the other seats are filled from the pool models in rotation (player `p` of seed
  `s` is member ``(s + p) mod n``), every seed played once per seat for the candidate. This
  is the headline number for C: "does the candidate beat the models we already have?"
* **Matchups**: for every pool member, the other seats are all that member. Recorded for
  every member; strategies can counter each other, so losing one matchup is information,
  not a failure.
* **Paired difference**: two candidates on identical tournaments (same seeds and seats),
  per-seed difference of results, so a regression check needs far fewer tournaments.

Pools only contain models trained for the same tournament (compared on engine settings, not
names); the candidate itself is left out when it is in the library (leave-one-out).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from tableforge.learning.opponents import PoolMember, model_member
from tableforge.poker.table import TableAgent
from tableforge.poker.tournament import TournamentConfig
from tableforge.poker.tournament_benchmark import (
    Estimate,
    TournamentBenchmark,
    TournamentSamples,
    collect_samples,
)

AgentFactory = Callable[[], TableAgent]


def same_tournament(first: TournamentConfig, second: TournamentConfig) -> bool:
    """Same engine settings (names are only labels)."""
    return replace(first, name="") == replace(second, name="")


def library_pool(
    library: Any,
    config: TournamentConfig,
    exclude: Sequence[str] = (),
    mode: str = "average",
) -> list[PoolMember]:
    """Every model in `library` trained for `config`, except `exclude`."""
    from tableforge.coordinator.analysis import AnalysisError, tournament_json

    members = []
    for info in library.list():
        if info.model_id in exclude or info.algorithm != "nfsp":
            continue
        try:
            model_config = TournamentConfig.from_json(tournament_json(info))
        except AnalysisError:
            continue
        if same_tournament(model_config, config):
            members.append(
                model_member(f"model:{info.model_id}", library.checkpoint(info.model_id), mode=mode)
            )
    return members


def rotating_field(members: Sequence[PoolMember]) -> Callable[[int, int], TableAgent]:
    if not members:
        raise ValueError("the pool is empty")

    def build(player: int, seed: int) -> TableAgent:
        tournament_seed = seed // 16
        return members[(tournament_seed + player) % len(members)].build(seed)

    return build


def uniform_field(member: PoolMember) -> Callable[[int, int], TableAgent]:
    return lambda player, seed: member.build(seed)


def pool_benchmark(
    candidate: AgentFactory,
    members: Sequence[PoolMember],
    config: TournamentConfig,
    seeds: int,
    seed_start: int,
    matchup_seeds: int = 0,
) -> dict[str, object]:
    """ROI against the whole pool, and (optionally) against each member alone."""
    overall = TournamentBenchmark.from_samples(
        collect_samples(candidate, rotating_field(members), config, seeds, seed_start), config.name
    ).summary()
    matchups: dict[str, object] = {}
    if matchup_seeds:
        for member in members:
            summary = TournamentBenchmark.from_samples(
                collect_samples(
                    candidate, uniform_field(member), config, matchup_seeds, seed_start
                ),
                config.name,
            ).summary()
            matchups[member.name] = {
                key: summary[key] for key in ("roi", "bounty_ev", "itm", "tournaments")
            }
    return {
        "members": [member.name for member in members],
        "overall": {
            key: overall[key]
            for key in ("roi", "prize_ev", "bounty_ev", "itm", "average_place", "tournaments")
        },
        "matchups": matchups,
    }


def paired_difference(first: TournamentSamples, second: TournamentSamples) -> Estimate:
    """ROI difference (first minus second), per seed, in buy-ins; both on the same seeds."""
    if len(first.total) != len(second.total):
        raise ValueError("paired samples need the same seeds")
    return Estimate.of(
        [(a - b) / first.buy_in for a, b in zip(first.total, second.total, strict=True)]
    )
