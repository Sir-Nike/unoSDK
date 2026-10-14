"""Newline-JSON wire protocol for Triad UNO v1 (organizer ↔ agent).

Transport: one JSON object per line on stdin/stdout. Stdout carries protocol
messages only; all diagnostics go to stderr. Every organizer message carries a
unique ``request_id`` which the agent must echo. Action ids (``a0..``) are
valid only for the request that issued them.

This module is stdlib-only so agents can vendor it. The JSON Schema files in
``schemas/`` are the authoritative contract; the builders/validators here
mirror them.
"""

from __future__ import annotations

import json
import math
from typing import cast

PROTOCOL_VERSION = "1.0"

# Published resource bounds (provisional defaults, see docs/protocol.md).
MAX_PROTOCOL_LINE = 4 * 1024 * 1024  # 4 MiB per newline-delimited message
MAX_CHECKPOINT = 1024 * 1024  # 1 MiB serialized agent_state
MAX_STDERR_PER_GAME = 10 * 1024 * 1024  # 10 MiB retained per agent/game

ORGANIZER_MSGS = ("initialize", "act", "game_over")
AGENT_MSGS = ("ready", "action", "done")

JsonDict = dict[str, object]


def make_request_id(game_id: str, counter: int) -> str:
    """Return the canonical request id for a game and decision counter."""
    return f"{game_id}-d{counter}"


# ---------------------------------------------------------------------------
# builders (organizer side)
# ---------------------------------------------------------------------------


def build_initialize(
    game_id: str, seat: int, request_id: str, max_turns: int, ruleset: str = "triad-uno-v1",
    config: JsonDict | None = None,
) -> JsonDict:
    """Build an ``initialize`` message for one agent seat."""
    return {
        "type": "initialize",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "game_id": game_id,
        "seat": seat,
        "ruleset": ruleset,
        "config": config if config is not None else {"max_turns": max_turns},
    }


def build_act(
    game_id: str, request_id: str, observation: JsonDict, agent_state: object | None = None
) -> JsonDict:
    """Build an ``act`` message carrying one observation."""
    return {
        "type": "act",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "game_id": game_id,
        "observation": observation,
        "agent_state": agent_state,
    }


def build_game_over(
    game_id: str, request_id: str, outcome: JsonDict, final_observation: JsonDict
) -> JsonDict:
    """Build a ``game_over`` message with the final outcome and observation."""
    return {
        "type": "game_over",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "game_id": game_id,
        "outcome": outcome,
        "final_observation": final_observation,
    }


# ---------------------------------------------------------------------------
# builders (agent side)
# ---------------------------------------------------------------------------


def build_ready(request_id: str, agent_state: object | None = None) -> JsonDict:
    """Build a ``ready`` message, optionally carrying a checkpoint."""
    return {"type": "ready", "request_id": request_id, "agent_state": agent_state}


def build_action(request_id: str, action_id: str, agent_state: object | None = None) -> JsonDict:
    """Build an ``action`` message selecting one request-scoped action id."""
    return {
        "type": "action",
        "request_id": request_id,
        "action_id": action_id,
        "agent_state": agent_state,
    }


def build_done(request_id: str) -> JsonDict:
    """Build a ``done`` message signalling clean agent shutdown."""
    return {"type": "done", "request_id": request_id}


def dumps_line(msg: JsonDict) -> str:
    """Serialize one protocol message; raises ValueError if over the line bound."""
    line = json.dumps(msg, separators=(",", ":"), allow_nan=False)
    if len(line.encode("utf-8")) > MAX_PROTOCOL_LINE:
        err = "protocol message exceeds 4 MiB line limit"
        raise ValueError(err)
    return line


