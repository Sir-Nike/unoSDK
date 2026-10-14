"""Local subprocess runner: play protocol agents against in-process baselines.

This is the development precursor of the Phase 3 container match worker. It
speaks the same newline-JSON protocol (initialize → act* → game_over) over a
subprocess's stdio, enforces request-id matching, legal-action checks, the
1 MiB checkpoint bound, and decision timeouts, and applies the deterministic
``a0`` fallback on any fault. Crashed/timed-out agents are respawned on their
next decision with the last accepted checkpoint — the same restart path the
production evaluator uses.

Session I/O lives in :mod:`triad_uno.agent_session`; this module only owns
the local game loop (mixed subprocess + in-process seats).
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any, TypedDict, Unpack

from . import engine
from .agent_session import (
    DriveContext,
    GameStats,
    ProcSession,
    baseline_action,
    build_outcome,
    drive_game,
    record_spawn_result,
    run_initial_handshakes,
)
from .engine import EngineAction, GameConfig, GameState

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .agent_session import BaselineAgent, CwdType, EnvType

__all__ = ["ProcAgent", "run_protocol_game"]


class _RunOptions(TypedDict, total=False):
    """Optional keyword knobs for :func:`run_protocol_game`."""

    timeout: float
    init_timeout: float
    game_id: str | None
    chaos_kill: dict[int, list[int]] | None
    cwd: CwdType
    env: EnvType
    agent_cwds: dict[int, CwdType] | None
    agent_envs: dict[int, EnvType] | None


class ProcAgent(ProcSession):
    """One agent subprocess within one game (local text-mode session)."""

    def __init__(
        self,
        cmd: list[str],
        game_id: str,
        seat: int,
        cwd: CwdType = None,
        env: EnvType = None,
    ) -> None:
        """Create (but do not start) a local subprocess agent."""
        super().__init__(cmd, game_id, seat, backend="subprocess", cwd=cwd, env=env)


def _baseline_action_id(state: GameState, seat: int, agent: BaselineAgent) -> EngineAction:
    """Select a baseline action for one seat (thin compat wrapper)."""
    return baseline_action(state, seat, agent)


def run_protocol_game(
    seed: int,
    agent_cmds: dict[int, list[str]],
    baselines: Mapping[int, BaselineAgent] | list[BaselineAgent | None],
    config: GameConfig | None = None,
    **options: Unpack[_RunOptions],
) -> tuple[GameState, dict[str, Any]]:
    """Run one game mixing subprocess protocol agents and in-process baselines.

    ``agent_cmds`` maps seat → argv. ``baselines`` covers the other seats
    (dict seat → agent or list of 4 with None for subprocess seats).
    ``chaos_kill`` maps seat → list of per-seat decision numbers at which to
    kill the process before deciding (restart-path testing).
    ``agent_cwds``/``agent_envs`` optionally override ``cwd``/``env`` per
    seat, so submissions that load relative configuration or weights run
    from their own directory even when the harness runs elsewhere.
    """
    timeout = options.get("timeout", 5.0)
    init_timeout = options.get("init_timeout", 20.0)
    game_id = options.get("game_id")
    chaos_kill = options.get("chaos_kill")
    cwd = options.get("cwd")
    env = options.get("env")
    agent_cwds = options.get("agent_cwds")
    agent_envs = options.get("agent_envs")
    config = config or GameConfig()
    game_id = game_id or uuid.uuid4().hex
    baseline_map: Mapping[int, BaselineAgent]
    if isinstance(baselines, list):
        baseline_map = {i: a for i, a in enumerate(baselines) if a is not None}
    else:
        baseline_map = baselines
    state = engine.new_game(config, seed)
    procs: dict[int, ProcSession] = {}
    stats = GameStats()
    stats.fallbacks = dict.fromkeys(range(config.players), 0)
    stats.restarts = dict.fromkeys(range(config.players), 0)
    stats.decisions = dict.fromkeys(range(config.players), 0)
    stats.latencies = {s: [] for s in range(config.players)}
    chaos: dict[int, list[int]] = chaos_kill or {}

    def ensure_spawn(seat: int, deadline: float | None = None) -> bool:
        """Ensure a live process for one seat; return False on fault."""
        if seat in procs and procs[seat].alive:
            return True
        seat_cwd = agent_cwds.get(seat, cwd) if agent_cwds else cwd
        seat_env = agent_envs.get(seat, env) if agent_envs else env
        proc = ProcAgent(agent_cmds[seat], game_id, seat, cwd=seat_cwd, env=seat_env)
        procs[seat] = proc
        spawn_timeout = init_timeout if deadline is None else max(0.0, deadline - time.monotonic())
        checkpoint, fault = proc.spawn(
            stats.next_rid(game_id), config.max_turns, spawn_timeout, deadline=deadline,
            config=config.to_dict(),
        )
        return record_spawn_result(stats, seat, checkpoint, fault)

    def baseline_fn(game_state: GameState, seat: int) -> tuple[EngineAction, bool]:
        """Select a baseline action; baselines never count as fallbacks here."""
        return _baseline_action_id(game_state, seat, baseline_map[seat]), False

    try:
        run_initial_handshakes(set(agent_cmds), ensure_spawn, stats)

        drive_ctx = DriveContext(
            game_id=game_id,
            protocol_seats=set(agent_cmds),
            sessions=procs,
            stats=stats,
            decision_timeout=timeout,
            ensure_spawn=ensure_spawn,
            baseline_fn=baseline_fn,
            chaos_kill=chaos,
            step_guard=100_000,
            guard_message="protocol runner step guard tripped",
        )
        drive_game(state, drive_ctx)

        outcome = build_outcome(state)
        for seat, proc in procs.items():
            if proc.alive:
                proc.notify_game_over(
                    outcome, engine.observe(state, seat), stats.next_rid(game_id), game_id
                )
                proc.kill()
        info = {
            "game_id": game_id,
            "outcome": outcome,
            "turns": state.turn_count,
            "fallbacks": stats.fallbacks,
            "restarts": stats.restarts,
            "faults": stats.faults,
            "latencies": stats.latencies,
            "decisions": stats.decisions,
            "checkpoints": {s: stats.checkpoints.get(s) for s in agent_cmds},
            "stderrs": {s: (procs[s].stderr_text if s in procs else "") for s in agent_cmds},
        }
        return state, info
    finally:
        for proc in procs.values():
            proc.kill()
