"""Reading GGPoker / Natural8 tournament hand histories (H1).

**Not yet checked against real hand histories.** The parser follows the publicly documented
GG text format (the same layout PokerStars uses), as it appears in hand history exports:

    Poker Hand #TM1234567890: Tournament #123456789, Bounty Hunters $10.80 Hold'em No Limit
    - Level7(100/200(25)) - 2024/01/15 20:31:05
    Table '23' 8-max Seat #3 is the button
    Seat 1: 4a6b8c2d (12,345 in chips, $5.40 bounty)
    Seat 3: Hero (9,800 in chips)
    4a6b8c2d: posts the ante 25
    Hero: posts small blind 100
    ...
    *** HOLE CARDS ***
    Dealt to Hero [Ah Kd]
    Dealt to 4a6b8c2d
    4a6b8c2d: raises 300 to 500
    Hero: calls 400
    *** FLOP *** [2c 7d Jh]
    ...
    *** SHOWDOWN ***
    4a6b8c2d: shows [Qs Qd]
    Hero collected 2,300 from pot
    *** SUMMARY ***

Each hand is replayed on the TableForge engine (`TableHand` with the recorded cards), so
every action is checked: a stack, blind or bet the engine does not accept is reported as a
problem with the line number, not silently skipped. Unknown cards (players who did not show)
are filled with cards nobody holds; they only stand in for hidden cards and are never shown.

Differences from GG handled here:
* antes are posted by every player (GG) instead of one big blind ante (TableForge): they
  are taken from the stacks first and added to the pot as dead money;
* a player who is all in for less than the blind posts what they have;
* "Bounty" amounts on seat lines, when present, are kept as displayed (in the tournament's
  currency, not buy-ins).
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from tableforge.poker.betting import Action, ActionType, IllegalActionError, Street
from tableforge.poker.cards import Card, full_deck
from tableforge.poker.table import TableHand

FORMAT = "ggpoker-text"
VERIFIED = False
"""False until the parser has been checked against real GG/Natural8 exports."""

_HAND = re.compile(r"^Poker Hand #(?P<id>[A-Za-z0-9-]+):\s*(?P<rest>.*)$")
_TOURNAMENT = re.compile(r"Tournament #(?P<id>\d+),?\s*(?P<name>[^-\n]*?)\s*(?:-|$)")
_LEVEL = re.compile(
    r"Level\s*(?P<level>\d+)\s*\(\s*(?P<sb>[\d,.]+)\s*/\s*(?P<bb>[\d,.]+)(?:\s*\(\s*(?P<ante>[\d,.]+)\s*\))?\s*\)"
)
_DATE = re.compile(r"(?P<date>\d{4}/\d{2}/\d{2} \d{1,2}:\d{2}:\d{2})")
_TABLE = re.compile(
    r"^Table '(?P<table>[^']*)' (?P<max>\d+)-max Seat #(?P<button>\d+) is the button"
)
_SEAT = re.compile(
    r"^Seat (?P<seat>\d+): (?P<name>.+?) \((?:\$)?(?P<chips>[\d,.]+) in chips"
    r"(?:, (?P<bounty>[^)]*?) bounty)?(?:\) is sitting out|\))?"
)
_POST = re.compile(
    r"^(?P<name>.+?): posts (?P<kind>the ante|small blind|big blind|straddle) (?P<amount>[\d,.]+)"
    r"(?P<all_in> and is all-in)?$"
)
_DEALT = re.compile(r"^Dealt to (?P<name>.+?)(?: \[(?P<cards>[^\]]+)\])?$")
_ACTION = re.compile(
    r"^(?P<name>.+?): (?P<verb>folds|checks|calls|bets|raises)"
    r"(?: (?P<first>[\d,.]+))?(?: to (?P<to>[\d,.]+))?(?P<all_in> and is all-in)?$"
)
_STREET = re.compile(
    r"^\*\*\* (?P<street>FLOP|TURN|RIVER|FIRST FLOP|SECOND FLOP) \*\*\* (?P<cards>.*)$"
)
_SHOWS = re.compile(r"^(?P<name>.+?): shows \[(?P<cards>[^\]]+)\]")
_SUMMARY_SHOWED = re.compile(
    r"^Seat \d+: (?P<name>.+?)(?: \([^)]*\))* showed \[(?P<cards>[^\]]+)\]"
)
_UNCALLED = re.compile(r"^Uncalled bet \((?P<amount>[\d,.]+)\) returned to (?P<name>.+)$")
_COLLECTED = re.compile(r"^(?P<name>.+?) collected (?P<amount>[\d,.]+) from")
_IGNORED = (
    "*** HOLE CARDS ***",
    "*** SHOWDOWN ***",
    "*** SUMMARY ***",
)
_STREETS = {"FLOP": Street.FLOP, "TURN": Street.TURN, "RIVER": Street.RIVER}


class HandHistoryError(ValueError):
    """A hand that cannot be read or replayed; `line` is 1-based in the pasted text."""

    def __init__(self, message: str, line: int = 0, hand_id: str = "") -> None:
        prefix = f"hand {hand_id}, " if hand_id else ""
        where = f"line {line}: " if line else ""
        super().__init__(f"{prefix}{where}{message}")
        self.line = line
        self.hand_id = hand_id


def _chips(text: str) -> int:
    value = float(text.replace(",", ""))
    if value != int(value):
        raise ValueError(f"fractional chips are not supported: {text}")
    return int(value)


def _cards(text: str) -> tuple[Card, ...]:
    return tuple(Card.parse(item) for item in text.split())


@dataclass
class ParsedHand:
    hand_id: str
    tournament_id: str
    tournament_name: str
    level: int
    small_blind: int
    big_blind: int
    ante: int
    date: str
    max_players: int
    button_seat_number: int
    names: list[str]
    """Players in table order (seat numbers ascending)."""
    seat_numbers: list[int]
    stacks: list[int]
    bounties: list[str]
    hero: str | None
    hero_cards: tuple[Card, Card] | None
    shown: dict[str, tuple[Card, Card]]
    board: list[Card]
    actions: list[tuple[int, Street, str, str, int | None, int | None]] = field(
        default_factory=list
    )
    """(line, street, player, verb, first amount, raise-to amount)."""
    posts: list[tuple[int, str, str, int]] = field(default_factory=list)
    collected: dict[str, int] = field(default_factory=dict)
    uncalled: dict[str, int] = field(default_factory=dict)
    first_line: int = 0


def split_hands(text: str) -> list[tuple[int, list[str]]]:
    """(first line number, lines) for every hand in a pasted file."""
    hands: list[tuple[int, list[str]]] = []
    current: list[str] = []
    start = 0
    for number, raw in enumerate(text.replace("\r\n", "\n").replace("\ufeff", "").split("\n"), 1):
        line = raw.strip()
        if _HAND.match(line):
            if current:
                hands.append((start, current))
            current, start = [line], number
        elif current:
            current.append(line)
    if current:
        hands.append((start, current))
    return hands


def parse_hand(lines: list[str], first_line: int = 1) -> ParsedHand:
    header = _HAND.match(lines[0])
    if header is None:
        raise HandHistoryError("not a GG hand (expected 'Poker Hand #...')", first_line)
    hand_id = header.group("id")
    rest = header.group("rest")
    tournament = _TOURNAMENT.search(rest)
    if tournament is None:
        raise HandHistoryError("only tournament hands are supported", first_line, hand_id)
    level = _LEVEL.search(rest)
    if level is None:
        raise HandHistoryError("the header has no 'Level n(sb/bb(ante))'", first_line, hand_id)
    date = _DATE.search(rest)
    hand = ParsedHand(
        hand_id=hand_id,
        tournament_id=tournament.group("id"),
        tournament_name=tournament.group("name").strip(),
        level=int(level.group("level")),
        small_blind=_chips(level.group("sb")),
        big_blind=_chips(level.group("bb")),
        ante=_chips(level.group("ante")) if level.group("ante") else 0,
        date=date.group("date") if date else "",
        max_players=0,
        button_seat_number=0,
        names=[],
        seat_numbers=[],
        stacks=[],
        bounties=[],
        hero=None,
        hero_cards=None,
        shown={},
        board=[],
        first_line=first_line,
    )
    street = Street.PREFLOP
    in_summary = False
    for offset, line in enumerate(lines[1:], 1):
        number = first_line + offset
        if not line:
            continue
        if line == "*** SUMMARY ***":
            in_summary = True
            continue
        if in_summary:
            shown = _SUMMARY_SHOWED.match(line)
            if shown and shown.group("name") in hand.names:
                cards = _cards(shown.group("cards"))
                if len(cards) == 2:
                    hand.shown.setdefault(shown.group("name"), (cards[0], cards[1]))
            continue
        if table := _TABLE.match(line):
            hand.max_players = int(table.group("max"))
            hand.button_seat_number = int(table.group("button"))
            continue
        if (seat := _SEAT.match(line)) and street is Street.PREFLOP and not hand.actions:
            if "is sitting out" in line:
                continue
            hand.seat_numbers.append(int(seat.group("seat")))
            hand.names.append(seat.group("name"))
            hand.stacks.append(_chips(seat.group("chips")))
            hand.bounties.append((seat.group("bounty") or "").strip())
            continue
        if post := _POST.match(line):
            if post.group("kind") == "straddle":
                raise HandHistoryError("straddles are not supported", number, hand_id)
            hand.posts.append(
                (number, post.group("name"), post.group("kind"), _chips(post.group("amount")))
            )
            continue
        if dealt := _DEALT.match(line):
            if dealt.group("cards"):
                cards = _cards(dealt.group("cards"))
                if len(cards) != 2:
                    raise HandHistoryError(
                        "only hold'em (two hole cards) is supported", number, hand_id
                    )
                hand.hero = dealt.group("name")
                hand.hero_cards = (cards[0], cards[1])
            continue
        if found := _STREET.match(line):
            name = found.group("street")
            if name not in _STREETS:
                raise HandHistoryError("run-it-twice hands are not supported", number, hand_id)
            street = _STREETS[name]
            boards = re.findall(r"\[([^\]]+)\]", found.group("cards"))
            if not boards:
                raise HandHistoryError("street without board cards", number, hand_id)
            hand.board = list(_cards(" ".join(boards)))
            continue
        if action := _ACTION.match(line):
            first = action.group("first")
            to = action.group("to")
            hand.actions.append(
                (
                    number,
                    street,
                    action.group("name"),
                    action.group("verb"),
                    _chips(first) if first else None,
                    _chips(to) if to else None,
                )
            )
            continue
        if shows := _SHOWS.match(line):
            cards = _cards(shows.group("cards"))
            if len(cards) == 2:
                hand.shown[shows.group("name")] = (cards[0], cards[1])
            continue
        if uncalled := _UNCALLED.match(line):
            hand.uncalled[uncalled.group("name")] = _chips(uncalled.group("amount"))
            continue
        if collected := _COLLECTED.match(line):
            name = collected.group("name")
            hand.collected[name] = hand.collected.get(name, 0) + _chips(collected.group("amount"))
            continue
    if not hand.names:
        raise HandHistoryError("no seats found", first_line, hand_id)
    if hand.button_seat_number == 0:
        raise HandHistoryError("no 'Table ... Seat #n is the button' line", first_line, hand_id)
    if hand.hero_cards is not None and hand.hero is not None:
        hand.shown.setdefault(hand.hero, hand.hero_cards)
    return hand


@dataclass(frozen=True, slots=True)
class ReplayedHand:
    parsed: ParsedHand
    table: TableHand
    """The hand replayed on the engine (complete)."""
    seat_of: dict[str, int]
    button: int
    actions: tuple[tuple[int, Action], ...]
    """(seat, action) in order, as the engine took them."""
    stacks_before: tuple[int, ...]
    """Chips behind at the start of the hand (before antes and blinds)."""
    hidden: frozenset[int]
    """Seats whose cards were not shown (filled with stand-in cards)."""


def _button_seat(hand: ParsedHand) -> int:
    if hand.button_seat_number in hand.seat_numbers:
        return hand.seat_numbers.index(hand.button_seat_number)
    # A dead button: the button is the player before the next occupied seat.
    later = [
        index for index, number in enumerate(hand.seat_numbers) if number > hand.button_seat_number
    ]
    following = later[0] if later else 0
    return (following - 1) % len(hand.seat_numbers)


def replay_hand(hand: ParsedHand) -> ReplayedHand:
    """Play the hand on the engine with the recorded cards and actions."""
    players = len(hand.names)
    if not 2 <= players <= 9:
        raise HandHistoryError(
            f"{players} players; 2 to 9 are supported", hand.first_line, hand.hand_id
        )
    seat_of = {name: index for index, name in enumerate(hand.names)}
    stacks = list(hand.stacks)
    antes = 0
    blinds: dict[str, int] = {}
    for number, name, kind, amount in hand.posts:
        if name not in seat_of:
            raise HandHistoryError(f"unknown player {name}", number, hand.hand_id)
        if kind == "the ante":
            seat = seat_of[name]
            paid = min(amount, stacks[seat])
            stacks[seat] -= paid
            antes += paid
        else:
            blinds[kind] = seat_of[name]
    button = _button_seat(hand)
    # The engine places blinds from the button; a GG hand where the blinds are not on the
    # expected seats (dead blinds, missing small blind) cannot be replayed exactly.
    expected_bb = (button + 1) % players if players == 2 else (button + 2) % players
    if "big blind" in blinds and blinds["big blind"] != expected_bb:
        raise HandHistoryError(
            "the blinds are not on the seats after the button (dead blinds are not supported)",
            hand.first_line,
            hand.hand_id,
        )
    if any(stack <= 0 for stack in stacks):
        raise HandHistoryError(
            "a player has no chips after the antes", hand.first_line, hand.hand_id
        )
    used = {card for cards in hand.shown.values() for card in cards} | set(hand.board)
    spare = [card for card in full_deck() if card not in used]
    random.Random(hand.hand_id).shuffle(spare)
    holes: list[tuple[Card, Card]] = []
    hidden: set[int] = set()
    for seat, name in enumerate(hand.names):
        if name in hand.shown:
            holes.append(hand.shown[name])
        else:
            holes.append((spare.pop(), spare.pop()))
            hidden.add(seat)
    board = list(hand.board) + [spare.pop() for _ in range(5 - len(hand.board))]
    try:
        table = TableHand(
            stacks,
            button,
            hand.small_blind,
            hand.big_blind,
            seed=0,
            extra_dead_money=antes,
            cards=(tuple(holes), tuple(board)),
        )
    except ValueError as error:
        raise HandHistoryError(str(error), hand.first_line, hand.hand_id) from error
    taken: list[tuple[int, Action]] = []
    for number, street, name, verb, first, to in hand.actions:
        if table.complete:
            raise HandHistoryError("an action after the hand ended", number, hand.hand_id)
        if name not in seat_of:
            raise HandHistoryError(f"unknown player {name}", number, hand.hand_id)
        seat = seat_of[name]
        if table.betting.current_actor != seat:
            expected = table.betting.current_actor
            who = hand.names[expected] if expected is not None else "nobody"
            raise HandHistoryError(f"{name} acts but it is {who}'s turn", number, hand.hand_id)
        if table.betting.street != street:
            raise HandHistoryError(
                "the action is on another street than the engine", number, hand.hand_id
            )
        legal = table.legal_actions(seat)
        if verb == "folds":
            action = Action.fold()
        elif verb == "checks":
            action = Action.check()
        elif verb == "calls":
            action = Action.call()
        elif verb == "bets":
            if first is None:
                raise HandHistoryError("a bet without an amount", number, hand.hand_id)
            action = Action.raise_to(first + table.betting.seats[seat].street_contribution)
        else:
            if to is None:
                raise HandHistoryError("a raise without 'to'", number, hand.hand_id)
            action = Action.raise_to(to)
        too_large = (
            action.type is ActionType.RAISE_TO
            and legal.max_raise_to is not None
            and action.amount is not None
            and action.amount > legal.max_raise_to
        )
        if too_large:
            raise HandHistoryError("raise larger than the stack", number, hand.hand_id)
        try:
            table.act(seat, action)
        except IllegalActionError as error:
            raise HandHistoryError(
                f"the engine rejects this action: {error}", number, hand.hand_id
            ) from error
        taken.append((seat, action))
    if not table.complete:
        raise HandHistoryError(
            "the hand stops before it is complete", hand.first_line, hand.hand_id
        )
    return ReplayedHand(
        parsed=hand,
        table=table,
        seat_of=seat_of,
        button=button,
        actions=tuple(taken),
        stacks_before=tuple(hand.stacks),
        hidden=frozenset(hidden),
    )


@dataclass
class ImportResult:
    hands: list[ReplayedHand]
    problems: list[str]


def read_hand_histories(text: str, limit: int = 2_000) -> ImportResult:
    """Parse and replay every hand in `text`; hands that fail are listed in `problems`."""
    hands: list[ReplayedHand] = []
    problems: list[str] = []
    chunks = split_hands(text)
    if not chunks:
        raise HandHistoryError("no GG hands found (each hand starts with 'Poker Hand #')")
    for start, lines in chunks[:limit]:
        try:
            hands.append(replay_hand(parse_hand(lines, start)))
        except HandHistoryError as error:
            problems.append(str(error))
        except ValueError as error:
            problems.append(f"line {start}: {error}")
    if len(chunks) > limit:
        problems.append(f"only the first {limit} hands were read")
    return ImportResult(hands, problems)
