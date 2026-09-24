from __future__ import annotations

import hashlib
import json
from collections import Counter

import pytest

pytest.importorskip("torch")

from tableforge.domain.league import (  # noqa: E402
    HistoricalCheckpoint,
    LeagueMatchupPayload,
    LeagueMember,
    MemberKind,
    build_population,
)
from tableforge.domain.learning import NfspActorPayload, NfspInitPayload  # noqa: E402
from tableforge.learning.iterative import NfspActorExecutor, NfspInitExecutor  # noqa: E402
from tableforge.learning.league import (  # noqa: E402
    LeagueMatchupExecutor,
    MemberLoader,
    population_picker,
)


class Store:
    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}

    def put(self, content: bytes) -> tuple[str, str]:
        digest = hashlib.sha256(content).hexdigest()
        self.items[digest[:16]] = content
        return digest[:16], digest

    def fetch(self, artifact_id: str) -> bytes:
        return self.items[artifact_id]


def checkpoint(store: Store, seed: int) -> tuple[str, str]:
    return store.put(
        NfspInitExecutor().execute(
            NfspInitPayload("1.0", agent_seed=seed, hidden_size=32, batch_size=32).to_bytes()
        )
    )


def test_picker_is_deterministic_and_follows_weights() -> None:
    store = Store()
    old = checkpoint(store, 1)
    population = build_population(
        2, "f" * 64, (HistoricalCheckpoint("round-0", old[0], old[1]),)
    )

    def kinds(seed: int) -> list[str]:
        pick = population_picker(population, MemberLoader(store.fetch), seed)
        return [type(pick(index)).__name__ for index in range(2_000)]

    first = kinds(5)
    assert first == kinds(5)
    counts = Counter(first)
    assert 0.33 < counts["NoneType"] / 2_000 < 0.47
    assert 0.24 < counts["NfspAgent"] / 2_000 < 0.36
    assert sum(counts[name] for name in ("CallBot", "RandomBot", "TightBot", "AggroBot")) > 400


def test_actor_with_population_is_deterministic() -> None:
    store = Store()
    base = checkpoint(store, 1)
    old = checkpoint(store, 2)
    population = store.put(
        build_population(
            1, base[1], (HistoricalCheckpoint("round-0", old[0], old[1]),)
        ).to_bytes()
    )
    payload = NfspActorPayload("1.0", base[0], base[1], 200, 0, 3, population[0], population[1])
    executor = NfspActorExecutor(store.fetch)

    assert executor.execute(payload.to_bytes()) == executor.execute(payload.to_bytes())


def test_actor_rejects_population_of_other_checkpoint() -> None:
    store = Store()
    base = checkpoint(store, 1)
    other = checkpoint(store, 2)
    population = store.put(build_population(1, other[1], ()).to_bytes())
    payload = NfspActorPayload("1.0", base[0], base[1], 20, 0, 3, population[0], population[1])

    with pytest.raises(ValueError, match="different checkpoint"):
        NfspActorExecutor(store.fetch).execute(payload.to_bytes())


def test_matchup_reports_named_result_with_interval() -> None:
    store = Store()
    first = checkpoint(store, 1)
    result = json.loads(
        LeagueMatchupExecutor(store.fetch).execute(
            LeagueMatchupPayload(
                "1.0",
                LeagueMember("a", MemberKind.CHECKPOINT, artifact_id=first[0], sha256=first[1]),
                LeagueMember("b", MemberKind.RULE, rule="call"),
                40,
                0,
            ).to_bytes()
        )
    )

    assert result["hands"] == 80
    assert result["confidence_low"] <= result["bb_per_100"] <= result["confidence_high"]
    assert (result["first"], result["second"]) == ("a", "b")
