"""Shared subprocess protocol session for Triad UNO agents.

Both the local development runner (:mod:`triad_uno.local_runner`) and the
Phase 3 container match worker (:mod:`triad_uno.match_worker`) speak the same
newline-JSON protocol (``initialize`` → ``act*`` → ``game_over``) over a
child's stdio, enforce request-id matching, legal-action checks, the 1 MiB
checkpoint bound, and decision timeouts, and apply the deterministic ``a0``
fallback on any fault.

This module owns the shared trust boundary so the two runners cannot drift:

- per-seat private broadcast (own hand + counts + played card only),
- request-scoped ``a0..`` action ids,
- generation-bound I/O (a late line from a killed session can never leak
  into its replacement's queue),
- restart-safe checkpoints (a restart's ``initialize`` response never
  replaces the last accepted checkpoint),
- deadline-bound writes (a blocked child cannot stall the organizer),
- guaranteed cleanup (terminate → killpg → reap I/O threads).
"""

from __future__ import annotations

import contextlib
import os
import queue
import select
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NotRequired, Protocol, TypedDict, TypeVar, Unpack, cast

from . import engine, protocol
from .engine import EngineAction, GameState, Transition

if TYPE_CHECKING:
    from collections.abc import Callable

JsonDict = dict[str, Any]
Checkpoint = Any
_T = TypeVar("_T")


def _require(value: _T | None, message: str) -> _T:
    """Return a required value, raising a fixed assertion when it is absent."""
    if value is None:
        raise AssertionError(message)
    return value


class BaselineAgent(Protocol):
    """In-process fallback opponent used for non-protocol seats."""

    def select(self, observation: JsonDict) -> str:
        """Return a request-scoped action id (``a0..``) for an observation."""
        ...  # pragma: no cover - explicit Protocol stub


class _HasFileno(Protocol):
    """Stream exposing a file descriptor for deadline-bounded writes."""

    def fileno(self) -> int:
        """Return the stream's file descriptor."""
        ...


class _ReadablePipe(Protocol):
    """Readable stream retained for post-game stderr diagnostics."""

    def read(self, size: int = -1) -> bytes | str:
        """Read up to ``size`` bytes or characters."""
        ...


CwdType = str | os.PathLike[str] | None
EnvType = dict[str, str] | None


class SessionOptions(TypedDict, total=False):
    """Optional process configuration for a protocol session."""

    cwd: CwdType
    env: EnvType
    text: bool
    bufsize: int


class _ActOptions(TypedDict):
    """Keyword fields of one act request."""

    legal_ids: set[str]
    timeout: float
    game_id: str
    deadline: NotRequired[float | None]
    started_at: NotRequired[float | None]


KEEP: object = object()  # sentinel: checkpoint unchanged
OUTPUT_LIMIT: object = object()  # sentinel: peer exceeded the line bound
MAX_QUEUED_LINES = 64
READ_CHUNK = 8192


def write_with_deadline(stream: _HasFileno, payload: bytes, deadline: float | None) -> str | None:
    """Write to a child pipe without letting a blocked child stall the organizer.

    Returns an error string on timeout/broken pipe, else None.
    """
    if os.name == "nt":
        done = threading.Event()
        errors = []
        def write():
            try:
                data = memoryview(payload)
                while data:
                    count = os.write(stream.fileno(), data)
                    if not count:
                        raise BrokenPipeError()
                    data = data[count:]
            except (OSError, ValueError):
                errors.append("process pipe broken")
            finally:
                done.set()
        threading.Thread(target=write, daemon=True).start()
        remaining = None if deadline is None else max(0., deadline - time.monotonic())
        if not done.wait(remaining):
            return "timeout"
        return errors[0] if errors else None
    try:
        fd = stream.fileno()
        was_blocking = os.get_blocking(fd)
    except (OSError, ValueError):
        return "process pipe broken"
    os.set_blocking(fd, False)
    try:
        offset = 0
        while offset < len(payload):
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return "timeout"
            try:
                _, writable, _ = select.select([], [fd], [], remaining)
            except (OSError, ValueError):
                return "process pipe broken"
            if not writable:
                return "timeout"
            try:
                offset += os.write(fd, payload[offset:])
            except BlockingIOError:
                continue
            except (BrokenPipeError, OSError, ValueError):
                return "process pipe broken"
    finally:
        with contextlib.suppress(OSError):
            os.set_blocking(fd, was_blocking)
    return None


