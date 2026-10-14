#!/usr/bin/env python3
"""CPU random example agent (standard library only).

Seeds a per-game generator from the game id and seat so games are
reproducible, then chooses uniformly among the legal actions.
"""

from __future__ import annotations

import hashlib
import importlib.util
import random
import sys
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(_SRC))
_spec = importlib.util.spec_from_file_location(
    "uno_agent.adapter", _SRC / "uno_agent" / "adapter.py"
)
if _spec is None or _spec.loader is None:
    raise ImportError("cannot load uno_agent.adapter")
_adapter: Any = importlib.util.module_from_spec(_spec)
sys.modules["uno_agent.adapter"] = _adapter
_spec.loader.exec_module(_adapter)
run_agent = _adapter.run_agent


class RandomStrategy:
    """Uniformly random legal actions with a deterministic per-game seed."""

    def __init__(self) -> None:
        """Create the strategy before any game is initialized."""
        self.rng = random.Random(0)

    def on_initialize(self, game_id: str, seat: int, _config: dict[str, Any]) -> None:
        """Seed the generator from the game id and seat."""
        digest = hashlib.sha256(f"{game_id}:{seat}".encode()).hexdigest()
        self.rng = random.Random(int(digest[:16], 16))

    def select_action(
        self, observation: dict[str, Any], _agent_state: Any
    ) -> tuple[str, None]:
        """Return a uniformly random legal action id."""
        legal: list[dict[str, Any]] = observation["legal_actions"]
        return str(self.rng.choice(legal)["action_id"]), None


if __name__ == "__main__":
    strategy = RandomStrategy()
    run_agent(strategy.select_action, on_initialize=strategy.on_initialize)
