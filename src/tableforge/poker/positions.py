"""Position names for 2 to 9 players.

Seats are counted from the button (0 = button). Heads-up the button is the small blind.
With three or more players the small and big blind follow the button, and the seats between
the big blind and the button are named as strategy tables have always named them for up to
6 players (4-max: CO; 5-max: HJ, CO; 6-max: UTG, HJ, CO), extended for full ring
(7-max: UTG, LJ, HJ, CO; 8-max: UTG, UTG1, LJ, HJ, CO; 9-max: UTG, UTG1, UTG2, LJ, HJ, CO).
"""

from __future__ import annotations

_EARLY: dict[int, tuple[str, ...]] = {
    0: (),
    1: ("CO",),
    2: ("HJ", "CO"),
    3: ("UTG", "HJ", "CO"),
    4: ("UTG", "LJ", "HJ", "CO"),
    5: ("UTG", "UTG1", "LJ", "HJ", "CO"),
    6: ("UTG", "UTG1", "UTG2", "LJ", "HJ", "CO"),
}


def position_name(players: int, relative: int) -> str:
    """Name of the seat `relative` places after the button."""
    if not 2 <= players <= 9 or not 0 <= relative < players:
        raise ValueError("2 to 9 players, and a seat at the table")
    if players == 2:
        return "SB" if relative == 0 else "BB"
    if relative == 0:
        return "BTN"
    if relative == 1:
        return "SB"
    if relative == 2:
        return "BB"
    return _EARLY[players - 3][relative - 3]


def preflop_order(players: int) -> tuple[str, ...]:
    """Positions in preflop acting order (heads-up: SB, BB)."""
    if players == 2:
        return ("SB", "BB")
    return (*_EARLY[players - 3], "BTN", "SB", "BB")


def relative_seat(position: str, players: int) -> int:
    """Seat of a position counted from the button (inverse of `position_name`)."""
    for relative in range(players):
        if position_name(players, relative) == position:
            return relative
    raise ValueError(f"no {position} at a {players}-player table")
