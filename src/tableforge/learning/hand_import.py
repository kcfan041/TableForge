"""Imported hand histories as replays, optionally reviewed by a coach model (H1).

`import_hands` turns GG hands (`tableforge.poker.hand_history`) into a replay document,
the same format replay jobs and played games use, so the replay page shows them. With a
coach model every decision of the person who exported the hands ("hero", the player with
dealt cards) is reviewed exactly like practice mode (`learning.play.review_decision`), and
the same practice report is produced.

What the coach is told about the tournament is partly guessed, because a hand history does
not contain it: the payout and starting stack come from the coach model's own training
tournament, every player at the table counts as still in, and bounties are read from the
seat lines (as a multiple of the smallest bounty at the table) when they are present.
Hands with more players than the coach was trained for are shown but not reviewed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tableforge.learning.explain import DecisionExplainer
from tableforge.learning.play import practice_report, review_decision
from tableforge.learning.replay import REPLAY_SCHEMA
from tableforge.poker.betting import ActionType
from tableforge.poker.hand_history import (
    FORMAT,
    VERIFIED,
    ReplayedHand,
    read_hand_histories,
)
from tableforge.poker.table import TableHand
from tableforge.poker.tournament import TournamentConfig, TournamentHandInfo

IMPORT_SCHEMA = "tableforge.hand-import.v1"
MAX_TEXT = 5 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Coach:
    explainer_for: Callable[[], DecisionExplainer]
    """A fresh explainer (models keep per-hand state)."""
    mode: str
    name: str
    config: TournamentConfig
    """The coach model's training tournament (payout, starting stack)."""
    max_players: int = 6


def _bounty_value(text: str) -> float | None:
    digits = "".join(char for char in text if char.isdigit() or char == ".")
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def _bounties(hand: ReplayedHand, starting_bounty: float) -> tuple[float, ...]:
    values = [_bounty_value(text) for text in hand.parsed.bounties]
    known = [value for value in values if value]
    if not known or starting_bounty <= 0:
        return ()
    smallest = min(known)
    return tuple(starting_bounty * (value / smallest if value else 1.0) for value in values)


def _info(hand: ReplayedHand, seat: int, coach: Coach) -> TournamentHandInfo:
    parsed = hand.parsed
    players = len(parsed.names)
    config = coach.config
    # The coach's encoder measures blinds against the starting stack and the blind level; a
    # site tournament has its own starting stack and level numbers, so the hand is presented
    # as the coach's first level (blinds over starting stack as in its own tournament).
    # GG antes are posted by everyone; the coach sees their total, like its big blind ante.
    return TournamentHandInfo(
        hand_number=0,
        level=1,
        small_blind=parsed.small_blind,
        big_blind=parsed.big_blind,
        ante=parsed.ante * players,
        hands_to_next_level=10,
        hands_per_level=10,
        players_left=players,
        starting_stack=max(1, round(parsed.big_blind * config.starting_stack_bb)),
        payout=config.payout,
        seat=seat,
        player=seat,
        seat_players=tuple(range(players)),
        bounties=_bounties(hand, config.starting_bounty) if config.bounty is not None else (),
        starting_bounty=config.starting_bounty,
        prizes=tuple(config.payout_for_place(place) for place in range(1, players + 1)),
        bounty_cash_share=config.bounty.cash_share if config.bounty is not None else 0.5,
        winner_keeps_own=config.bounty.winner_keeps_own if config.bounty is not None else True,
    )


