#!/usr/bin/env python3
"""Stateful counter example: demonstrates checkpoint + restart recovery.

The checkpoint counts decisions. After a crash the organizer respawns the
process with the last accepted checkpoint, so the count continues instead of
resetting. Anything you need after a restart must be reconstructible from the
public history plus your checkpoint.
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


def select_action(observation: dict[str, Any], agent_state: Any) -> tuple[str, dict[str, int]]:
    """Prefer a non-wild play and advance the decision checkpoint."""
    legal: list[dict[str, Any]] = observation["legal_actions"]
    count = int((agent_state or {}).get("count", 0)) + 1
    non_wild = [
        action
        for action in legal
        if action["type"] == "play"
        and (action.get("card") or {}).get("kind") not in ("wild", "wild_draw_four")
    ]
    if legal[0]["type"] == "choose_color":
        action_id = str(legal[0]["action_id"])
    else:
        action_id = str((non_wild or legal)[0]["action_id"])
    return action_id, {"count": count}


if __name__ == "__main__":
    run_agent(select_action)
