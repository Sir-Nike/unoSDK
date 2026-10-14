#!/usr/bin/env python3
"""Dependency-free protocol smoke check for the unoSDK starters.

Drives an agent subprocess through the complete protocol lifecycle --
initialize -> act (TURN / AFTER_DRAW / CHOOSE_INITIAL_COLOR) -> game_over --
plus a malformed-input line, a kill-and-respawn restart, and strict stdout
discipline. Standard library only; works with plain ``python3``.

The checks verify protocol-correctness for ANY valid strategy: replies must
echo the request id, return an action_id from the current request's legal
actions, and return a JSON checkpoint within the 1 MiB bound. They never
require a specific card choice, a keep-vs-play decision, or an unchanged
checkpoint -- a strategy that picks another legal card, keeps a drawn card,
or updates its memory passes.

Usage:
    python3 tools/check.py --agent python [--root DIR]
    python3 tools/check.py --agent ts [--root DIR]

Exit code is 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import queue
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

PROTOCOL_VERSION = "1.0"

JsonDict = dict[str, Any]
CheckResult = tuple[str, bool, str]


# ---------------------------------------------------------------------------
# fixtures: scripted organizer messages
# ---------------------------------------------------------------------------

def _hand(refs_kinds: list[tuple[str | None, str, int | None]]) -> list[JsonDict]:
    """Build a fixture hand from (color, kind, value) triples."""
    out = []
    for i, (color, kind, value) in enumerate(refs_kinds):
        out.append({"card_ref": f"h{i}", "color": color, "kind": kind,
                    "value": value})
    return out


def _observation(phase: str, legal: list[JsonDict], seat: int = 0,
                 agent_extra: JsonDict | None = None) -> JsonDict:
    """Build a fixture observation for the given phase."""
    obs: JsonDict = {
        "seat": seat,
        "phase": phase,
        "turn_number": 7,
        "turns_completed": 6,
        "direction": 1,
        "active_color": "red",
        "hand": _hand([("red", "number", 3), ("blue", "number", 5),
                       (None, "wild", None)]),
        "hand_sizes": [3, 5, 4, 6],
        "top_discard": {"color": "red", "kind": "number", "value": 9},
        "draw_pile_size": 60,
        "discard_pile_size": 12,
        "history": [{"type": "game_start", "turn_number": 0, "seat": None,
                     "hand_sizes": [7, 7, 7, 7]}],
        "private_events": [],
        "drawn_card_ref": None,
        "legal_actions": legal,
        "finished_order": [],
        "is_finished": False,
    }
    if agent_extra:
        obs.update(agent_extra)
    return obs


def fixtures(game_id: str = "smoke-1") -> dict[str, JsonDict]:
    """Return scripted organizer messages for the smoke lifecycle."""
    turn_legal: list[JsonDict] = [
        {"action_id": "a0", "type": "play", "card_ref": "h0",
         "card": {"color": "red", "kind": "number", "value": 3}},
        {"action_id": "a1", "type": "play", "card_ref": "h2",
         "card": {"color": None, "kind": "wild", "value": None},
         "color": "blue"},
        {"action_id": "a2", "type": "draw"},
    ]
    draw_legal: list[JsonDict] = [{"action_id": "a0", "type": "draw"}]
    color_legal: list[JsonDict] = [
        {"action_id": "a0", "type": "choose_color", "color": "red"},
        {"action_id": "a1", "type": "choose_color", "color": "blue"},
    ]
    return {
        "initialize": {
            "type": "initialize", "protocol_version": PROTOCOL_VERSION,
            "request_id": f"{game_id}-d0", "game_id": game_id, "seat": 0,
            "ruleset": "triad-uno-v1", "config": {"max_turns": 1000, "players": 4, "initial_hand_size": 7, "position_points": [5, 3, 2, 1]},
        },
        "turn": {
            "type": "act", "protocol_version": PROTOCOL_VERSION,
            "request_id": f"{game_id}-d1", "game_id": game_id,
            "observation": _observation("TURN", turn_legal),
            "agent_state": {"n": 1},
        },
        "turn_draw_only": {
            "type": "act", "protocol_version": PROTOCOL_VERSION,
            "request_id": f"{game_id}-d2", "game_id": game_id,
            "observation": _observation("TURN", draw_legal),
            "agent_state": None,
        },
        "after_draw": {
            "type": "act", "protocol_version": PROTOCOL_VERSION,
            "request_id": f"{game_id}-d3", "game_id": game_id,
            "observation": _observation(
                "AFTER_DRAW",
                [{"action_id": "a0", "type": "keep"},
                 {"action_id": "a1", "type": "play", "card_ref": "h1",
                  "card": {"color": "blue", "kind": "number", "value": 5}}],
                agent_extra={"drawn_card_ref": "h1"}),
            "agent_state": {"n": 2},
        },
        "choose_color": {
            "type": "act", "protocol_version": PROTOCOL_VERSION,
            "request_id": f"{game_id}-d4", "game_id": game_id,
            "observation": _observation(
                "CHOOSE_INITIAL_COLOR", color_legal),
            "agent_state": None,
        },
        "restart_turn": {
            "type": "act", "protocol_version": PROTOCOL_VERSION,
            "request_id": f"{game_id}-d5", "game_id": game_id,
            "observation": _observation("TURN", turn_legal),
            "agent_state": {"n": 2},
        },
        "game_over": {
            "type": "game_over", "protocol_version": PROTOCOL_VERSION,
            "request_id": f"{game_id}-d9", "game_id": game_id,
            "outcome": {"placements": [0, 1, 2, 3], "is_draw": False,
                        "unfinished": [], "reason": "all_placed"},
            "final_observation": _observation("FINISHED", []),
        },
    }


# ---------------------------------------------------------------------------
# session driver
# ---------------------------------------------------------------------------

class CheckFailure(Exception):
    """Smoke-check failure carrying a human-readable reason."""


MAX_CHECKPOINT = 1024 * 1024  # 1 MiB serialized agent_state

# Realistic harness faults: reported as a failed check instead of aborting
# the remaining checks. Anything else propagates (a bug we want to see).
HARNESS_ERRORS = (AssertionError, OSError, ValueError, TypeError, KeyError,
                  IndexError)


def assert_legal_reply(reply: JsonDict, request: JsonDict) -> str:
    """Accept any protocol-correct answer to an act request.

    The action_id must come from THIS request's legal actions (any of them:
    another legal play, a keep, a colour choice are all fine), and the
    checkpoint must be JSON within the size bound (any value, including an
    updated memory -- echoing is not required).
    """
    legal_ids = {a["action_id"] for a in
                 request["observation"]["legal_actions"]}
    if reply.get("action_id") not in legal_ids:
        raise CheckFailure(f"illegal action {reply.get('action_id')!r}; "
                           f"legal: {sorted(legal_ids)}")
    state = reply.get("agent_state")
    if state is not None:
        try:
            size = len(json.dumps(state).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise CheckFailure(
                "checkpoint is not JSON-serializable") from exc
        if size > MAX_CHECKPOINT:
            raise CheckFailure("checkpoint exceeds 1 MiB")
    return cast(str, reply["action_id"])


class Session:
    """One agent process plus a background stdout reader."""

    def __init__(self, proc: subprocess.Popen[str]) -> None:
        """Wrap a started agent process and its background readers."""
        self.proc = proc
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stderr = bytearray()
        self.stdout_lines: list[str] = []
        self._reader: threading.Thread | None = None
        self._drainer: threading.Thread | None = None

    def start(self) -> None:
        """Start the stdout/stderr reader threads."""
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._drainer = threading.Thread(target=self._drain_stderr, daemon=True)
        self._reader.start()
        self._drainer.start()

    def _read_loop(self) -> None:
        """Forward agent stdout lines into the reply queue."""
        stdout = self.proc.stdout
        if stdout is None:
            self._queue.put(None)
            return
        try:
            for raw in stdout:
                self.stdout_lines.append(raw)
                self._queue.put(raw)
        except (OSError, ValueError):
            pass  # pipes closed by kill(); remaining lines already queued
        finally:
            self._queue.put(None)

    def _drain_stderr(self) -> None:
        """Drain agent stderr into the retained buffer."""
        # Line iteration (not read(N)): each flushed log line must land in the
        # buffer promptly, while the process is still alive.
        stderr = self.proc.stderr
        if stderr is None:
            return
        try:
            for chunk in stderr:
                self._stderr.extend(chunk.encode("utf-8", "replace"))
        except (OSError, ValueError):
            pass

    @property
    def stderr_text(self) -> str:
        """Return buffered agent stderr as text."""
        return bytes(self._stderr).decode("utf-8", "replace")

    def send(self, msg: JsonDict) -> None:
        """Send one JSON message to the agent."""
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def send_raw(self, text: str) -> None:
        """Send raw text to the agent (for malformed-input checks)."""
        assert self.proc.stdin is not None
        self.proc.stdin.write(text)
        self.proc.stdin.flush()

    def recv(self, timeout: float = 10.0) -> JsonDict:
        """Receive one protocol JSON reply from the agent."""
        try:
            raw = self._queue.get(timeout=timeout)
        except queue.Empty as exc:
            raise CheckFailure("timed out waiting for agent reply") from exc
        if raw is None:
            raise CheckFailure("agent closed stdout without replying")
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CheckFailure(
                f"stdout is not protocol JSON: {raw.strip()[:120]}") from exc
        if not isinstance(msg, dict):
            raise CheckFailure(f"stdout is not a protocol object: {msg!r}")
        return msg

    def expect(self, mtype: str, request_id: str, timeout: float = 10.0) -> JsonDict:
        """Receive a reply and check its type and request id."""
        msg = self.recv(timeout)
        if msg.get("type") != mtype:
            raise CheckFailure(f"expected {mtype}, got {msg}")
        if msg.get("request_id") != request_id:
            raise CheckFailure(f"request id not echoed: {msg}")
        return msg

    def expect_no_reply(self, timeout: float = 1.0) -> None:
        """Assert the agent stays silent (e.g. after malformed input)."""
        try:
            raw = self._queue.get(timeout=timeout)
        except queue.Empty:
            return
        raise CheckFailure(f"agent replied to malformed input: {raw!r}"[:200])

    def kill(self) -> None:
        """Close agent pipes and terminate the process."""
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    def wait_exit(self, timeout: float = 10.0) -> int:
        """Wait for the agent process to exit."""
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise CheckFailure("agent did not exit after stdin closed") from exc


@contextlib.contextmanager
def open_session(cmd: list[str], cwd: str | None) -> Iterator[Session]:
    """Start an agent process and yield a live Session; kill it on exit."""
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1, cwd=cwd)
    session = Session(proc)
    session.start()
    try:
        yield session
    finally:
        session.kill()


# ---------------------------------------------------------------------------
# the smoke lifecycle
# ---------------------------------------------------------------------------

class SmokeCheck:
    """Drive one agent through every scripted protocol lifecycle check."""

    def __init__(self, cmd: list[str], cwd: str | None, label: str) -> None:
        """Prepare the fixtures and session bookkeeping for one agent."""
        self.cmd = cmd
        self.cwd = cwd
        self.label = label
        self.fx = fixtures()
        self.held: dict[str, Session] = {}
        self.sessions: list[Session] = []
        self._stack = contextlib.ExitStack()

    @property
    def session(self) -> Session:
        """The currently active agent session."""
        return self.held["s"]

    def spawn(self) -> Session:
        """Start a fresh agent session and remember it."""
        session = self._stack.enter_context(open_session(self.cmd, self.cwd))
        self.sessions.append(session)
        self.held["s"] = session
        return session

    def check_initialize(self) -> str:
        """Verify the initialize -> ready handshake."""
        self.spawn()
        self.session.send(self.fx["initialize"])
        reply = self.session.expect("ready", self.fx["initialize"]["request_id"])
        assert "agent_state" in reply, "ready must carry agent_state"
        return f"ready echoes request id ({self.label})"

    def check_turn_answers_legally(self) -> str:
        """Verify a TURN act is answered with a legal action."""
        request = self.fx["turn"]
        self.session.send(request)
        reply = self.session.expect("action", request["request_id"])
        aid = assert_legal_reply(reply, request)
        return f"legal action {aid} with valid checkpoint"

    def check_malformed_input_ignored(self) -> str:
        """Verify malformed input is logged and ignored, not answered."""
        self.session.send_raw("this is not json\n")
        self.session.expect_no_reply()
        if "ignoring non-JSON" not in self.session.stderr_text:
            raise CheckFailure("malformed input was not logged to stderr")
        # The session must still be usable afterwards.
        request = self.fx["turn_draw_only"]
        self.session.send(request)
        reply = self.session.expect("action", request["request_id"])
        aid = assert_legal_reply(reply, request)
        return f"no stdout reply; stderr logged; session survived ({aid})"

    def check_after_draw_answers_legally(self) -> str:
        """Verify an AFTER_DRAW act is answered with a legal action."""
        request = self.fx["after_draw"]
        self.session.send(request)
        reply = self.session.expect("action", request["request_id"])
        aid = assert_legal_reply(reply, request)
        return f"AFTER_DRAW answered legally ({aid})"

    def check_choose_color(self) -> str:
        """Verify a CHOOSE_INITIAL_COLOR act is answered legally."""
        request = self.fx["choose_color"]
        self.session.send(request)
        reply = self.session.expect("action", request["request_id"])
        aid = assert_legal_reply(reply, request)
        return f"initial colour answered legally ({aid})"

    def check_restart_from_checkpoint(self) -> str:
        """Verify a respawned agent answers from the checkpoint alone."""
        self.session.kill()
        self.spawn()
        self.session.send(self.fx["initialize"])
        self.session.expect("ready", self.fx["initialize"]["request_id"])
        request = self.fx["restart_turn"]
        self.session.send(request)
        reply = self.session.expect("action", request["request_id"])
        aid = assert_legal_reply(reply, request)
        return f"respawned agent answers from checkpoint alone ({aid})"

    def check_game_over(self) -> str:
        """Verify game_over is acknowledged and the same process accepts a new game."""
        request = self.fx["game_over"]
        self.session.send(request)
        self.session.expect("done", request["request_id"])
        next_init = dict(self.fx["initialize"], game_id="next-game", request_id="next-init")
        self.session.send(next_init)
        self.session.expect("ready", "next-init")
        next_act = dict(self.fx["turn"], game_id="next-game", request_id="next-act", agent_state=None)
        self.session.send(next_act)
        self.session.expect("action", "next-act")
        if self.session.proc.stdin is not None:
            self.session.proc.stdin.close()
        self.session.wait_exit()
        return "done acknowledged; next game served by same process; exits on EOF"

    def check_stdout_discipline(self) -> str:
        """Verify every stdout line is a well-formed protocol message."""
        lines = [raw for session in self.sessions
                 for raw in session.stdout_lines]
        for raw in lines:
            msg = json.loads(raw)
            assert msg.get("type") in ("ready", "action", "done"), msg
            assert isinstance(msg.get("request_id"), str), msg
        return f"{len(lines)} stdout lines, all protocol JSON"

    def run(self) -> list[CheckResult]:
        """Run every check; return a list of (name, ok, detail)."""
        checks: list[tuple[str, Callable[[], str]]] = [
            ("initialize", self.check_initialize),
            ("turn-answers-legally", self.check_turn_answers_legally),
            ("malformed-input", self.check_malformed_input_ignored),
            ("after-draw", self.check_after_draw_answers_legally),
            ("choose-color", self.check_choose_color),
            ("restart", self.check_restart_from_checkpoint),
            ("game-over", self.check_game_over),
            ("stdout-discipline", self.check_stdout_discipline),
        ]
        results: list[CheckResult] = []
        try:
            for name, func in checks:
                results.append(self._record(name, func))
        finally:
            if "s" in self.held:
                self.session.kill()
            self._stack.close()
        return results

    @staticmethod
    def _record(name: str, func: Callable[[], str]) -> CheckResult:
        """Run one check and record its pass/fail detail."""
        try:
            return (name, True, func() or "")
        except CheckFailure as exc:
            return (name, False, str(exc))
        except HARNESS_ERRORS as exc:
            return (name, False, f"harness error: {exc}")


def check_agent(cmd: list[str], cwd: str | None, label: str) -> list[CheckResult]:
    """Run every check; return a list of (name, ok, detail)."""
    return SmokeCheck(cmd, cwd, label).run()


def agent_command(kind: str, root: str, agent_path: str | None = None) -> list[str]:
    """Return the subprocess command for the requested agent starter."""
    if agent_path:
        path = Path(agent_path)
        if not path.is_absolute():
            path = Path(root) / path
        if kind == "ts":
            node = shutil.which("node")
            if node is None:
                raise CheckFailure("node not installed")
            return [node, str(path)]
        return [sys.executable, str(path)]
    if kind == "python":
        return [sys.executable, str(Path(root) / "src" / "agent.py")]
    if kind == "ts":
        node = shutil.which("node")
        if node is None:
            raise CheckFailure("node not installed")
        # Swapped layouts keep only src/agent.ts; otherwise use the template.
        swapped = Path(root) / "src" / "agent.ts"
        if swapped.exists():
            return [node, str(swapped)]
        return [node, str(Path(root) / "templates" / "typescript" / "src" / "agent.ts")]
    raise CheckFailure(f"unknown agent kind {kind!r}")


def main(argv: list[str] | None = None) -> int:
    """Run the protocol smoke check from the command line."""
    ap = argparse.ArgumentParser(description="unoSDK protocol smoke check")
    ap.add_argument("--agent", choices=["python", "ts"], default="python")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--agent-path", default=None,
                    help="agent script path relative to --root "
                         "(default: src/agent.py, or the JS template)")
    args = ap.parse_args(argv)
    try:
        cmd = agent_command(args.agent, args.root, args.agent_path)
    except CheckFailure as exc:
        print(f"SMOKE SKIP ({args.agent}): {exc}")
        return 2
    results = check_agent(cmd, args.cwd or args.root, args.agent)
    failed = 0
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        failed += 0 if ok else 1
    if failed:
        print(f"SMOKE FAILED ({args.agent}): {failed} failing checks")
        return 1
    print(f"SMOKE PASSED ({args.agent}): {len(results)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