def build_outcome(state: GameState) -> JsonDict:
    """Build the organizer-side outcome dict from finished engine state."""
    return {
        "placements": list(state.outcome.placements if state.outcome else []),
        "is_draw": bool(state.outcome.is_draw) if state.outcome else True,
        "unfinished": list(state.outcome.unfinished if state.outcome else []),
        "reason": state.outcome.reason if state.outcome else "unknown",
    }


def baseline_action(state: GameState, seat: int, agent: BaselineAgent) -> EngineAction:
    """Select an in-process baseline action with deterministic fallback."""
    obs = engine.observe(state, seat)
    action: EngineAction | None = None
    with contextlib.suppress(Exception):
        aid = agent.select(obs)
        action = engine.engine_action_from_id(state, seat, aid)
    if action is None:
        action = engine.fallback_action(state)
    if action is None:
        msg = "no legal action in non-terminal state"
        raise AssertionError(msg)
    return action


def maybe_accept_checkpoint(checkpoints: dict[int, Any], seat: int, checkpoint: object) -> None:
    """Record an initialize checkpoint unless one is already accepted.

    A restart's ``initialize`` response must never replace the last accepted
    checkpoint: agents commonly return a fresh initial checkpoint from
    ``initialize``; accepting it after a crash would silently discard the
    recovered game memory the restart contract promises to preserve.
    """
    if checkpoint is not KEEP and seat not in checkpoints:
        checkpoints[seat] = checkpoint


@dataclass
class GameStats:
    """Per-game fault/latency/checkpoint accounting shared by both runners."""

    checkpoints: dict[int, Any] = field(default_factory=dict)
    fallbacks: dict[int, int] = field(default_factory=lambda: dict.fromkeys(range(4), 0))
    restarts: dict[int, int] = field(default_factory=lambda: dict.fromkeys(range(4), 0))
    faults: list[JsonDict] = field(default_factory=list)
    latencies: dict[int, list[float]] = field(default_factory=lambda: {s: [] for s in range(4)})
    decisions: dict[int, int] = field(default_factory=lambda: dict.fromkeys(range(4), 0))
    req_counter: int = 0

    def next_rid(self, game_id: str) -> str:
        """Mint the next request id for a game."""
        self.req_counter += 1
        return protocol.make_request_id(game_id, self.req_counter)

    def record_fault(self, seat: int, stage: str, error: str) -> None:
        """Append a fixed-string fault entry (never echoes hidden state)."""
        self.faults.append({"seat": seat, "stage": stage, "error": error})


