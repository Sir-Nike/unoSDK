"""Card model for Triad UNO v1. Engine internals use globally unique uids (0..107).

Observations never expose uids — see engine.observe() for local h-refs.
"""

from __future__ import annotations

from dataclasses import dataclass

COLORS: tuple[str, ...] = ("red", "yellow", "green", "blue")
COLOR_INDEX = {c: i for i, c in enumerate(COLORS)}

NUMBER = "number"
SKIP = "skip"
REVERSE = "reverse"
DRAW_TWO = "draw_two"
WILD = "wild"
WILD_DRAW_FOUR = "wild_draw_four"

COLOURED_ACTIONS = (SKIP, REVERSE, DRAW_TWO)

DECK_SIZE = 108


@dataclass(frozen=True)
class Card:
    """One physical card with a globally unique uid (0..107)."""

    color: str | None  # None for wild / wild_draw_four
    kind: str
    value: int | None  # 0..9 for numbers, else None
    uid: int

    def to_public_dict(self) -> dict[str, str | int | None]:
        """Return the participant-safe public fields (no uid)."""
        return {"color": self.color, "kind": self.kind, "value": self.value}

    def describe(self) -> str:
        """Return a short human-readable description of the card."""
        if self.kind == NUMBER:
            return f"{self.color} {self.value}"
        if self.kind in COLOURED_ACTIONS:
            return f"{self.color} {self.kind}"
        return self.kind


def build_canonical_deck() -> list[Card]:
    """Build the 108-card deck in canonical order with uids 0..107."""
    cards: list[Card] = []
    uid = 0
    for color in COLORS:
        # one zero
        cards.append(Card(color=color, kind=NUMBER, value=0, uid=uid))
        uid += 1
        for v in range(1, 10):
            for _ in range(2):
                cards.append(Card(color=color, kind=NUMBER, value=v, uid=uid))
                uid += 1
        for kind in COLOURED_ACTIONS:
            for _ in range(2):
                cards.append(Card(color=color, kind=kind, value=None, uid=uid))
                uid += 1
    for _ in range(4):
        cards.append(Card(color=None, kind=WILD, value=None, uid=uid))
        uid += 1
    for _ in range(4):
        cards.append(Card(color=None, kind=WILD_DRAW_FOUR, value=None, uid=uid))
        uid += 1
    if len(cards) != DECK_SIZE:
        msg = f"canonical deck has {len(cards)} cards, expected {DECK_SIZE}"
        raise AssertionError(msg)
    if uid != DECK_SIZE:
        msg = f"canonical deck used {uid} uids, expected {DECK_SIZE}"
        raise AssertionError(msg)
    return cards


def hand_color_counts(hand: list[Card]) -> dict[str, int]:
    """Count how many cards of each colour are present in a hand."""
    counts = dict.fromkeys(COLORS, 0)
    for card in hand:
        if card.color in counts:
            counts[card.color] += 1
    return counts


def most_common_color_first(hand: list[Card]) -> list[str]:
    """Return all colours ordered by descending hand count, then fixed order."""
    counts = hand_color_counts(hand)
    return sorted(COLORS, key=lambda c: (-counts[c], COLOR_INDEX[c]))