def checkpoint_size(value: object) -> int:
    """Return the serialized UTF-8 byte size of a checkpoint value."""
    if value is None:
        return 0
    return len(json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def is_checkpoint_ok(value: object) -> bool:
    """Checkpoints must be JSON-serializable and fit in 1 MiB."""
    if value is None:
        return True
    try:
        return checkpoint_size(value) <= MAX_CHECKPOINT
    except (TypeError, ValueError, RecursionError):
        return False


# ---------------------------------------------------------------------------
# validators
# ---------------------------------------------------------------------------


def _bad(reason: str) -> str:
    # Fixed-string errors only: never echo hidden game state back to the agent.
    return reason


def _validate_organizer_body(mtype: str, msg: JsonDict) -> str | None:
    if mtype == "initialize":
        config = msg.get("config", {})
        if not isinstance(config, dict):
            return _bad("invalid config")
        players = config.get("players", 4)
        if type(players) is not int or players != 4:
            return _bad("invalid players")
        seat = msg.get("seat")
        return None if type(seat) is int and 0 <= seat < players else _bad("invalid seat")
    if mtype == "act":
        if not isinstance(msg.get("observation"), dict):
            return _bad("act requires observation")
        if "agent_state" in msg and not is_checkpoint_ok(msg["agent_state"]):
            return _bad("oversized agent_state")
        return None
    if mtype == "game_over" and not isinstance(msg.get("outcome"), dict):
        return _bad("game_over requires outcome")
    return None


def validate_organizer_message(msg: JsonDict) -> str | None:
    """Validate an organizer-to-agent message, returning error or None."""
    if not isinstance(msg, dict):
        return _bad("message must be an object")
    mtype = msg.get("type")
    if mtype not in ORGANIZER_MSGS:
        return _bad("unknown organizer message type")
    for key in ("protocol_version", "request_id", "game_id"):
        if not isinstance(msg.get(key), str) or not msg[key]:
            return _bad(f"missing or invalid {key}")
    if msg["protocol_version"] != PROTOCOL_VERSION:
        return _bad("unsupported protocol version")
    return _validate_organizer_body(mtype, msg)


def _has_oversized_checkpoint(msg: JsonDict) -> bool:
    if "agent_state" not in msg:
        return False
    return msg["agent_state"] is not None and not is_checkpoint_ok(msg["agent_state"])


def _validate_action_response(msg: JsonDict, legal_ids: set[str] | None) -> str | None:
    action_id = msg.get("action_id")
    if not isinstance(action_id, str) or action_id not in (legal_ids or set()):
        return _bad("illegal action id")
    if _has_oversized_checkpoint(msg):
        return _bad("oversized checkpoint")
    return None


def validate_agent_response(
    msg: JsonDict, expected_type: str, expected_request_id: str, legal_ids: set[str] | None = None
) -> str | None:
    """Validate an agent-to-organizer message, returning error or None.

    Error strings are fixed and safe to log; they never include hidden state.
    """
    if not isinstance(msg, dict):
        return _bad("response must be an object")
    allowed = {"type", "request_id"}
    if expected_type in ("action", "ready"):
        allowed.add("agent_state")
    if expected_type == "action":
        allowed.add("action_id")
    if set(msg) - allowed:
        return _bad("unexpected response fields")
    if msg.get("type") != expected_type:
        return _bad(f"expected {expected_type}")
    if msg.get("request_id") != expected_request_id:
        return _bad("request id mismatch")
    if expected_type == "action":
        return _validate_action_response(msg, legal_ids)
    if expected_type == "ready" and _has_oversized_checkpoint(msg):
        return _bad("oversized checkpoint")
    return None


def parse_line(line: str | bytes) -> tuple[JsonDict | None, str | None]:
    """Parse one protocol line. Returns (message, error)."""
    if isinstance(line, bytes):
        try:
            line = line.decode("utf-8")
        except UnicodeDecodeError:
            return None, _bad("response is not valid utf-8")
    if len(line.encode("utf-8")) > MAX_PROTOCOL_LINE:
        return None, _bad("response exceeds line limit")
    line = line.strip()
    if not line:
        return None, _bad("empty response")
    try:
        parsed = json.loads(line, parse_constant=_reject_constant, parse_float=_finite_float,
                            object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        return None, _bad("response is not valid json")
    return cast("JsonDict", parsed), None


def _reject_constant(value):
    raise ValueError("non-finite JSON number")


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite JSON number")
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


OBSERVATION_KEYS = frozenset(
    {
        "seat",
        "phase",
        "turn_number",
        "turns_completed",
        "direction",
        "active_color",
        "hand",
        "hand_sizes",
        "top_discard",
        "draw_pile_size",
        "discard_pile_size",
        "history",
        "private_events",
        "drawn_card_ref",
        "legal_actions",
        "finished_order",
        "is_finished",
    }
)

# Substrings that must never appear in agent-facing JSON (hidden state).
FORBIDDEN_OBSERVATION_TOKENS = ('"uid"', '"card_uid"', '"seed"', '"rng"')


def check_observation_shape(obs: JsonDict) -> str | None:
    """Lightweight stdlib shape + privacy check for observations."""
    if not isinstance(obs, dict):
        return _bad("observation must be an object")
    missing = OBSERVATION_KEYS - set(obs)
    if missing:
        return _bad(f"observation missing keys: {sorted(missing)}")
    for card in cast("list[object]", obs.get("hand", [])):
        if not isinstance(card, dict) or "card_ref" not in card:
            return _bad("hand entries need local card_ref")
        if "uid" in card or "card_uid" in card:
            return _bad("hand leaks internal ids")
    text = json.dumps(obs)
    for tok in FORBIDDEN_OBSERVATION_TOKENS:
        if tok in text:
            return _bad("observation leaks hidden state")
    return None
