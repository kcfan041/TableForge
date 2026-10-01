"""Playing against models: a tournament where one seat is a person.

A `GameSession` drives an ordinary `Tournament` hand by hand. Seats played by models or
rule bots act on their own; when it is the person's turn the session stops and waits for
`act`. The person only sees what their seat may see (other hole cards stay hidden until a
showdown), and a hint (the model's explanation for the person's own spot) is only given
when the session was created with hints on.

Where the models run is not decided here: a session receives `DecisionExplainer`s from a
`SeatFactory`. The cluster host builds them from its model library (remote play), a
person's own computer builds them from downloaded models (local play), and a dedicated
inference node can later provide the same interface.

A finished game becomes a replay document (the same format as replay jobs, every hole
card shown) and a plain-text hand history.

**Practice mode** (H2): a coach model looks at every decision of the person, after the
person has acted, and records what it would have done (its probabilities and Q values).
The person sees this feedback right after each decision; `report()` groups all decisions by
position, stack depth, situation and street and lists the costliest ones. The coach's
numbers are a model's estimates, not the truth: "Q loss" is how much less the chosen action
is worth than the coach's best one, in the coach's own estimate.

Games can be saved (`PlayService(store=...)`): a game is stored as its settings plus the
person's actions, and restored by replaying them (every seat is deterministic for a given
seed), so games survive a restart of the service.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tableforge.learning.explain import DecisionExplainer, DecisionExplanation, canonical_action
from tableforge.learning.replay import (
    action_entry,
    hand_document,
    replay_document,
    rule_explainer,
)
from tableforge.poker.betting import (
    Action,
    ActionType,
    IllegalActionError,
    LegalActions,
    Street,
)
from tableforge.poker.table import TableHand, TableObservation
from tableforge.poker.tournament import (
    StartedHand,
    Tournament,
    TournamentConfig,
    TournamentHandInfo,
)

SESSION_SCHEMA = "tableforge.play-session.v1"
HUMAN = "human"
MAX_GAME_HANDS = 1_000
MAX_BOT_ACTIONS = 2_000
RULE_BOTS = ("random", "call", "tight", "aggro", "pushfold", "hunter", "pushfold_nash")
RECORD_SCHEMA = "tableforge.play-record.v1"
REPORT_SCHEMA = "tableforge.practice-report.v1"
MISTAKE_Q_LOSS = 0.02
"""Buy-ins: a decision is flagged when it is worth at least this much less than the
coach's best action (in the coach's estimate) and the coach would rarely choose it."""
MISTAKE_PROBABILITY = 0.1
STACK_BUCKETS = ((5.0, "0-5"), (10.0, "5-10"), (20.0, "10-20"), (40.0, "20-40"))
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class PlayError(ValueError):
    """A request the player can fix (not their turn, illegal amount, unknown seat)."""


@dataclass(frozen=True, slots=True)
class SeatChoice:
    """Who sits at one seat: the person, a rule bot, or a model (with its play mode)."""

    kind: str
    name: str = ""
    mode: str = ""

    def __post_init__(self) -> None:
        if self.kind not in (HUMAN, "rule", "model"):
            raise PlayError("a seat is human, rule or model")
        if self.kind == "rule" and self.name not in RULE_BOTS:
            raise PlayError(f"unknown rule bot: {self.name}")
        if self.kind == "model" and not self.name:
            raise PlayError("a model seat needs a model id")

    @property
    def label(self) -> str:
        return "You" if self.kind == HUMAN else self.name


SeatFactory = Callable[[SeatChoice, int], DecisionExplainer]
"""Builds the explainer for a model seat (`choice`, player id); must be a fresh instance."""


def parse_seats(value: object, players: int) -> tuple[SeatChoice, ...]:
    """Seats from user input: a list of ``{"kind", "name", "mode"}`` with exactly one human."""
    if not isinstance(value, list) or len(value) != players:
        raise PlayError(f"give exactly {players} seats")
    seats = []
    for item in value:
        if not isinstance(item, dict):
            raise PlayError("each seat must be an object")
        seats.append(
            SeatChoice(
                str(item.get("kind", "")),
                str(item.get("name", ""))[:64],
                str(item.get("mode", ""))[:32],
            )
        )
    if sum(seat.kind == HUMAN for seat in seats) != 1:
        raise PlayError("exactly one seat is played by you")
    return tuple(seats)


@dataclass
class _Bot:
    """A model or rule bot seat; logs its decisions like replay seats do."""

    player: int
    explainer: DecisionExplainer
    mode: str
    explain: bool
    log: list[dict[str, object]]

    def begin_hand(self, info: TournamentHandInfo) -> None:
        self.explainer.begin_hand(info)

    def act(self, observation: TableObservation, legal: LegalActions) -> Action:
        explanation = (
            self.explainer.explain(observation, legal, self.mode).as_dict()
            if self.explain
            else None
        )
        action = self.explainer.act(observation, legal, self.mode)
        self.log.append(action_entry(self.player, observation, legal, action, explanation))
        return action


class _Human:
    """Placeholder agent for the person's seat; the session never asks it to act."""

    def begin_hand(self, info: TournamentHandInfo) -> None:
        del info

    def act(self, observation: TableObservation, legal: LegalActions) -> Action:
        raise AssertionError("the session asks the person, not this agent")


