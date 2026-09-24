from __future__ import annotations

import pytest

from tableforge.poker.cards import Card, Rank, Suit, full_deck


def test_card_round_trip() -> None:
    for card in full_deck():
        assert Card.parse(str(card)) == card


def test_full_deck_has_52_unique_cards() -> None:
    cards = full_deck()

    assert len(cards) == 52
    assert len(set(cards)) == 52
    assert {card.rank for card in cards} == set(Rank)
    assert {card.suit for card in cards} == set(Suit)


@pytest.mark.parametrize("value", ["", "A", "10s", "1s", "Ax", "abc"])
def test_invalid_card_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        Card.parse(value)
