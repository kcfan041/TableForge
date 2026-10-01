from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from tableforge.learning.algorithms import (
    ALGORITHMS,
    NFSP,
    AlgorithmSpec,
    UnknownAlgorithmError,
    algorithm,
    all_executors,
)
from tableforge.learning.explain import PlayMode, RuleBotExplainer
from tableforge.poker.table import TableAgent
from tableforge.worker.tournament import rule_bot


def test_nfsp_is_registered_with_its_play_modes() -> None:
    assert algorithm("nfsp") is NFSP
    assert [mode.key for mode in NFSP.play_modes] == ["average", "best_response"]
    assert NFSP.mode() == "average" and NFSP.mode("best_response") == "best_response"
    with pytest.raises(ValueError, match="unknown play mode"):
        NFSP.mode("aggressive")
    with pytest.raises(UnknownAlgorithmError):
        algorithm("deep-cfr")


def test_nfsp_explainer_keeps_its_old_names() -> None:
    pytest.importorskip("torch")
    from tableforge.learning.nfsp_explain import AVERAGE, BEST_RESPONSE

    assert NFSP.play_modes == (AVERAGE, BEST_RESPONSE)


@pytest.fixture
def fake() -> Iterator[AlgorithmSpec]:
    """A second algorithm whose "checkpoint" is the name of a rule bot."""

    def player(checkpoint: bytes, mode: str) -> Callable[[], TableAgent]:
        del mode
        return lambda: rule_bot(checkpoint.decode(), 1)

    spec = AlgorithmSpec(
        key="fake",
        label="Fake",
        checkpoint_schema="tableforge.fake-checkpoint.v1",
        play_modes=(PlayMode("default", "Default"),),
        explainer=lambda checkpoint: RuleBotExplainer(rule_bot(checkpoint.decode(), 1)),
        player=player,
        executors=lambda fetch: {"tableforge.v1.FakeJobPayload": object()},
    )
    ALGORITHMS[spec.key] = spec
    try:
        yield spec
    finally:
        del ALGORITHMS[spec.key]


def test_a_new_algorithm_plugs_in_without_core_changes(fake: AlgorithmSpec, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from tableforge.coordinator.models import play_modes
    from tableforge.coordinator.play import ModelSeats
    from tableforge.learning.opponents import model_member
    from tableforge.learning.play import SeatChoice
    from tableforge.models import ModelLibrary

    library = ModelLibrary(tmp_path)
    library.add("tight-fake", b"tight", "fake", "6max-pko-10bb")

    explainer = ModelSeats(lambda: library)(SeatChoice("model", "tight-fake"), 0)
    member = model_member("model:tight-fake", b"tight", algorithm="fake", mode="default")

    assert play_modes("fake") == [{"key": "default", "label": "Default", "description": ""}]
    assert explainer.play_modes[0].key == "rule"
    assert member.build(3).__class__.__name__ == "TightBot"
    assert "tableforge.v1.FakeJobPayload" in all_executors(lambda artifact: b"")


def test_two_algorithms_cannot_claim_the_same_job(fake: AlgorithmSpec) -> None:
    clash = AlgorithmSpec(
        key="clash",
        label="Clash",
        checkpoint_schema="x",
        play_modes=(PlayMode("default", "Default"),),
        explainer=fake.explainer,
        player=fake.player,
        executors=lambda fetch: {"tableforge.v1.FakeJobPayload": object()},
    )
    ALGORITHMS["clash"] = clash
    try:
        with pytest.raises(AssertionError, match="registered twice"):
            all_executors(lambda artifact: b"")
    finally:
        del ALGORITHMS["clash"]


def test_pool_and_comparison_payloads_only_name_non_nfsp_algorithms() -> None:
    from tableforge.domain.opponent_pool import OpponentPoolSpec, PoolModel
    from tableforge.learning.comparison import ComparisonPayload, Competitor
    from tableforge.poker.tournament import preset_config

    sha = "a" * 64
    nfsp = OpponentPoolSpec((), (PoolModel("model:a", "x", sha),)).to_bytes()
    other = OpponentPoolSpec((), (PoolModel("model:a", "x", sha, algorithm="ppo"),)).to_bytes()
    config = preset_config(10).to_json()
    comparison = ComparisonPayload(
        "1.0", config, (Competitor("model:a", "x", sha), Competitor("rule:tight")), 2, 0, 0
    )

    assert b"algorithm" not in nfsp and b'"algorithm":"ppo"' in other
    assert OpponentPoolSpec.from_bytes(other).models[0].algorithm == "ppo"
    assert b"algorithm" not in comparison.to_bytes()
    assert ComparisonPayload.from_bytes(comparison.to_bytes()) == comparison