@dataclass
class GameSession:
    """One tournament between a person and models or bots. Not thread-safe by itself;
    `PlayService` serialises access."""

    session_id: str
    owner: str
    config: TournamentConfig
    seed: int
    seats: tuple[SeatChoice, ...]
    hints: bool
    tournament: Tournament
    agents: list[_Bot | _Human]
    hint_explainer: DecisionExplainer | None
    """The coach: explains the person's spots (hints before acting, practice after)."""
    hint_mode: str
    practice: bool = False
    coach: SeatChoice | None = None
    created: float = field(default_factory=time.monotonic)
    touched: float = field(default_factory=time.monotonic)
    log: list[dict[str, object]] = field(default_factory=list)
    hands: list[dict[str, object]] = field(default_factory=list)
    started: StartedHand | None = None
    last_hand: dict[str, object] | None = None
    final: dict[str, object] | None = None
    actions: list[tuple[str, int | None]] = field(default_factory=list)
    """The person's actions in order (a saved game is its settings plus these)."""
    reviews: list[dict[str, object]] = field(default_factory=list)

    @property
    def human(self) -> int:
        return next(index for index, seat in enumerate(self.seats) if seat.kind == HUMAN)

    @property
    def complete(self) -> bool:
        return self.final is not None

    # --- playing -------------------------------------------------------------------------

    def advance(self) -> None:
        """Play bot actions (and deal new hands) until the person must act or the game ends."""
        actions = 0
        while self.final is None:
            if self.started is None:
                if self.tournament.complete:
                    self._finish_game()
                    return
                if len(self.hands) >= MAX_GAME_HANDS:
                    raise RuntimeError("the game is longer than the hand limit")
                self.log.clear()
                self.started = self.tournament.begin_hand(self.agents, record=True)
                if self.hint_explainer is not None and self.human in self.started.seat_players:
                    own = self.started.seat_players.index(self.human)
                    self.hint_explainer.begin_hand(self.started.infos[own])
            hand = self.started.hand
            if hand.complete:
                self._finish_hand()
                continue
            actor = hand.betting.current_actor
            if actor is None:
                raise AssertionError("active hand must have a current actor")
            seat = actor
            player = self.started.seat_players[seat]
            if player == self.human:
                return
            observation = hand.observe(seat)
            legal = observation.legal_actions
            if legal is None:
                raise AssertionError("current actor must receive legal actions")
            hand.act(seat, self.started.agents[seat].act(observation, legal))
            actions += 1
            if actions > MAX_BOT_ACTIONS:
                raise RuntimeError("bots took too many actions")

    def act(self, kind: str, amount: int | None = None) -> None:
        """The person's action: fold, check, call, check_call, raise (to `amount`), all_in."""
        if self.final is not None:
            raise PlayError("the game is over")
        started = self.started
        if started is None or self.human not in started.seat_players:
            raise PlayError("it is not your turn")
        seat = started.seat_players.index(self.human)
        hand = started.hand
        if hand.complete or hand.betting.current_actor != seat:
            raise PlayError("it is not your turn")
        observation = hand.observe(seat)
        legal = observation.legal_actions
        assert legal is not None
        action = _human_action(kind, amount, legal)
        explanation = (
            self.hint_explainer.explain(observation, legal, self.hint_mode)
            if self.hint_explainer is not None
            else None
        )
        try:
            hand.act(seat, action)
        except IllegalActionError as error:
            raise PlayError(str(error)) from error
        self.actions.append((kind, amount))
        self.log.append(
            action_entry(
                self.human,
                observation,
                legal,
                action,
                explanation.as_dict() if explanation is not None else None,
            )
        )
        if self.practice and explanation is not None:
            self.reviews.append(
                review(
                    started,
                    seat,
                    self.tournament.hand_number + 1,
                    observation,
                    legal,
                    action,
                    explanation,
                )
            )
        self.advance()

    def _finish_hand(self) -> None:
        started = self.started
        assert started is not None and started.hand.result is not None
        result = started.hand.result
        record = self.tournament.finish_hand(started, result)
        assert record is not None
        document = hand_document(record, started.hand, result, self.log)
        self.hands.append(document)
        shown = {
            seat
            for seat, state in enumerate(started.hand.betting.seats)
            if started.seat_players[seat] == self.human
            or (not result.ended_by_fold and not state.folded)
        }
        all_cards = [[str(card) for card in cards] for cards in started.hand.hole_cards]
        self.last_hand = {
            **document,
            "hole_cards": [cards if seat in shown else [] for seat, cards in enumerate(all_cards)],
            "actions": _public_actions(self.log),
            "icm_before": [],
            "icm_after": [],
        }
        self.started = None

    def _finish_game(self) -> None:
        final = self.tournament.finish()
        self.final = {
            "places": list(final.places),
            "prizes": [round(value, 6) for value in final.prizes],
            "bounties": [round(value, 6) for value in final.bounties],
            "results": [round(value, 6) for value in final.results],
        }

    # --- what the person sees -------------------------------------------------------------

    def view(self) -> dict[str, object]:
        """The table as the person sees it; other players' cards only after a showdown."""
        tournament = self.tournament
        config = self.config
        view: dict[str, object] = {
            "schema_version": SESSION_SCHEMA,
            "session_id": self.session_id,
            "players": config.players,
            "human": self.human,
            "labels": [seat.label for seat in self.seats],
            "seats": [
                {"kind": seat.kind, "name": seat.name, "mode": seat.mode} for seat in self.seats
            ],
            "hints": self.hints,
            "practice": self.practice,
            "coach": self.coach.name if self.coach is not None else None,
            "feedback": self.reviews[-1] if self.practice and self.reviews else None,
            "reviews": len(self.reviews),
            "config": json.loads(config.to_json()),
            "hands_played": len(self.hands),
            "stacks": list(tournament.stacks),
            "bounties": [round(value, 6) for value in tournament.heads]
            if config.bounty is not None
            else [],
            "bounty_cash": [round(value, 6) for value in tournament.bounty_cash],
            "places": list(tournament.places),
            "complete": self.final is not None,
            "result": self.final,
            "last_hand": self.last_hand,
            "hand": None,
            "turn": None,
        }
        started = self.started
        if started is None:
            return view
        hand = started.hand
        human_seat = (
            started.seat_players.index(self.human) if self.human in started.seat_players else None
        )
        view["hand"] = self._hand_view(started, human_seat)
        if human_seat is not None and hand.betting.current_actor == human_seat:
            observation = hand.observe(human_seat)
            legal = observation.legal_actions
            assert legal is not None
            view["turn"] = _turn_view(observation, legal)
            # Practice mode alone gives feedback only after the person has acted.
            if self.hints and self.hint_explainer is not None:
                view["hint"] = self.hint_explainer.explain(
                    observation, legal, self.hint_mode
                ).as_dict()
        return view

    def _hand_view(self, started: StartedHand, human_seat: int | None) -> dict[str, object]:
        hand: TableHand = started.hand
        betting = hand.betting
        info = started.infos[0]
        return {
            "hand": self.tournament.hand_number + 1,
            "level": started.level,
            "small_blind": info.small_blind,
            "big_blind": info.big_blind,
            "ante": info.ante,
            "button": started.seat_players[hand.button],
            "players": list(started.seat_players),
            "street": betting.street.name.lower(),
            "board": [str(card) for card in hand.board],
            "pot": betting.pot,
            "current_bet": betting.current_bet,
            "current_player": started.seat_players[betting.current_actor]
            if betting.current_actor is not None
            else None,
            "stacks": [state.stack for state in betting.seats],
            "street_bets": [state.street_contribution for state in betting.seats],
            "folded": [state.folded for state in betting.seats],
            "all_in": [state.all_in for state in betting.seats],
            "hole_cards": [str(card) for card in hand.hole_cards[human_seat]]
            if human_seat is not None
            else [],
            "actions": _public_actions(self.log),
        }

    # --- after the game -------------------------------------------------------------------

    def replay(self) -> dict[str, object]:
        """The finished game as a replay document (every hole card shown)."""
        if self.final is None:
            raise PlayError("the game is not over yet")
        return replay_document(
            self.config,
            self.seed,
            [seat.label for seat in self.seats],
            self.hands,
            self.tournament.result(),
        )

    def hand_history(self) -> str:
        """A plain-text hand history of every finished hand."""
        return hand_history_text(self.config, [seat.label for seat in self.seats], self.hands)

    def report(self) -> dict[str, object]:
        """Practice report: the person's decisions grouped, with the costliest ones."""
        if not self.practice:
            raise PlayError("this game was not played in practice mode")
        return practice_report(self.reviews, self.coach.label if self.coach else "")

    def record(self) -> dict[str, object]:
        """Everything needed to restore this game (see `restore_session`)."""
        return {
            "schema_version": RECORD_SCHEMA,
            "session_id": self.session_id,
            "owner": self.owner,
            "config_json": self.config.to_json(),
            "seed": self.seed,
            "seats": [_seat_dict(seat) for seat in self.seats],
            "hints": self.hints,
            "practice": self.practice,
            "coach": _seat_dict(self.coach) if self.coach is not None else None,
            "actions": [[kind, amount] for kind, amount in self.actions],
            "summary": self.summary(),
            "updated": time.time(),
        }

    def summary(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "labels": [seat.label for seat in self.seats],
            "hands_played": len(self.hands),
            "complete": self.complete,
            "practice": self.practice,
        }


