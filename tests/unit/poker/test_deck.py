from __future__ import annotations

import pytest

from tableforge.poker.deck import Deck, DeckExhaustedError


def test_same_seed_produces_same_deck() -> None:
    first = Deck.shuffled("experiment:match:hand").deal(52)
    second = Deck.shuffled("experiment:match:hand").deal(52)

    assert first == second


def test_different_seed_changes_order() -> None:
    assert Deck.shuffled(1).deal(52) != Deck.shuffled(2).deal(52)


def test_deal_has_no_duplicates_and_tracks_remaining() -> None:
    deck = Deck.shuffled(42)

    cards = deck.deal(7)

    assert len(set(cards)) == 7
    assert deck.remaining == 45


def test_deck_exhaustion_is_atomic() -> None:
    deck = Deck.ordered()
    deck.deal(50)

    with pytest.raises(DeckExhaustedError):
        deck.deal(3)

    assert deck.remaining == 2


def test_negative_deal_is_rejected() -> None:
    with pytest.raises(ValueError):
        Deck.ordered().deal(-1)