class ProcSession:
    """One agent execution (one container/process lifetime).

    Generation-bound: the reader thread captures its session's queue at
    start, so a late line from a killed session can never appear in the
    replacement session's queue. Combined with request-id echo checks and
    kill-before-respawn, repeated timeouts cannot create overlapping
    executions for one seat.
    """

    def __init__(
        self,
        cmd: list[str],
        game_id: str,
        seat: int,
        backend: str = "subprocess",
        **options: Unpack[SessionOptions],
    ) -> None:
        """Create (but do not start) a session for one seat."""
        self.cmd = cmd
        self.game_id = game_id
        self.seat = seat
        self.backend = backend
        self.cwd = options.get("cwd")
        self.env = options.get("env")
        self._text = options.get("text", False)
        self._bufsize = options.get("bufsize", 0)
        self.proc: subprocess.Popen[Any] | None = None
        self._lines: queue.Queue[Any] = queue.Queue()
        self._stderr_buf = bytearray()
        self._reader: threading.Thread | None = None
        self._drainer: threading.Thread | None = None
        self.alive = False
        self._gen = 0
        self._cleanup_error: str | None = None
        self._output_overflow = False
        self.container_name = f"triad-uno-{game_id}-s{seat}"

    def spawn(
        self, request_id: str, max_turns: int, timeout: float, deadline: float | None = None,
        config: JsonDict | None = None,
    ) -> tuple[object, str | None]:
        """Start the process and run the initialize handshake."""
        self._gen += 1
        self._cleanup_locked()
        if self.proc is not None or self._cleanup_error is not None:
            return KEEP, self._cleanup_error or "previous process did not exit"
        self._lines = queue.Queue(maxsize=MAX_QUEUED_LINES)
        self._output_overflow = False
        popen_error = self._launch_process()
        if popen_error is not None:
            return KEEP, popen_error
        self.alive = True
        if self.proc is None:
            msg = "process missing after successful launch"
            raise AssertionError(msg)
        stdout, stderr = self.proc.stdout, self.proc.stderr
        if stdout is None or stderr is None:
            msg = "process pipes missing after successful launch"
            raise AssertionError(msg)
        self._start_io_threads(stdout, stderr)
        return self._initialize_handshake(request_id, max_turns, timeout, deadline, config)

    def begin_game(
        self, game_id: str, seat: int, request_id: str, max_turns: int, timeout: float
    ) -> tuple[object, str | None]:
        """Reinitialize a healthy execution without discarding loaded weights."""
        self.game_id = game_id
        self.seat = seat
        self._stderr_buf.clear()
        if not self.alive or self.proc is None or self.proc.poll() is not None:
            self.kill()
            return KEEP, self._cleanup_error or "process exited"
        # Keep the same reader queue: unsolicited/stale replies must be rejected,
        # never silently discarded when entering the next game.
        return self._initialize_handshake(request_id, max_turns, timeout, None)

    def request_act(
        self,
        observation: JsonDict,
        checkpoint: object,
        request_id: str,
        **options: Unpack[_ActOptions],
    ) -> tuple[str | None, object, str | None, float]:
        """Send one act request.

        Returns (action_id|None, checkpoint|KEEP, fault|None, latency).
        """
        legal_ids = options["legal_ids"]
        timeout = options["timeout"]
        game_id = options["game_id"]
        deadline = options.get("deadline")
        started_at = options.get("started_at")
        if not self.alive:
            return None, KEEP, "process not alive", 0.0
        msg = protocol.build_act(game_id, request_id, observation, checkpoint)
        t0 = started_at if started_at is not None else time.monotonic()
        deadline = deadline if deadline is not None else t0 + timeout
        send_error = self._send(msg, deadline=deadline)
        if send_error is not None:
            self.kill()
            return None, KEEP, send_error, time.monotonic() - t0
        reply, fault = self._recv(deadline=deadline)
        latency = time.monotonic() - t0
        if fault is not None or reply is None:
            self.kill()
            return None, KEEP, fault or "process exited", latency
        response_error = protocol.validate_agent_response(reply, "action", request_id, legal_ids)
        if response_error is not None:
            self.kill()
            return None, KEEP, response_error, latency
        new_state = reply.get("agent_state")
        action_id = reply["action_id"]
        if not isinstance(action_id, str):
            err = "agent action id must be a string"
            raise TypeError(err)
        return action_id, (new_state if new_state is not None else KEEP), None, latency

    def notify_game_over(
        self,
        outcome: JsonDict,
        final_obs: JsonDict,
        request_id: str,
        game_id: str,
        timeout: float = 2.0,
    ) -> None:
        """Best-effort game_over notification; faults kill the session."""
        if not self.alive:
            return
        deadline = time.monotonic() + timeout
        if (
            self._send(
                protocol.build_game_over(game_id, request_id, outcome, final_obs),
                deadline=deadline,
            )
            is not None
        ):
            self.kill()
            return
        reply, fault = self._recv(deadline=deadline)
        # Validate type AND request id: a stale/late 'done' from a previous
        # request must not count as acknowledgement.
        if (
            fault is not None
            or not isinstance(reply, dict)
            or protocol.validate_agent_response(reply, "done", request_id) is not None
        ):
            self.kill()

    def kill(self) -> None:
        """Mark dead and reap the process plus I/O threads."""
        self.alive = False
        self._cleanup_locked()

    @property
    def stderr_text(self) -> str:
        """Return retained stderr (up to the per-game bound) as text."""
        return bytes(self._stderr_buf[-65536:]).decode("utf-8", "replace")

    @property
    def cleanup_error(self) -> str | None:
        """Return the sticky cleanup error, if cleanup ever failed."""
        return self._cleanup_error

    def is_process_gone(self) -> bool:
        """Return True when no child process remains."""
        return self.proc is None or self.proc.poll() is not None

    def _launch_process(self) -> str | None:
        """Spawn the child process; return an error string on failure."""
        # Lifetime spans many decisions plus restarts; cleanup owns reaping.
        try:
            self.proc = subprocess.Popen(
                self.cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=self._text,
                bufsize=self._bufsize,
                start_new_session=True,
                cwd=self.cwd,
                env=self.env,
            )
        except OSError as exc:
            return f"spawn failed: {exc.strerror or exc}"
        return None

    def _initialize_handshake(
        self, request_id: str, max_turns: int, timeout: float, deadline: float | None,
        config: JsonDict | None = None,
    ) -> tuple[object, str | None]:
        """Run initialize → ready and return (checkpoint|KEEP, fault|None)."""
        msg = protocol.build_initialize(self.game_id, self.seat, request_id, max_turns, config=config)
        deadline = deadline if deadline is not None else time.monotonic() + timeout
        send_error = self._send(msg, deadline=deadline)
        if send_error is not None:
            self.kill()
            return KEEP, send_error
        reply, fault = self._recv(deadline=deadline)
        if fault is not None or reply is None:
            self.kill()
            return KEEP, fault or "process exited"
        response_error = protocol.validate_agent_response(reply, "ready", request_id)
        if response_error is not None:
            self.kill()
            return KEEP, response_error
        state = reply.get("agent_state")
        return (state if state is not None else KEEP), None

    def _start_io_threads(self, out: _HasFileno, err: _ReadablePipe) -> None:
        """Start generation-bound stdout reader and stderr drainer threads."""
        # Bind the queue at thread start so late output from a killed
        # generation can never leak into its replacement's queue.
        current_queue = self._lines
        stderr_buf = self._stderr_buf
        self._reader = threading.Thread(
            target=self._read_stdout, args=(out, current_queue), daemon=True
        )
        self._drainer = threading.Thread(
            target=self._read_stderr, args=(err, stderr_buf), daemon=True
        )
        self._reader.start()
        self._drainer.start()

    def _enqueue(self, current_queue: queue.Queue[Any], item: object) -> bool:
        """Queue one line; collapse overflow to a single limit sentinel."""
        try:
            current_queue.put_nowait(item)
        except queue.Full:
            self._output_overflow = True
            with contextlib.suppress(queue.Full):
                current_queue.put_nowait(OUTPUT_LIMIT)
            return False
        return True

    def _read_stdout(self, out: _HasFileno, current_queue: queue.Queue[Any]) -> None:
        """Pump framed stdout lines into the generation-bound queue."""
        pending = b""
        try:
            while True:
                chunk = os.read(out.fileno(), READ_CHUNK)
                if not chunk:
                    break
                pending += chunk
                if b"\n" not in pending and len(pending) > protocol.MAX_PROTOCOL_LINE:
                    self._enqueue(current_queue, OUTPUT_LIMIT)
                    return
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    if len(line) + 1 > protocol.MAX_PROTOCOL_LINE:
                        self._enqueue(current_queue, OUTPUT_LIMIT)
                        return
                    if not self._enqueue(current_queue, line + b"\n"):
                        return
            if pending:
                if len(pending) > protocol.MAX_PROTOCOL_LINE:
                    self._enqueue(current_queue, OUTPUT_LIMIT)
                else:
                    self._enqueue(current_queue, pending)
        finally:
            self._enqueue(current_queue, None)

    def _read_stderr(self, err: _ReadablePipe, stderr_buf: bytearray) -> None:
        """Retain bounded stderr for post-game diagnostics."""
        with contextlib.suppress(OSError, ValueError):
            while True:
                chunk = err.read(65536)
                if not chunk:
                    break
                data = chunk if isinstance(chunk, bytes) else chunk.encode("utf-8", "replace")
                stderr_buf.extend(data)
                if len(stderr_buf) > protocol.MAX_STDERR_PER_GAME:
                    del stderr_buf[: len(stderr_buf) - protocol.MAX_STDERR_PER_GAME]

    def _send(self, msg: JsonDict, deadline: float | None = None) -> str | None:
        """Serialize and deadline-write one organizer message."""
        if self.proc is None or self.proc.stdin is None:
            err = "process stdin unavailable"
            raise AssertionError(err)
        try:
            line = protocol.dumps_line(msg)
        except ValueError:
            return "organizer message exceeds line limit"
        return write_with_deadline(self.proc.stdin, (line + "\n").encode("utf-8"), deadline)

    def _recv(
        self, timeout: float | None = None, deadline: float | None = None
    ) -> tuple[JsonDict | None, str | None]:
        """Receive one agent line, enforcing output limits and deadlines."""
        wait = timeout
        if deadline is not None:
            wait = max(0.0, deadline - time.monotonic())
        try:
            raw = self._lines.get(timeout=wait)
        except queue.Empty:
            if self.proc is not None and self.proc.poll() is not None:
                return None, "process exited"
            return None, "timeout"
        if raw is None:  # EOF sentinel from reader
            return None, "process exited"
        if raw is OUTPUT_LIMIT:
            return None, "response exceeds line limit"
        return protocol.parse_line(raw)

    def _cleanup_locked(self) -> None:
        """Terminate, SIGKILL the process group if needed, and reap I/O threads."""
        proc = self.proc
        reader, self._reader = self._reader, None
        drainer, self._drainer = self._drainer, None
        if proc is None:
            return
        self._cleanup_error = None
        with contextlib.suppress(OSError, ValueError):
            if proc.stdin is not None:
                proc.stdin.close()
        self._terminate_process(proc)
        if proc.poll() is None:
            self._cleanup_error = self._cleanup_error or "process remained alive after cleanup"
            self.proc = proc
        else:
            self.proc = None
        for thread in (reader, drainer):
            if thread is not None and thread.is_alive():
                thread.join(timeout=1)
        if any(t is not None and t.is_alive() for t in (reader, drainer)):
            self._cleanup_error = self._cleanup_error or "I/O reader did not exit"
        self._extra_cleanup()

    def _terminate_process(self, proc: subprocess.Popen[Any]) -> None:
        """Stop the child and always kill its process group before returning."""
        with contextlib.suppress(OSError):
            proc.terminate()
        self._wait_for_process(proc, report_timeout=False)
        self._kill_process_group(proc)
        self._wait_for_process(proc, report_timeout=True)

    def _wait_for_process(self, proc: subprocess.Popen[Any], *, report_timeout: bool) -> None:
        """Wait for a process and record only failures that cleanup cannot fix."""
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            if report_timeout:
                self._cleanup_error = self._cleanup_error or "process remained alive after kill"
        except OSError:
            self._cleanup_error = self._cleanup_error or "failed to terminate process"

    def _kill_process_group(self, proc: subprocess.Popen[Any]) -> None:
        """Kill the session group, falling back to the parent process if needed."""
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            elif proc.poll() is None:
                proc.kill()
        except ProcessLookupError:
            return  # The whole group is already gone.
        except OSError:
            if proc.poll() is None:
                with contextlib.suppress(OSError):
                    proc.kill()
            if proc.poll() is None:
                self._cleanup_error = self._cleanup_error or "failed to terminate process"

    def _extra_cleanup(self) -> None:
        """Backend-specific cleanup hook (Docker container removal)."""
        if self.backend != "docker":
            return
        exe = shutil.which("docker")
        if exe is None:
            self._cleanup_error = self._cleanup_error or "docker container cleanup failed"
            return
        try:
            completed = subprocess.run(
                [exe, "rm", "-f", self.container_name],
                capture_output=True,
                timeout=5,
                check=False,
            )
            if completed.returncode and b"No such container" not in completed.stderr:
                self._cleanup_error = self._cleanup_error or "docker container cleanup failed"
        except (OSError, subprocess.SubprocessError):
            self._cleanup_error = self._cleanup_error or "docker container cleanup failed"