def _seat_dict(seat: SeatChoice) -> dict[str, str]:
    return {"kind": seat.kind, "name": seat.name, "mode": seat.mode}


# --- practice ----------------------------------------------------------------------------

_EARLY = {
    1: ("UTG",),
    2: ("UTG", "CO"),
    3: ("UTG", "HJ", "CO"),
    4: ("UTG", "LJ", "HJ", "CO"),
    5: ("UTG", "UTG1", "LJ", "HJ", "CO"),
    6: ("UTG", "UTG1", "UTG2", "LJ", "HJ", "CO"),
}
"""Names of the seats between the big blind and the button, by how many there are
(6-max: UTG, HJ, CO, as in strategy tables)."""


def position_name(players: int, relative: int) -> str:
    """Position of the seat `relative` places after the button (0 = button)."""
    if players == 2:
        return "SB" if relative == 0 else "BB"
    if relative == 0:
        return "BTN"
    if relative == 1:
        return "SB"
    if relative == 2:
        return "BB"
    return _EARLY[players - 3][relative - 3]


def situation(observation: TableObservation) -> str:
    """What the decision faces: ``unopened``, ``limped``, ``facing_raise``,
    ``facing_reraise``, ``facing_all_in`` (preflop) or ``postflop``."""
    if observation.street is not Street.PREFLOP:
        return "postflop"
    history = observation.action_history
    raises = [item for item in history if item.action_type is ActionType.RAISE_TO]
    if not raises:
        limped = any(item.action_type is ActionType.CALL for item in history)
        return "limped" if limped else "unopened"
    if observation.players[raises[-1].actor].all_in:
        return "facing_all_in"
    return "facing_raise" if len(raises) == 1 else "facing_reraise"


