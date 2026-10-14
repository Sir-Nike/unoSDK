"""Baseline opponents for local training and validation games.

Both baselines operate only on the filtered observation (own hand + counts +
explicit legal actions). They never touch hidden state.
"""

from __future__ import annotations

import random
from typing import Any

JsonDict = dict[str, Any]


def _action_id(entry: JsonDict) -> str:
    """Extract a validated action id from one legal-action entry."""
    action_id = entry["action_id"]
    if not isinstance(action_id, str):
        msg = "legal action entry is missing a string action_id"
        raise TypeError(msg)
    return action_id


class RandomAgent:
    """Uniform random baseline (seeded per instance)."""

    def __init__(self, seed: int = 0) -> None:
        """Create a baseline with its own RNG stream."""
        self.rng = random.Random(seed)

    def select(self, observation: JsonDict) -> str:
        """Return a random legal action id."""
        legal = observation["legal_actions"]
        if not legal:
            msg = "no legal actions"
            raise RuntimeError(msg)
        return _action_id(self.rng.choice(legal))


class HeuristicAgent:
    """Plays first non-wild in canonical order; keeps wilds; plays drawn cards.

    Canonical order already sorts numbers → actions → wild → WD4 with a0 as
    fallback, so this is: prefer cheap cards, save wilds, always press a
    playable draw, and choose the most-common-hand colour (server orders wild
    variants that way, so a0 among wilds is already the preferred colour).
    """

    def __init__(self, seed: int = 0) -> None:
        """Create a baseline with its own RNG stream."""
        self.rng = random.Random(seed)

    def select(self, observation: JsonDict) -> str:
        """Return the heuristic action id for an observation."""
        legal = observation["legal_actions"]
        if not legal:
            msg = "no legal actions"
            raise RuntimeError(msg)
        phase = observation["phase"]
        if phase == "CHOOSE_INITIAL_COLOR":
            return _action_id(legal[0])
        if phase == "AFTER_DRAW":
            plays = [a for a in legal if a["type"] == "play"]
            if plays:
                return _action_id(plays[0])
            return _action_id(legal[0])
        # TURN
        non_wild = [
            a
            for a in legal
            if a["type"] == "play"
            and (a.get("card") or {}).get("kind") not in ("wild", "wild_draw_four")
        ]
        if non_wild:
            return _action_id(non_wild[0])
        return _action_id(legal[0])
