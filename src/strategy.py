"""EDIT THIS FILE -- your UNO strategy lives here.

``select_action(observation, agent_state)`` is called once per decision with:

- ``observation``: your hand (local ``h`` refs), hand sizes for every seat,
  the top discard, the active colour, pile sizes, the public event history,
  and the explicit ``legal_actions`` for this request (request-scoped ``a0..``
  ids, canonically ordered so ``a0`` is always legal and matches the
  organizer fallback).
- ``agent_state``: your last accepted checkpoint (or ``None``).

Return ``(action_id, new_agent_state)``:

- ``action_id`` must come from the current ``legal_actions``. Never reuse an
  id from an earlier request.
- ``new_agent_state`` is any JSON value up to 1 MiB, or ``None`` to keep the
  previous checkpoint. Your process may be killed and restarted at any time;
  anything you need after a restart must be reconstructible from the public
  ``observation["history"]`` plus this checkpoint.

Rules of the road: stdout is protocol-only (log to stderr), answer every
``act`` within the decision deadline, and never assume ``h``/``a`` ids
persist past the current request.
"""

from typing import Any


def _pending_penalty(observation: dict[str, Any]) -> int:
    """Reconstruct the public multiplier chain after a process restart."""
    total = 0
    for event in observation.get("history") or []:
        if event.get("type") == "play":
            kind = (event.get("card") or {}).get("kind")
            if kind == "draw_two":
                total += 2
            elif kind == "wild_draw_four":
                total += 4
        elif event.get("type") == "penalty_draw":
            total = 0
    reported = observation.get("pending_penalty")
    return reported if isinstance(reported, int) and reported >= 0 else total


def select_action(observation: dict[str, Any],
                  agent_state: Any) -> tuple[str, Any]:
    """Choose a playable action when one exists, else the first legal action."""
    legal = observation["legal_actions"]
    # During stacking, the organizer offers compatible multiplier plays plus
    # ``draw`` to accept the chain. Prefer continuing the chain; this explicit
    # branch also works with observations that include a public pending total.
    if observation.get("phase") == "TURN" and _pending_penalty(observation):
        stack = [action for action in legal if action["type"] == "play"]
        if stack:
            return stack[0]["action_id"], agent_state
    for action in legal:
        if action["type"] == "play":
            return action["action_id"], agent_state
    return legal[0]["action_id"], agent_state
