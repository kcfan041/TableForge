from __future__ import annotations

import pytest

from tableforge.poker.cards import Card
from tableforge.poker.evaluator import HandCategory, HandRank, evaluate


def cards(value: str) -> tuple[Card, ...]:
    return tuple(Card.parse(token) for token in value.split())


@pytest.mark.parametrize(
    ("value", "category", "kickers"),
    [
        ("As Kd 9h 6c 3s", HandCategory.HIGH_CARD, (14, 13, 9, 6, 3)),
        ("As Ad 9h 6c 3s", HandCategory.ONE_PAIR, (14, 9, 6, 3)),
        ("As Ad 9h 9c 3s", HandCategory.TWO_PAIR, (14, 9, 3)),
        ("As Ad Ah 6c 3s", HandCategory.THREE_OF_A_KIND, (14, 6, 3)),
        ("9s 8d 7h 6c 5s", HandCategory.STRAIGHT, (9,)),
        ("As Js 9s 6s 3s", HandCategory.FLUSH, (14, 11, 9, 6, 3)),
        ("As Ad Ah 6c 6s", HandCategory.FULL_HOUSE, (14, 6)),
        ("As Ad Ah Ac 3s", HandCategory.FOUR_OF_A_KIND, (14, 3)),
        ("9s 8s 7s 6s 5s", HandCategory.STRAIGHT_FLUSH, (9,)),
    ],
)
def test_known_hand_categories(
    value: str, category: HandCategory, kickers: tuple[int, ...]
) -> None:
    assert evaluate(cards(value)) == HandRank(category, kickers)


def test_wheel_is_five_high_straight() -> None:
    assert evaluate(cards("As 2d 3h 4c 5s")) == HandRank(HandCategory.STRAIGHT, (5,))


def test_seven_cards_choose_best_five() -> None:
    assert evaluate(cards("As Ad Ah Kc Ks 2d 3d")) == HandRank(
        HandCategory.FULL_HOUSE, (14, 13)
    )


def test_category_ordering() -> None:
    hands = [
        evaluate(cards("As Kd 9h 6c 3s")),
        evaluate(cards("As Ad 9h 6c 3s")),
        evaluate(cards("As Ad 9h 9c 3s")),
        evaluate(cards("As Ad Ah 6c 3s")),
        evaluate(cards("9s 8d 7h 6c 5s")),
        evaluate(cards("As Js 9s 6s 3s")),
        evaluate(cards("As Ad Ah 6c 6s")),
        evaluate(cards("As Ad Ah Ac 3s")),
        evaluate(cards("9s 8s 7s 6s 5s")),
    ]

    assert hands == sorted(hands)


def test_pair_kickers_break_tie() -> None:
    assert evaluate(cards("As Ad Kh Qc Js")) > evaluate(cards("Ah Ac Kd Qs Ts"))


def test_best_flush_uses_five_highest_suited_cards() -> None:
    assert evaluate(cards("As Ks 9s 6s 3s 2s Qd")) == HandRank(
        HandCategory.FLUSH, (14, 13, 9, 6, 3)
    )


@pytest.mark.parametrize("value", ["As Ad Ah Ac", "As Ad Ah Ac Ks Qs Js Ts", "As As 2d 3h 4c"])
def test_invalid_hand_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        evaluate(cards(value))
