from __future__ import annotations

import pytest

from tableforge.learning.explain import DecisionExplainer, RuleBotExplainer
from tableforge.learning.play import (
    HUMAN,
    PlayError,
    PlayService,
    SeatChoice,
    create_session,
    parse_seats,
)
from tableforge.learning.replay import REPLAY_SCHEMA
from tableforge.poker.tournament import BountyConfig, preset_config
from tableforge.worker.tournament import rule_bot


def factory(choice: SeatChoice, player: int) -> DecisionExplainer:
    """Stands in for a model: a rule bot named by the model id."""
    return RuleBotExplainer(rule_bot(choice.name, 7 + player))


def seats(players: int = 3) -> tuple[SeatChoice, ...]:
    others = [SeatChoice("model", "aggro"), SeatChoice("rule", "call"), SeatChoice("rule", "tight")]
    return (SeatChoice(HUMAN), *others[: players - 1])


def play_out(session_act, view: dict[str, object]) -> dict[str, object]:  # type: ignore[no-untyped-def]
    for _ in range(3_000):
        if view["complete"]:
            return view
        turn = view["turn"]
        assert isinstance(turn, dict)
        view = session_act("check_call" if turn["can_check"] else "all_in")
    raise AssertionError("game did not end")


def test_session_waits_for_the_person_and_hides_other_cards() -> None:
    config = preset_config(10, players=3, bounty=BountyConfig())
    session = create_session("me", config, seats(), 5, factory)
    view = session.view()

    assert not view["complete"]
    turn = view["turn"]
    hand = view["hand"]
    assert isinstance(turn, dict) and isinstance(hand, dict)
    assert hand["current_player"] == view["human"] == 0
    assert len(hand["hole_cards"]) == 2
    assert "hint" not in view
    assert all("explanation" not in entry for entry in hand["actions"])
    assert sum(view["bounties"]) == pytest.approx(3 * config.starting_bounty)  # type: ignore[arg-type]


def test_a_full_game_becomes_a_replay_and_a_hand_history() -> None:
    config = preset_config(10, players=3, bounty=BountyConfig())
    session = create_session("me", config, seats(), 11, factory)

    def act(kind: str) -> dict[str, object]:
        session.act(kind)
        return session.view()

    final = play_out(act, session.view())
    replay = session.replay()
    history = session.hand_history()

    assert final["complete"] and final["turn"] is None
    assert replay["schema_version"] == REPLAY_SCHEMA
    assert replay["labels"] == ["You", "aggro", "call"]
    hands = replay["hands"]
    assert isinstance(hands, list) and len(hands) == final["hands_played"]
    assert all(len(cards) == 2 for hand in hands for cards in hand["hole_cards"])
    places = replay["result"]["places"]  # type: ignore[index]
    assert places.count(1) == 1 and all(1 <= place <= 3 for place in places)
    assert history.startswith("TableForge tournament") and "Hand #1 " in history
    with pytest.raises(PlayError, match="over"):
        session.act("fold")


def test_the_person_sees_showdown_cards_but_not_folded_ones() -> None:
    config = preset_config(10, players=3, bounty=BountyConfig())
    for seed in range(30):
        session = create_session("me", config, seats(), seed, factory)
        session.act("all_in")
        last = session.last_hand
        if last is None or not last["showdown"]:
            continue
        shown = last["hole_cards"]
        assert isinstance(shown, list) and len(shown[0]) == 2
        return
    pytest.fail("no showdown in 30 seeds")


def test_actions_are_checked() -> None:
    config = preset_config(10, players=2)
    session = create_session(
        "me", config, (SeatChoice(HUMAN), SeatChoice("rule", "call")), 3, factory
    )
    turn = session.view()["turn"]
    assert isinstance(turn, dict)

    with pytest.raises(PlayError, match="raise to between"):
        session.act("raise", 1)
    with pytest.raises(PlayError, match="unknown action"):
        session.act("jump")
    session.act("raise", turn["min_raise_to"])


def test_hints_are_off_by_default_and_explain_the_persons_spot_when_on() -> None:
    config = preset_config(10, players=3, bounty=BountyConfig())
    session = create_session("me", config, seats(), 5, factory, hints=True)
    view = session.view()

    hint = view["hint"]
    assert isinstance(hint, dict)
    assert sum(hint["probabilities"].values()) == pytest.approx(1.0)  # type: ignore[union-attr]
    no_model = (SeatChoice(HUMAN), SeatChoice("rule", "call"), SeatChoice("rule", "tight"))
    with pytest.raises(PlayError, match="need a model"):
        create_session("me", config, no_model, 5, factory, hints=True)


def test_seat_parsing() -> None:
    parsed = parse_seats([{"kind": "human"}, {"kind": "model", "name": "m1", "mode": "average"}], 2)
    assert parsed[1] == SeatChoice("model", "m1", "average")
    for bad in (
        [{"kind": "human"}],
        [{"kind": "human"}, {"kind": "human"}],
        [{"kind": "rule", "name": "call"}, {"kind": "rule", "name": "call"}],
        [{"kind": "human"}, {"kind": "rule", "name": "shark"}],
        [{"kind": "human"}, {"kind": "model"}],
    ):
        with pytest.raises(PlayError):
            parse_seats(bad, 2)