def _replay_with_review(
    hand: ReplayedHand, coach: Coach | None, labels: dict[str, int], number: int
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Play the hand again on the engine, asking the coach at every hero decision."""
    parsed = hand.parsed
    players = len(parsed.names)
    original = hand.table
    table = TableHand(
        list(original.initial_stacks),
        hand.button,
        parsed.small_blind,
        parsed.big_blind,
        seed=0,
        extra_dead_money=original.extra_dead_money,
        cards=(original.hole_cards, original.runout),
    )
    hero = hand.seat_of.get(parsed.hero) if parsed.hero is not None else None
    explainer = None
    if coach is not None and hero is not None and players <= coach.max_players:
        explainer = coach.explainer_for()
        explainer.begin_hand(_info(hand, hero, coach))
    actions: list[dict[str, object]] = []
    reviews: list[dict[str, object]] = []
    ids = [labels[name] for name in parsed.names]
    for seat, action in hand.actions:
        observation = table.observe(seat)
        legal = table.legal_actions(seat)
        me = observation.players[seat]
        explanation = None
        if explainer is not None and seat == hero:
            assert coach is not None
            explanation = explainer.explain(observation, legal, coach.mode)
            reviews.append(
                {
                    **review_decision(
                        players,
                        (seat - hand.button) % players,
                        hand.stacks_before[seat] / parsed.big_blind,
                        number,
                        observation,
                        legal,
                        action,
                        explanation,
                    ),
                    "hand_id": parsed.hand_id,
                }
            )
        name = "fold"
        if action.type in (ActionType.CHECK, ActionType.CALL):
            name = "check_call"
        elif action.type is ActionType.RAISE_TO:
            name = "all_in" if action.amount == legal.max_raise_to else "raise"
        actions.append(
            {
                "player": ids[seat],
                "street": observation.street.name.lower(),
                "pot": observation.pot,
                "to_call": max(0, observation.current_bet - me.street_contribution),
                "stack": me.stack,
                "action": name,
                "amount": action.amount if action.type is ActionType.RAISE_TO else None,
                "explanation": explanation.as_dict() if explanation is not None else None,
            }
        )
        table.act(seat, action)
    result = table.result
    assert result is not None
    if result.ended_by_fold:
        winners = [ids[seat] for seat, payout in enumerate(result.payouts) if payout > 0]
    else:
        winners = [ids[seat] for seat in result.winners]
    engine_won = {
        parsed.names[seat]: result.payouts[seat] - table.betting.seats[seat].total_contribution
        for seat in range(players)
    }
    # GG's "collected" excludes an uncalled bet returned to the player; the engine's
    # payout includes it.
    collected_matches = all(
        result.payouts[hand.seat_of[name]] - parsed.uncalled.get(name, 0) == amount
        for name, amount in parsed.collected.items()
        if name in hand.seat_of
    )
    document: dict[str, object] = {
        "hand": number,
        "hand_id": parsed.hand_id,
        "tournament": parsed.tournament_id,
        "date": parsed.date,
        "level": parsed.level,
        "small_blind": parsed.small_blind,
        "big_blind": parsed.big_blind,
        "ante": parsed.ante,
        "button": ids[hand.button],
        "players": ids,
        "stacks_before": list(hand.stacks_before),
        "stacks_after": list(result.final_stacks),
        "bounties_before": [],
        "bounties_after": [],
        "icm_before": [],
        "icm_after": [],
        "hole_cards": [
            [] if seat in hand.hidden else [str(card) for card in table.hole_cards[seat]]
            for seat in range(players)
        ],
        "board": [str(card) for card in table.board],
        "actions": actions,
        "busted": [ids[seat] for seat in range(players) if result.final_stacks[seat] == 0],
        "knockouts": [],
        "showdown": not result.ended_by_fold,
        "winners": winners,
        "collected_matches": collected_matches,
        "net": engine_won,
    }
    return document, reviews


def import_hands(text: str, coach: Coach | None = None) -> dict[str, object]:
    """Replay document (and practice report with a coach) for pasted GG hand histories."""
    if len(text.encode("utf-8")) > MAX_TEXT:
        raise ValueError("the hand history is larger than 5 MB; split it into smaller files")
    imported = read_hand_histories(text)
    labels: dict[str, int] = {}
    hero_names: set[str] = set()
    for hand in imported.hands:
        for name in hand.parsed.names:
            labels.setdefault(name, len(labels))
        if hand.parsed.hero is not None:
            hero_names.add(hand.parsed.hero)
    documents: list[dict[str, object]] = []
    reviews: list[dict[str, object]] = []
    skipped = 0
    for number, hand in enumerate(imported.hands, 1):
        document, found = _replay_with_review(hand, coach, labels, number)
        documents.append(document)
        reviews.extend(found)
        if coach is not None and len(hand.parsed.names) > coach.max_players:
            skipped += 1
    mismatched = [str(item["hand_id"]) for item in documents if not item["collected_matches"]]
    replay: dict[str, object] = {
        "schema_version": REPLAY_SCHEMA,
        "source": FORMAT,
        "config": {"players": max((len(hand.parsed.names) for hand in imported.hands), default=0)},
        "seed": None,
        "labels": list(labels),
        "hands": documents,
        "result": None,
    }
    return {
        "schema_version": IMPORT_SCHEMA,
        "format": FORMAT,
        "verified_against_real_exports": VERIFIED,
        "hands": len(documents),
        "problems": imported.problems,
        "payout_mismatches": mismatched,
        "hero": sorted(hero_names),
        "hero_ids": sorted(labels[name] for name in hero_names),
        "tournaments": sorted({hand.parsed.tournament_id for hand in imported.hands}),
        "replay": replay,
        "coach": coach.name if coach is not None else None,
        "not_reviewed": skipped,
        "report": practice_report(reviews, coach.name) if coach is not None and reviews else None,
    }


__all__ = ["Coach", "import_hands"]