def run_initial_handshakes(
    protocol_seats: set[int],
    ensure_spawn: Callable[..., bool],
    stats: GameStats,
) -> None:
    """Run sequential initial handshakes, recording retryable faults."""
    for seat in protocol_seats:
        if not ensure_spawn(seat):
            stats.record_fault(seat, "initialize-retry", "will retry on first decision")


def current_decision(state: GameState) -> tuple[int, JsonDict, set[str]]:
    """Return the current (seat, observation, legal action ids) triple."""
    seat = state.current_seat
    obs = engine.observe(state, seat)
    legal_actions = obs["legal_actions"]
    legal_ids = (
        {cast("JsonDict", a)["action_id"] for a in legal_actions}
        if isinstance(legal_actions, list)
        else set()
    )
    return seat, obs, legal_ids


def record_spawn_result(stats: GameStats, seat: int, checkpoint: object, fault: str | None) -> bool:
    """Record an ensure_spawn outcome; return False when the spawn failed."""
    if fault is not None:
        stats.record_fault(seat, "initialize", fault)
        return False
    maybe_accept_checkpoint(stats.checkpoints, seat, checkpoint)
    return True


def apply_fallback_keep(
    state: GameState,
    action: EngineAction,
    transition: Transition,
    *,
    fallback_used: bool,
) -> None:
    """End a fallback no-play turn immediately with a keep.

    Fallback policy ends the turn even if the drawn card happens to be
    playable. Event extension is harmless for callers that discard the
    transition; state mutation (the keep) is what matters.
    """
    if fallback_used and action.type == "draw" and state.phase == engine.AFTER_DRAW:
        kept = engine.step(state, EngineAction(type="keep"))
        if not kept.ok:
            msg = f"fallback keep failed: {kept.error}"
            raise AssertionError(msg)
        with contextlib.suppress(AttributeError):
            transition.events.extend(kept.events)
            transition.outcome = kept.outcome


