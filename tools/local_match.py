#!/usr/bin/env python3
"""Play local protocol-agent games using the bundled authoritative rules."""
import argparse
import json
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "local_evaluator" / "src"))
GAME = json.loads((ROOT / "submission.json").read_text())["game"].split("-")[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=ROOT, help="submission directory")
    parser.add_argument("--cmd", help="override manifest entrypoint, e.g. 'node src/agent.js'")
    parser.add_argument("--games", type=int, default=3)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-turns", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--baseline-only", action="store_true", help="fast in-process baseline simulation")
    if GAME == "bluff":
        parser.add_argument("--players", type=int, default=4, help="Bluff supports 4–8")
        parser.add_argument("--decks", type=int, default=1, help="Bluff supports 1–8")
    parser.add_argument("--restart-test", action="store_true", help="UNO: restart every seat before its second decision")
    parser.add_argument("--report", type=Path, help="write JSON diagnostics")
    args = parser.parse_args()
    if args.games < 1 or args.timeout <= 0:
        parser.error("games and timeout must be positive")
    if GAME == "uno":
        from triad_uno import engine, baselines, simulator, local_runner
        config = engine.GameConfig(max_turns=args.max_turns)
        players = engine.SEATS
    else:
        if args.restart_test:
            parser.error("--restart-test is supported by the UNO runner only")
        from triad_bluff import engine, baselines, simulator, local_runner
        config = engine.GameConfig(max_turns=args.max_turns, players=args.players, decks=args.decks)
        players = args.players
    candidate = args.candidate.resolve()
    command = None
    if not args.baseline_only:
        manifest = json.loads((candidate / "submission.json").read_text())
        if manifest["game"] != "triad-" + GAME + "-v1":
            parser.error("candidate belongs to a different game")
        command = shlex.split(args.cmd) if args.cmd else list(manifest["entrypoint"])
        if not command:
            parser.error("empty agent command")
        if command[0] in ("python", "python3"):
            command[0] = sys.executable
        command = [command[0]] + [str(candidate / arg) if (candidate / arg).is_file() else arg
                                   for arg in command[1:]]
    rows = []
    for index in range(args.games):
        seed = args.seed + index
        if args.baseline_only:
            agents = [baselines.HeuristicAgent(seed * 10 + seat) for seat in range(players)]
            if GAME == "uno":
                state, _, _ = simulator.run_game(seed, agents, config)
                info = {"outcome": local_runner.build_outcome(state), "fallbacks": {}, "faults": []}
            else:
                result = simulator.run_game(seed, agents, config)
                state = result.state
                info = {"outcome": state.outcome.to_dict(), "fallbacks": result.fallbacks, "faults": result.faults}
        elif GAME == "uno":
            state, info = local_runner.run_protocol_game(
                seed, {seat: command for seat in range(players)}, {}, config,
                timeout=args.timeout, cwd=str(candidate),
                chaos_kill=({seat: [2] for seat in range(players)} if args.restart_test else None))
        else:
            # The original Bluff runner inherits cwd for relative weights/configuration.
            import os
            previous = Path.cwd()
            try:
                os.chdir(candidate)
                result = local_runner.run_commands({seat: command for seat in range(players)},
                    seed, config, timeout=args.timeout, game_id=f"local-{seed}")
            finally:
                os.chdir(previous)
            state = result.state
            info = {"outcome": state.outcome.to_dict(), "fallbacks": result.fallbacks,
                    "faults": result.faults, "latencies": result.latencies}
        latencies = info.pop("latencies", {})
        info.pop("checkpoints", None)
        if GAME == "uno" and args.restart_test:
            info["injected_faults"] = [fault for fault in info["faults"] if fault.get("stage") == "chaos-kill"]
            info["faults"] = [fault for fault in info["faults"] if fault.get("stage") != "chaos-kill"]
        row = {"seed": seed, "turns": state.turn_count, **info,
               "latency_max_ms": max((value * 1000 for values in latencies.values()
                                      for value in values), default=0)}
        rows.append(row)
        print(json.dumps(row))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({"game": GAME, "games": rows}, indent=2) + "\n")
    return 2 if any(sum(row["fallbacks"].values()) or row["faults"] for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
