"""Newline-JSON lifecycle adapter for the unoSDK Python starter.

Standard library only. Handles:

- initialization (``initialize`` -> ``ready``),
- request-id echoing on every reply,
- JSON serialization plus stdout flushing (stdout is protocol-only),
- checkpoint pass-through with the 1 MiB bound,
- terminal acknowledgement (``game_over`` -> ``done``),
- stderr-only diagnostics via :func:`log`.

Your code goes in ``select_action`` (see ``strategy.py``). You do not need to
edit this file.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from uno_agent import protocol

SelectAction = Callable[[dict[str, Any], Any], tuple[str, Any]]
InitializeHook = Callable[[str, int, dict[str, Any]], Any]
GameOverHook = Callable[[dict[str, Any], dict[str, Any], Any], None]


def log(*parts: object) -> None:
    """Write diagnostics to stderr. Never print() to stdout: it is protocol-only."""
    print(*parts, file=sys.stderr, flush=True)


def _send(msg: dict[str, Any]) -> None:
    sys.stdout.write(protocol.dumps_line(msg) + "\n")
    sys.stdout.flush()


def _handle_initialize(msg: dict[str, Any],
                       on_initialize: InitializeHook | None) -> Any:
    state = (on_initialize(msg["game_id"], msg["seat"], msg.get("config", {}))
             if on_initialize is not None else None)
    if state is not None and not protocol.is_checkpoint_ok(state):
        log("agent: initial checkpoint oversized; sending null")
        state = None
    _send(protocol.build_ready(msg["request_id"], state))
    return state


def _handle_act(msg: dict[str, Any], select_action: SelectAction,
                checkpoint: Any = None) -> Any:
    action_id, new_state = select_action(
        msg["observation"], msg.get("agent_state"))
    if not isinstance(action_id, str):
        log("agent: select_action must return a string action_id")
        sys.exit(1)
    if new_state is not None and not protocol.is_checkpoint_ok(new_state):
        log("agent: checkpoint exceeds 1 MiB; failing this response")
        sys.exit(1)
    _send(protocol.build_action(msg["request_id"], action_id, new_state))
    return checkpoint if new_state is None else new_state


def _handle_game_over(msg: dict[str, Any],
                      on_game_over: GameOverHook | None, checkpoint: Any = None) -> None:
    if on_game_over is not None:
        on_game_over(msg["outcome"], msg.get("final_observation", {}),
                     checkpoint)
    _send(protocol.build_done(msg["request_id"]))


def run_agent(select_action: SelectAction,
              on_initialize: InitializeHook | None = None,
              on_game_over: GameOverHook | None = None) -> None:
    """Run the agent loop until stdin closes, reinitializing between games.

    ``select_action(observation, agent_state)`` must return
    ``(action_id, new_agent_state)`` where ``action_id`` is one of the current
    request's ``legal_actions`` and ``new_agent_state`` is a JSON value of at
    most 1 MiB (or ``None`` to keep the previous checkpoint).

    Exceptions from the hooks are not swallowed: the process prints the
    traceback to stderr and exits non-zero so the organizer can restart it.
    """
    checkpoint: Any = None
    for raw in iter(lambda: sys.stdin.buffer.readline(protocol.MAX_PROTOCOL_LINE + 2), b""):
        if len(raw) > protocol.MAX_PROTOCOL_LINE + 1:
            raise ValueError("organizer line exceeds protocol limit")
        line = raw.strip()
        if not line:
            continue
        msg, parse_error = protocol.parse_line(line)
        if parse_error is not None:
            log(f"agent: ignoring non-JSON input line: {parse_error}")
            continue
        if msg is None:
            continue
        err = protocol.validate_organizer_message(msg)
        if err is not None:
            log(f"agent: invalid organizer message: {err}")
            continue
        mtype = msg["type"]
        if mtype == "initialize":
            checkpoint = _handle_initialize(msg, on_initialize)
        elif mtype == "act":
            checkpoint = _handle_act(msg, select_action, checkpoint)
        elif mtype == "game_over":
            _handle_game_over(msg, on_game_over, checkpoint)
            checkpoint = None
