from __future__ import annotations

import random

import pytest

pytest.importorskip("torch")

from tableforge.learning import encoding_v2 as v2  # noqa: E402
from tableforge.learning.nfsp import NfspAgent, NfspConfig  # noqa: E402
from tableforge.learning.opponents import (  # noqa: E402
    OpponentPool,
    PoolMember,
    model_member,
    rule_members,
)
from tableforge.learning.pool_benchmark import (  # noqa: E402
    library_pool,
    paired_difference,
    pool_benchmark,
    rotating_field,
    same_tournament,
)
from tableforge.learning.tournament_training import tournament_self_play_train  # noqa: E402
from tableforge.models import ModelLibrary  # noqa: E402
from tableforge.poker.tournament import BountyConfig, preset_config  # noqa: E402
from tableforge.poker.tournament_benchmark import collect_samples  # noqa: E402
from tableforge.worker.tournament import field_factory, rule_bot  # noqa: E402


def tiny_agent(seed: int = 1) -> NfspAgent:
    return NfspAgent(
        NfspConfig(hidden_size=16, batch_size=16, seed=seed, observation_schema=v2.SCHEMA)
    )


def pko(stack: float = 10) -> object:
    return preset_config(stack, bounty=BountyConfig())


def test_pfsp_prefers_opponents_the_learner_loses_to() -> None:
    strong, weak = rule_members(["tight", "random"])
    pool = OpponentPool([strong, weak], weighting="hard")
    for _ in range(40):
        pool.record(strong, -1.0, 1.0)
        pool.record(weak, 1.0, -1.0)

    draws = [pool.draw(random.Random(seed)).name for seed in range(400)]

    assert draws.count("rule:tight") > 300
    assert pool.weight(weak) == pytest.approx(pool.floor)
    uniform = OpponentPool([strong, weak])
    assert uniform.weight(strong) == uniform.weight(weak)
    with pytest.raises(ValueError):
        OpponentPool([], weighting="hard")
    with pytest.raises(ValueError):
        OpponentPool([strong], weighting="nonsense")


def test_history_keeps_only_the_newest_snapshots() -> None:
    pool = OpponentPool(rule_members(["call"]), history_limit=2)
    for index in range(4):
        pool.add_history(PoolMember(f"history:{index}", lambda seed: rule_bot("call", seed)))

    assert [member.name for member in pool.all_members()] == [
        "rule:call",
        "history:2",
        "history:3",
    ]


def test_training_against_a_pool_records_results_and_snapshots() -> None:
    learner = tiny_agent()
    frozen = model_member("model:other", tiny_agent(2).to_bundle())
    pool = OpponentPool(rule_members(["tight"]) + [frozen], weighting="hard", history_limit=2)

    report = tournament_self_play_train(
        learner, pko(), 12, 0, self_play_share=0.3, opponents=pool, history_every=4
    )

    assert report.tournaments == 12
    names = [item["name"] for item in pool.summary()]
    assert names[:2] == ["rule:tight", "model:other"] and names[2:] == ["history:8", "history:12"]
    assert sum(item["games"] for item in pool.summary()) > 0


def test_pool_benchmark_and_leave_one_out(tmp_path: object) -> None:
    from pathlib import Path

    library = ModelLibrary(Path(str(tmp_path)))
    config = pko()
    for index, model_id in enumerate(("pko-10bb-a", "pko-10bb-b")):
        library.add(model_id, tiny_agent(index).to_bundle(), "nfsp", "6max-pko-10bb")
    library.add("pko-25bb-c", tiny_agent(3).to_bundle(), "nfsp", "6max-pko-25bb")

    members = library_pool(library, config, exclude=("pko-10bb-a",))
    result = pool_benchmark(lambda: rule_bot("tight", 0), members, config, 3, 0, matchup_seeds=2)

    assert [member.name for member in members] == ["model:pko-10bb-b"]
    assert result["members"] == ["model:pko-10bb-b"]
    assert result["overall"]["tournaments"] == 18  # type: ignore[index]
    assert set(result["matchups"]) == {"model:pko-10bb-b"}  # type: ignore[arg-type]
    assert same_tournament(pko(), preset_config(10, bounty=BountyConfig()))
    assert not same_tournament(pko(), pko(25))


def test_rotating_field_and_paired_difference() -> None:
    members = rule_members(["tight", "call", "aggro"])
    field = rotating_field(members)
    seats = [type(field(player, 5 * 16 + player)).__name__ for player in range(3)]
    assert seats == ["AggroBot", "TightBot", "CallBot"]

    config = pko()
    first = collect_samples(lambda: rule_bot("tight", 0), field_factory("mixed"), config, 4)
    again = collect_samples(lambda: rule_bot("tight", 0), field_factory("mixed"), config, 4)
    assert paired_difference(first, again).mean == 0