def test_service_keeps_sessions_private_and_expires_them() -> None:
    now = [0.0]
    service = PlayService(factory, idle_seconds=60, per_owner=2, clock=lambda: now[0])
    config = preset_config(10, players=3)
    first = service.start("alice", config, seats(), 1)
    session_id = str(first["session_id"])

    with pytest.raises(KeyError):
        service.view("bob", session_id)
    assert service.view("alice", session_id)["session_id"] == session_id
    service.start("alice", config, seats(), 2)
    service.start("alice", config, seats(), 3)
    assert len(service.sessions("alice")) == 2
    with pytest.raises(KeyError):
        service.view("alice", session_id)
    now[0] = 120.0
    assert service.sessions("alice") == []


class Coach:
    """A fake model with probabilities and Q values: prefers all-in, folding is worst."""

    @property
    def play_modes(self):  # type: ignore[no-untyped-def]
        from tableforge.learning.explain import PlayMode

        return (PlayMode("average", "Average"),)

    def begin_hand(self, info: object) -> None:
        del info

    def explain(self, observation, legal, mode):  # type: ignore[no-untyped-def]
        from tableforge.learning.explain import DecisionExplanation, Detail

        values = {"fold": -0.3, "check_call": 0.0, "raise_half_pot": 0.05, "raise_pot": 0.08}
        values["all_in"] = 0.1
        probabilities = {"fold": 0.0, "check_call": 0.2, "raise": 0.1, "all_in": 0.7}
        if legal.max_raise_to is None:
            probabilities = {"fold": 0.0, "check_call": 1.0, "raise": 0.0, "all_in": 0.0}
            values = {"fold": -0.3, "check_call": 0.0}
        if not legal.can_fold:
            probabilities["check_call"] += probabilities.pop("fold")
            probabilities["fold"] = 0.0
        return DecisionExplanation(
            probabilities, details=(Detail("Q", "buy-in", per_action=values),)
        )

    def act(self, observation, legal, mode):  # type: ignore[no-untyped-def]
        from tableforge.poker.betting import Action

        return (
            Action.raise_to(legal.max_raise_to)
            if legal.max_raise_to
            else (Action.check() if legal.can_check else Action.call())
        )


def coach_factory(choice: SeatChoice, player: int) -> DecisionExplainer:
    if choice.name == "coach":
        return Coach()  # type: ignore[return-value]
    return factory(choice, player)


def practice_seats() -> tuple[SeatChoice, ...]:
    return (SeatChoice(HUMAN), SeatChoice("model", "coach"), SeatChoice("rule", "tight"))


def test_practice_reviews_every_decision_after_it_is_made() -> None:
    config = preset_config(10, players=3, bounty=BountyConfig())
    session = create_session("me", config, practice_seats(), 5, coach_factory, practice=True)
    before = session.view()

    assert before["practice"] and before["feedback"] is None and "hint" not in before
    turn = before["turn"]
    assert isinstance(turn, dict)
    session.act("fold" if turn["can_fold"] else "check_call")
    feedback = session.view()["feedback"]

    assert isinstance(feedback, dict)
    assert feedback["recommended"] == "all_in" or feedback["recommended"] == "check_call"
    assert feedback["position"] in {"BTN", "SB", "BB"}
    assert feedback["situation"] in {"unopened", "limped", "facing_raise", "facing_all_in"}
    if feedback["chosen"] == "fold":
        assert feedback["q_loss"] == pytest.approx(0.4) and feedback["mistake"]

    def act(kind: str) -> dict[str, object]:
        session.act(kind)
        return session.view()

    play_out(act, session.view())
    report = session.report()
    overall = report["overall"]
    assert isinstance(overall, dict)
    assert overall["decisions"] == len(session.reviews) > 0
    assert set(report["by"]) == {"position", "stack", "situation", "street"}  # type: ignore[arg-type]
    assert report["unit"] == "buy-in" and report["coach"] == "coach"
    worst = report["worst"]
    assert isinstance(worst, list) and worst == sorted(worst, key=lambda item: -item["q_loss"])
    plain = create_session("me", config, practice_seats(), 5, coach_factory)
    with pytest.raises(PlayError, match="practice"):
        plain.report()


def test_saved_games_are_restored_exactly(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from tableforge.learning.play import GameStore

    config = preset_config(10, players=3, bounty=BountyConfig())
    store = GameStore(tmp_path / "games")
    first = PlayService(coach_factory, store=store)
    started = first.start("alice", config, practice_seats(), 9, practice=True)
    session_id = str(started["session_id"])
    for _ in range(4):
        view = first.view("alice", session_id)
        if view["complete"]:
            break
        turn = view["turn"]
        assert isinstance(turn, dict)
        first.act("alice", session_id, "check_call")
    before = first.view("alice", session_id)

    restarted = PlayService(coach_factory, store=store)
    after = restarted.view("alice", session_id)

    assert after == before
    assert restarted.report("alice", session_id) == first.report("alice", session_id)
    assert [item["session_id"] for item in restarted.sessions("alice")] == [session_id]
    with pytest.raises(KeyError):
        restarted.view("bob", session_id)
    restarted.close("alice", session_id)
    assert PlayService(coach_factory, store=store).sessions("alice") == []


def test_positions_and_situations() -> None:
    from tableforge.learning.play import position_name, stack_bucket

    assert [position_name(6, seat) for seat in range(6)] == ["BTN", "SB", "BB", "UTG", "HJ", "CO"]
    assert [position_name(9, seat) for seat in range(9)] == [
        "BTN",
        "SB",
        "BB",
        "UTG",
        "UTG1",
        "UTG2",
        "LJ",
        "HJ",
        "CO",
    ]
    assert position_name(2, 0) == "SB" and position_name(2, 1) == "BB"
    assert [stack_bucket(value) for value in (3, 7.5, 12, 30, 80)] == [
        "0-5",
        "5-10",
        "10-20",
        "20-40",
        "40+",
    ]
