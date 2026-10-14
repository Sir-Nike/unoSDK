# Training: simulator, seeds, self-play

Use the bundled evaluator (see `local-testing.md`). From this repository's
root, run `export PYTHONPATH="$PWD/local_evaluator/src"` before the examples.
Your submitted agent must run without this development dependency.

## In-process simulator

```python
from triad_uno.engine import GameConfig
from triad_uno.simulator import run_game, replay_actions, to_replay_dict
from triad_uno.baselines import RandomAgent, HeuristicAgent

agents = [HeuristicAgent(seed) for seed in range(4)]
final, action_log, events = run_game(42, agents, GameConfig())
print(final.outcome, final.turn_count)

# Reproduce the exact game from seed + actions (auditable replays).
replayed = replay_actions(42, action_log, GameConfig())
assert [c.uid for h in replayed.hands for c in h] == [c.uid for h in final.hands for c in h]
```

`run_game` expects agents exposing `select(observation) -> action_id` — the
same observation dict the protocol delivers. Invalid outputs fall back to
`a0`, mirroring production.

## Seeds and determinism

- `new_game(config, seed)` drives shuffle, deal, starter, and recycling from
  one `random.Random(seed)`. Never use module-global randomness in agents or
  training code; give every bot its own `random.Random`.
- Practice uses separate seeds from final evaluation. Final seeds stay
  private until appeals close -- do not hardcode seed-specific play.
- Replays are `seed + action log`; `to_replay_dict` shows the artifact shape
  (placements, turn count, hand sizes, full public history).

## Baseline opponents

| Baseline | Behaviour |
|---|---|
| `RandomAgent(seed)` | Uniform over legal actions (seeded per instance). |
| `HeuristicAgent(seed)` | Plays first non-wild in canonical order, presses playable draws, saves wilds. |

Mix them in `run_game` or `uv run --locked python -m triad_uno.simulator` for
diverse self-play. Independent per-instance RNG keeps bots uncorrelated.

## Protocol agents in training

```sh
uv run --locked python tools/local_match.py --games 3
uv run --locked python tools/local_match.py --restart-test --games 3
```

Reports include placements, latency, fallbacks and restarts. Use the in-process
simulator above for bulk training and this runner for submission conformance.