@dataclass
class DriveContext:
    """Shared per-game context threaded through the protocol game loop."""

    game_id: str
    protocol_seats: set[int]
    sessions: dict[int, ProcSession]
    stats: GameStats
    decision_timeout: float
    ensure_spawn: Callable[..., bool]
    baseline_fn: Callable[..., tuple[EngineAction, bool]]
    chaos_kill: dict[int, list[int]]
    step_guard: int
    guard_message: str


def decide_protocol_action(
    state: GameState,
    seat: int,
    obs: JsonDict,
    legal_ids: set[str],
    ctx: DriveContext,
) -> tuple[EngineAction, bool]:
    """Run one protocol decision with restart, deadline, fallback, and checkpoint rules.

    ``ctx.ensure_spawn`` is ``(seat, deadline) -> bool`` and already records
    its own initialize faults plus checkpoint acceptance. Returns
    (action, fallback_used). A restart plus request delivery plus response
    receipt share one absolute deadline, so all pieces must stay together here.
    """
    stats = ctx.stats
    decision_started = time.monotonic()
    decision_deadline = decision_started + ctx.decision_timeout
    stats.decisions[seat] += 1
    if stats.decisions[seat] in (ctx.chaos_kill.get(seat) or []):
        if seat in ctx.sessions:
            ctx.sessions[seat].kill()
        stats.record_fault(seat, "chaos-kill", "injected crash")
    sess = ctx.sessions.get(seat)
    if sess is None or not sess.alive:
        stats.restarts[seat] += 1
        if not ctx.ensure_spawn(seat, decision_deadline):
            stats.fallbacks[seat] += 1
            stats.record_fault(seat, "respawn-failed", "handshake failed")
            stats.latencies[seat].append(time.monotonic() - decision_started)
            fallback = _require(engine.fallback_action(state), "no fallback action available")
            return fallback, True
        sess = ctx.sessions[seat]
    sess = _require(sess, "session missing after spawn")
    checkpoint = stats.checkpoints.get(seat)
    aid, new_cp, fault, latency = sess.request_act(
        obs,
        checkpoint,
        stats.next_rid(ctx.game_id),
        legal_ids=legal_ids,
        timeout=ctx.decision_timeout,
        game_id=ctx.game_id,
        deadline=decision_deadline,
        started_at=decision_started,
    )
    stats.latencies[seat].append(latency)
    if fault is not None:
        stats.fallbacks[seat] += 1
        stats.record_fault(seat, "act", fault)
        fallback = _require(engine.fallback_action(state), "no fallback action available")
        return fallback, True
    resolved = engine.engine_action_from_id(state, seat, _require(aid, "missing action id"))
    action = _require(resolved, "validated action id did not resolve")
    if new_cp is not KEEP:
        stats.checkpoints[seat] = new_cp
    return action, False


def drive_game(state: GameState, ctx: DriveContext) -> None:
    """Drive ``state`` to FINISHED, mutating sessions/stats in place.

    ``ctx.ensure_spawn`` is ``(seat, deadline) -> bool``; ``ctx.baseline_fn``
    is ``(state, seat) -> (action, fallback_used)``. Raises RuntimeError when
    the step guard trips.
    """
    steps = 0
    while state.phase != engine.FINISHED:
        steps += 1
        if steps > ctx.step_guard:
            raise RuntimeError(ctx.guard_message)
        seat, obs, legal_ids = current_decision(state)
        fallback_used = False
        if seat in ctx.protocol_seats:
            action, fallback_used = decide_protocol_action(state, seat, obs, legal_ids, ctx)
        else:
            action, fallback_used = ctx.baseline_fn(state, seat)
        if action is None:
            msg = "decision produced no action"
            raise AssertionError(msg)
        transition = engine.step(state, action)
        if not transition.ok:
            msg = f"game produced invalid action: {transition.error}"
            raise AssertionError(msg)
        apply_fallback_keep(state, action, transition, fallback_used=fallback_used)