def stack_bucket(stack_bb: float) -> str:
    for limit, name in STACK_BUCKETS:
        if stack_bb <= limit:
            return name
    return "40+"


def _value_key(action: Action, legal: LegalActions, observation: TableObservation) -> str:
    """The coach's value entry for a concrete action (a raise: the nearest raise size)."""
    name = canonical_action(action, legal)
    if name != "raise" or action.amount is None:
        return name
    me = observation.players[observation.observer]
    to_call = max(0, observation.current_bet - me.street_contribution)
    pot_after_call = observation.pot + to_call
    low = legal.min_raise_to or 0
    high = legal.max_raise_to or 0
    sizes = {
        "raise_half_pot": min(max(observation.current_bet + pot_after_call // 2, low), high),
        "raise_pot": min(max(observation.current_bet + pot_after_call, low), high),
    }
    amount = int(action.amount)
    return min(sizes, key=lambda key: abs(sizes[key] - amount))


def review(
    started: StartedHand,
    seat: int,
    hand_number: int,
    observation: TableObservation,
    legal: LegalActions,
    action: Action,
    explanation: DecisionExplanation,
) -> dict[str, object]:
    """The coach's view of one of the person's decisions."""
    info = started.infos[seat]
    players = len(started.seat_players)
    return review_decision(
        players,
        (seat - started.hand.button) % players,
        started.stacks_before[seat] / info.big_blind,
        hand_number,
        observation,
        legal,
        action,
        explanation,
    )


def review_decision(
    players: int,
    relative: int,
    stack_bb: float,
    hand_number: int,
    observation: TableObservation,
    legal: LegalActions,
    action: Action,
    explanation: DecisionExplanation,
) -> dict[str, object]:
    """A decision reviewed by a coach; `relative` is the seat's distance from the button."""
    me = observation.players[observation.observer]
    chosen = canonical_action(action, legal)
    probabilities = {key: round(value, 4) for key, value in explanation.probabilities.items()}
    recommended = max(probabilities, key=lambda key: probabilities[key])
    values = dict(explanation.details[0].per_action) if explanation.details else {}
    unit = explanation.details[0].unit if explanation.details else ""
    key = _value_key(action, legal, observation)
    q_chosen = values.get(key)
    best_key = max(values, key=lambda item: values[item]) if values else None
    q_loss = round(max(values.values()) - q_chosen, 4) if values and q_chosen is not None else None
    chosen_probability = probabilities.get(chosen, 0.0)
    return {
        "hand": hand_number,
        "street": observation.street.name.lower(),
        "position": position_name(players, relative),
        "situation": situation(observation),
        "stack_bb": round(stack_bb, 1),
        "stack": stack_bucket(stack_bb),
        "hole_cards": [str(card) for card in observation.hole_cards],
        "board": [str(card) for card in observation.board],
        "pot": observation.pot,
        "to_call": max(0, observation.current_bet - me.street_contribution),
        "chosen": chosen,
        "chosen_key": key,
        "amount": action.amount,
        "recommended": recommended,
        "probabilities": probabilities,
        "chosen_probability": chosen_probability,
        "values": {name: round(value, 4) for name, value in values.items()},
        "value_unit": unit,
        "best_value_key": best_key,
        "q_loss": q_loss,
        "mistake": q_loss is not None
        and q_loss > MISTAKE_Q_LOSS
        and chosen_probability < MISTAKE_PROBABILITY,
    }


def _summarize(items: Sequence[dict[str, object]]) -> dict[str, object]:
    losses = [float(str(item["q_loss"])) for item in items if item["q_loss"] is not None]
    count = len(items)
    return {
        "decisions": count,
        "agree": round(sum(item["chosen"] == item["recommended"] for item in items) / count, 4)
        if count
        else None,
        "chosen_probability": round(
            sum(float(str(item["chosen_probability"])) for item in items) / count, 4
        )
        if count
        else None,
        "q_loss_total": round(sum(losses), 4),
        "q_loss_mean": round(sum(losses) / len(losses), 4) if losses else None,
        "mistakes": sum(bool(item["mistake"]) for item in items),
    }


def practice_report(reviews: Sequence[dict[str, object]], coach: str) -> dict[str, object]:
    """The person's decisions grouped by position, stack depth, situation and street."""
    groups: dict[str, dict[str, list[dict[str, object]]]] = {
        "position": {},
        "stack": {},
        "situation": {},
        "street": {},
    }
    for item in reviews:
        for dimension, buckets in groups.items():
            buckets.setdefault(str(item[dimension]), []).append(item)
    # Only decisions the coach's policy would not usually make: its policy and its Q values
    # can disagree (e.g. it mostly folds a spot its Q network values higher for calling),
    # and listing those as the person's worst decisions would be misleading.
    worst = sorted(
        (
            item
            for item in reviews
            if item["q_loss"] is not None and item["chosen"] != item["recommended"]
        ),
        key=lambda item: -float(str(item["q_loss"])),
    )[:10]
    return {
        "schema_version": REPORT_SCHEMA,
        "coach": coach,
        "unit": next((str(item["value_unit"]) for item in reviews if item["value_unit"]), ""),
        "overall": _summarize(reviews),
        "by": {
            dimension: {name: _summarize(items) for name, items in buckets.items()}
            for dimension, buckets in groups.items()
        },
        "worst": worst,
        "thresholds": {"q_loss": MISTAKE_Q_LOSS, "probability": MISTAKE_PROBABILITY},
    }


def _public_actions(log: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    """Actions without the models' explanations (they would reveal hand strength)."""
    return [{key: value for key, value in entry.items() if key != "explanation"} for entry in log]


def _human_action(kind: str, amount: int | None, legal: LegalActions) -> Action:
    if kind == "fold":
        if not legal.can_fold:
            raise PlayError("you can check; folding is not needed")
        return Action.fold()
    if kind in ("check", "call", "check_call"):
        return Action.check() if legal.can_check else Action.call()
    if legal.min_raise_to is None or legal.max_raise_to is None:
        raise PlayError("you cannot raise here")
    if kind == "all_in":
        return Action.raise_to(legal.max_raise_to)
    if kind == "raise":
        if amount is None:
            raise PlayError("give the amount to raise to")
        if not legal.min_raise_to <= amount <= legal.max_raise_to:
            raise PlayError(f"raise to between {legal.min_raise_to} and {legal.max_raise_to} chips")
        return Action.raise_to(int(amount))
    raise PlayError(f"unknown action: {kind}")


def _turn_view(observation: TableObservation, legal: LegalActions) -> dict[str, object]:
    me = observation.players[observation.observer]
    to_call = max(0, observation.current_bet - me.street_contribution)
    sizes: dict[str, int] = {}
    if legal.min_raise_to is not None and legal.max_raise_to is not None:
        pot_after_call = observation.pot + to_call
        for name, fraction in (("half_pot", 0.5), ("pot", 1.0)):
            target = observation.current_bet + int(pot_after_call * fraction)
            sizes[name] = min(max(target, legal.min_raise_to), legal.max_raise_to)
        sizes["min"] = legal.min_raise_to
    return {
        "can_fold": legal.can_fold,
        "can_check": legal.can_check,
        "call_amount": legal.call_amount,
        "min_raise_to": legal.min_raise_to,
        "max_raise_to": legal.max_raise_to,
        "all_in_only": legal.all_in_only,
        "to_call": to_call,
        "raise_sizes": sizes,
    }


def hand_history_text(
    config: TournamentConfig, labels: Sequence[str], hands: Sequence[dict[str, object]]
) -> str:
    lines = [f"TableForge tournament: {config.name or 'custom'} ({config.players} players)"]
    for hand in hands:
        players = [int(player) for player in hand["players"]]  # type: ignore[attr-defined]
        lines.append("")
        lines.append(
            f"Hand #{hand['hand']}  level {hand['level']}  blinds "
            f"{hand['small_blind']}/{hand['big_blind']}  ante {hand['ante']}"
        )
        lines.append(f"Button: {labels[int(hand['button'])]}")  # type: ignore[call-overload]
        stacks = hand["stacks_before"]
        cards = hand["hole_cards"]
        for seat, player in enumerate(players):
            lines.append(
                f"  {labels[player]}: {stacks[seat]} chips, "  # type: ignore[index]
                f"[{' '.join(cards[seat])}]"  # type: ignore[index]
            )
        street = ""
        for entry in hand["actions"]:  # type: ignore[attr-defined]
            if entry["street"] != street:
                street = str(entry["street"])
                lines.append(f"  *** {street.upper()} ***")
            amount = f" to {entry['amount']}" if entry["amount"] is not None else ""
            lines.append(f"  {labels[int(entry['player'])]}: {entry['action']}{amount}")
        board = " ".join(hand["board"])  # type: ignore[arg-type]
        if board:
            lines.append(f"  Board: [{board}]")
        winners = ", ".join(labels[int(player)] for player in hand["winners"])  # type: ignore[attr-defined]
        lines.append(f"  Winner: {winners}")
        after = hand["stacks_after"]
        lines.append(
            "  Stacks: "
            + ", ".join(
                f"{labels[player]} {after[seat]}"  # type: ignore[index]
                for seat, player in enumerate(players)
            )
        )
    return "\n".join(lines) + "\n"


def create_session(
    owner: str,
    config: TournamentConfig,
    seats: Sequence[SeatChoice],
    seed: int,
    factory: SeatFactory,
    hints: bool = False,
    hint_model: SeatChoice | None = None,
    practice: bool = False,
    session_id: str | None = None,
) -> GameSession:
    """Seat everyone and play until the person's first decision.

    With `hints` or `practice` on, `hint_model` (a model seat choice, the coach) explains the
    person's own spots; by default the first model at the table does.
    """
    if len(seats) != config.players:
        raise PlayError(f"give exactly {config.players} seats")
    if sum(seat.kind == HUMAN for seat in seats) != 1:
        raise PlayError("exactly one seat is played by you")
    if not 0 <= seed < 2**31:
        raise PlayError("seed must be between 0 and 2^31")
    log: list[dict[str, object]] = []
    agents: list[_Bot | _Human] = []
    for player, seat in enumerate(seats):
        if seat.kind == HUMAN:
            agents.append(_Human())
        elif seat.kind == "rule":
            agents.append(
                _Bot(player, rule_explainer(seat.name, seed * 16 + player), "rule", False, log)
            )
        else:
            explainer = factory(seat, player)
            mode = seat.mode or explainer.play_modes[0].key
            if mode not in {item.key for item in explainer.play_modes}:
                raise PlayError(f"unknown play mode {mode} for {seat.name}")
            agents.append(_Bot(player, explainer, mode, True, log))
    hint_explainer: DecisionExplainer | None = None
    hint_mode = ""
    coach: SeatChoice | None = None
    if hints or practice:
        choice = hint_model or next((seat for seat in seats if seat.kind == "model"), None)
        if choice is None or choice.kind != "model":
            raise PlayError("hints and practice need a model; seat one or choose the coach")
        hint_explainer = factory(choice, -1)
        hint_mode = choice.mode or hint_explainer.play_modes[0].key
        coach = SeatChoice("model", choice.name, hint_mode)
    session = GameSession(
        session_id=session_id or secrets.token_urlsafe(12),
        owner=owner,
        config=config,
        seed=seed,
        seats=tuple(seats),
        hints=hints,
        tournament=Tournament(config, seed),
        agents=agents,
        hint_explainer=hint_explainer,
        hint_mode=hint_mode,
        practice=practice,
        coach=coach,
        log=log,
    )
    session.advance()
    return session


def restore_session(record: dict[str, object], factory: SeatFactory) -> GameSession:
    """Rebuild a saved game by replaying the person's actions (every seat is
    deterministic for a given seed, so the game ends up exactly where it was)."""
    if record.get("schema_version") != RECORD_SCHEMA:
        raise ValueError("unsupported saved game")
    seats = parse_seats(
        record["seats"], TournamentConfig.from_json(str(record["config_json"])).players
    )
    coach_data = record.get("coach")
    coach = None
    if isinstance(coach_data, dict):
        coach = SeatChoice(
            str(coach_data["kind"]), str(coach_data["name"]), str(coach_data["mode"])
        )
    session = create_session(
        str(record["owner"]),
        TournamentConfig.from_json(str(record["config_json"])),
        seats,
        int(str(record["seed"])),
        factory,
        hints=bool(record.get("hints")),
        hint_model=coach,
        practice=bool(record.get("practice")),
        session_id=str(record["session_id"]),
    )
    actions = record.get("actions")
    if not isinstance(actions, list):
        raise ValueError("saved game has no actions")
    for kind, amount in actions:
        session.act(str(kind), None if amount is None else int(amount))
    return session


class GameStore:
    """Saved games of one host: one JSON file per game (settings plus the person's
    actions), written atomically after every action."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, session_id: str) -> Path:
        if not _SESSION_ID.match(session_id):
            raise KeyError(session_id)
        return self.root / f"{session_id}.json"

    def save(self, session: GameSession) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(session.session_id)
        staging = path.with_suffix(".tmp")
        staging.write_text(json.dumps(session.record()), encoding="utf-8")
        os.replace(staging, path)

    def load(self, session_id: str) -> dict[str, object] | None:
        path = self._path(session_id)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None

    def delete(self, session_id: str) -> None:
        self._path(session_id).unlink(missing_ok=True)

    def records(self) -> list[dict[str, object]]:
        if not self.root.is_dir():
            return []
        found = []
        for path in self.root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("schema_version") == RECORD_SCHEMA:
                found.append(data)
        return found


class PlayService:
    """Keeps the game sessions of one host.

    Sessions belong to whoever created them (another caller cannot see or play them). Games
    in memory are dropped after `idle_seconds` without activity; with a `store`, every game
    is also saved after each action and restored on demand (after a restart, or after it
    was dropped from memory), and each owner keeps at most `saved_per_owner` saved games.
    Without a store, each owner may keep at most `per_owner` games in memory.
    """

    def __init__(
        self,
        factory: SeatFactory,
        idle_seconds: float = 2 * 3600.0,
        per_owner: int = 3,
        clock: Callable[[], float] = time.monotonic,
        store: GameStore | None = None,
        saved_per_owner: int = 20,
    ) -> None:
        self.factory = factory
        self.idle_seconds = idle_seconds
        self.per_owner = per_owner
        self.clock = clock
        self.store = store
        self.saved_per_owner = saved_per_owner
        self._sessions: dict[str, GameSession] = {}
        self._lock = threading.Lock()

    def start(
        self,
        owner: str,
        config: TournamentConfig,
        seats: Sequence[SeatChoice],
        seed: int,
        hints: bool = False,
        hint_model: SeatChoice | None = None,
        practice: bool = False,
    ) -> dict[str, object]:
        session = create_session(
            owner, config, seats, seed, self.factory, hints, hint_model, practice
        )
        now = self.clock()
        session.created = session.touched = now
        with self._lock:
            self._expire(now)
            mine = sorted(
                (item for item in self._sessions.values() if item.owner == owner),
                key=lambda item: item.touched,
            )
            for old in mine[: max(0, len(mine) - self.per_owner + 1)]:
                del self._sessions[old.session_id]
            self._sessions[session.session_id] = session
            self._save(session)
            self._trim_saved(owner)
            return session.view()

    def view(self, owner: str, session_id: str) -> dict[str, object]:
        with self._lock:
            return self._get(owner, session_id).view()

    def act(
        self, owner: str, session_id: str, kind: str, amount: int | None = None
    ) -> dict[str, object]:
        with self._lock:
            session = self._get(owner, session_id)
            session.act(kind, amount)
            self._save(session)
            return session.view()

    def replay(self, owner: str, session_id: str) -> dict[str, object]:
        with self._lock:
            return self._get(owner, session_id).replay()

    def hand_history(self, owner: str, session_id: str) -> str:
        with self._lock:
            return self._get(owner, session_id).hand_history()

    def report(self, owner: str, session_id: str) -> dict[str, object]:
        with self._lock:
            return self._get(owner, session_id).report()

    def close(self, owner: str, session_id: str) -> None:
        with self._lock:
            self._get(owner, session_id)
            self._sessions.pop(session_id, None)
            if self.store is not None:
                self.store.delete(session_id)

    def sessions(self, owner: str) -> list[dict[str, object]]:
        with self._lock:
            self._expire(self.clock())
            found = {
                item.session_id: item.summary()
                for item in self._sessions.values()
                if item.owner == owner
            }
            for record in self._saved(owner):
                summary = record.get("summary")
                if isinstance(summary, dict):
                    found.setdefault(str(record["session_id"]), summary)
            return list(found.values())

    def _save(self, session: GameSession) -> None:
        if self.store is not None:
            self.store.save(session)

    def _saved(self, owner: str) -> list[dict[str, object]]:
        if self.store is None:
            return []
        mine = [
            record
            for record in self.store.records()
            if secrets.compare_digest(str(record.get("owner", "")), owner)
        ]
        return sorted(mine, key=lambda record: -float(str(record.get("updated", 0))))

    def _trim_saved(self, owner: str) -> None:
        if self.store is None:
            return
        for record in self._saved(owner)[self.saved_per_owner :]:
            self.store.delete(str(record["session_id"]))

    def _get(self, owner: str, session_id: str) -> GameSession:
        now = self.clock()
        self._expire(now)
        session = self._sessions.get(session_id)
        if session is None and self.store is not None:
            try:
                record = self.store.load(session_id)
            except KeyError:
                record = None
            if record is not None and secrets.compare_digest(str(record.get("owner", "")), owner):
                session = restore_session(record, self.factory)
                self._sessions[session_id] = session
        if session is None or not secrets.compare_digest(session.owner, owner):
            raise KeyError(session_id)
        session.touched = now
        return session

    def _expire(self, now: float) -> None:
        for key in [
            key for key, item in self._sessions.items() if now - item.touched > self.idle_seconds
        ]:
            del self._sessions[key]
