"""In-process simulator + JSON replay format (Phase 1 training kit)."""

from __future__ import annotations

import argparse
import contextlib
import sys
from typing import TYPE_CHECKING, Any

from . import engine
from .baselines import HeuristicAgent, RandomAgent
from .engine import EngineAction, GameConfig, GameState

if TYPE_CHECKING:
    from .agent_session import BaselineAgent

JsonDict = dict[str, Any]


def run_game(
    seed: int,
    agents: list[BaselineAgent],
    config: GameConfig | None = None,
    max_steps: int = 100_000,
) -> tuple[GameState, list[JsonDict], list[JsonDict]]:
    """Run one game with in-process agents exposing select(observation)->action_id.

    Returns (final_state, action_log, all_events). action_log entries are
    engine-action dicts plus seat, and reproduce the game via replay_actions().
    Invalid agent outputs fall back to the canonical a0 action.
    """
    config = config or GameConfig()
    state = engine.new_game(config, seed)
    action_log: list[JsonDict] = []
    all_events: list[JsonDict] = []
    steps = 0
    while state.phase != engine.FINISHED:
        if steps > max_steps:
            msg = "simulator step guard tripped"
            raise RuntimeError(msg)
        seat = state.current_seat
        obs = engine.observe(state, seat)
        action: EngineAction | None = None
        with contextlib.suppress(Exception):
            aid = agents[seat].select(obs)
            action = engine.engine_action_from_id(state, seat, aid)
        if action is None:
            action = engine.fallback_action(state)
        if action is None:
            msg = "no legal action in non-terminal state"
            raise AssertionError(msg)
        tr = engine.step(state, action)
        if not tr.ok:
            msg = f"simulator generated invalid action: {tr.error}"
            raise AssertionError(msg)
        action_log.append({"seat": seat, **action.to_dict()})
        all_events.extend(tr.events)
        steps += 1
    return state, action_log, all_events


def replay_actions(
    seed: int, action_log: list[JsonDict], config: GameConfig | None = None
) -> GameState:
    """Re-apply a recorded action log; must reproduce identical transitions."""
    config = config or GameConfig()
    state = engine.new_game(config, seed)
    for entry in action_log:
        action = EngineAction(
            type=entry["type"], card_uid=entry.get("card_uid"), color=entry.get("color")
        )
        tr = engine.step(state, action)
        if not tr.ok:
            msg = f"replay diverged: {tr.error} on {entry}"
            raise RuntimeError(msg)
    return state


def to_replay_dict(
    seed: int, config: GameConfig, final: GameState, action_log: list[JsonDict]
) -> JsonDict:
    """Assemble the full organizer replay payload for one finished game."""
    return {
        "manifest": {
            "ruleset": engine.RULESET_VERSION,
            "seed": seed,
            "max_turns": config.max_turns,
        },
        "actions": action_log,
        "outcome": None
        if final.outcome is None
        else {
            "placements": final.outcome.placements,
            "is_draw": final.outcome.is_draw,
            "unfinished": final.outcome.unfinished,
            "reason": final.outcome.reason,
        },
        "turn_count": final.turn_count,
        "final_hand_sizes": [len(h) for h in final.hands],
        "history": final.history,
    }


def main() -> None:
    """Run a few local games and print first-place wins by seat."""
    ap = argparse.ArgumentParser(description="Triad UNO v1 local simulator")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--games", type=int, default=5)
    ap.add_argument(
        "--mix",
        action="store_true",
        help="mix random + heuristic agents instead of all-heuristic",
    )
    args = ap.parse_args()
    wins = [0, 0, 0, 0]
    for g in range(args.games):
        seed = args.seed + g
        if args.mix:
            agents: list[BaselineAgent] = [
                RandomAgent(seed + 100 + i) if i % 2 == 0 else HeuristicAgent(seed + 200 + i)
                for i in range(4)
            ]
        else:
            agents = [HeuristicAgent(seed + i) for i in range(4)]
        final, _, _ = run_game(seed, agents)
        if final.outcome and not final.outcome.is_draw:
            wins[final.outcome.placements[0]] += 1
        sys.stdout.write(f"game seed={seed}: outcome={final.outcome} turns={final.turn_count}\n")
    sys.stdout.write(f"first-place wins by seat over {args.games} games: {wins}\n")


if __name__ == "__main__":
    main()
