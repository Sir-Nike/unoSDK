#!/usr/bin/env python3
"""CPU heuristic example agent (standard library only).

Plays the first legal non-wild action in canonical order, presses a playable
draw, and saves wilds for later. Run it with ``python agent.py``.
"""

from __future__ import annotations

import importlib.util
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


def select_action(observation: dict[str, Any], agent_state: Any) -> tuple[str, Any]:
    """Choose a legal action: follow the phase rules and save wilds for later."""
    legal: list[dict[str, Any]] = observation["legal_actions"]
    phase = observation["phase"]
    if phase == "CHOOSE_INITIAL_COLOR":
        return str(legal[0]["action_id"]), agent_state
    if phase == "AFTER_DRAW":
        plays = [action for action in legal if action["type"] == "play"]
        return str((plays or legal)[0]["action_id"]), agent_state
    non_wild = [
        action
        for action in legal
        if action["type"] == "play"
        and (action.get("card") or {}).get("kind") not in ("wild", "wild_draw_four")
    ]
    return str((non_wild or legal)[0]["action_id"]), agent_state


if __name__ == "__main__":
    run_agent(select_action)
