# Troubleshooting

## Malformed output / fallback spikes

Symptoms: `validate-agent` reports `illegal action id`, `response is not
valid json`, or a climbing fallback count.

- Print nothing to stdout except protocol lines. Audit every `print`/`console.log`:
  diagnostics go to stderr (`sys.stderr` / `process.stderr`).
- Validate that `action_id` comes from the **current** request's
  `legal_actions`. `a0..` ids die with the request; caching them across turns
  is the most common bug.
- Wild plays need their `color` variant id, not the card ref. When in doubt,
  return `a0` -- it is always legal.
- Reproduce minimally: `uv run --locked python tools/check.py --agent python` (or `--agent
  js`) shows the exact failing exchange.

## Timeouts

Symptoms: `timeout` faults, rising `latency_max`, slow games.

- Preload weights and build lookup tables at `initialize`, not per decision.
- The 5 s budget covers delivery, your compute, serialization, and receipt --
  measure with the latency columns in `validate-agent`, not wall-clock in
  your head.
- Flush stdout after every reply (both starters already do). A buffered
  reply that arrives late is still a timeout.
- Keep per-decision work bounded: full-history scans are fine (history is
  small), model inference must fit comfortably inside the budget.

## GPU memory

- GPU models have a maximum 1 GiB per-agent VRAM admission allowance. The
  runtime currently accounts for this allowance but does not enforce a hard
  per-container VRAM cap.
- Include CUDA context, framework, and inference workspace allocations when
  estimating peak use. If the model needs more than 1 GiB, use a smaller model
  or submit a CPU agent.

## Restart bugs

Symptoms: everything passes until `--restart-test`, then fallbacks or
divergent play after the kill.

- Your replacement process gets the last accepted checkpoint plus the full
  public history -- and nothing else. If a decision differs with and without
  process memory, something lived only in memory.
- `null` checkpoints keep the previous one; they never clear it. If you need
  to reset state, write an explicit fresh value.
- Oversized checkpoints (> 1 MiB) invalidate the whole response: the action
  is lost AND the checkpoint is not updated. Stay small.
- An `initialize` handler that returns a fresh checkpoint does not erase the
  accepted one (the organizer preserves it), but do not depend on that --
  treat `initialize` as load-only and rebuild from history + checkpoint.

## Container failures

Symptoms: image builds locally but validation reports build/startup errors,
or the agent exits immediately in the image.

- Reproduce the exact entrypoint: `docker run -i --rm <img>` (or `node src/agent.ts`) fed one `initialize` line must print
  one `ready` line.
- Do not bake in host paths, `..` segments, or shell wrappers: the manifest
  entrypoint is an argv array resolved inside the image.
- Runtime evaluation has no network. For offline builds, stage Python packages
  in a target-platform wheelhouse and install from it with locked, offline
  `uv pip install`; runtime package installation or weight downloads will fail
  even when local tests pass.
- Run as non-root (both template Dockerfiles do) and keep writes under the
  tmp allowance; the root filesystem is read-only.
- If the daemon itself is unavailable, validation falls back to the
  documented local path -- but a red build log always means your files, so
  read the stage diagnostics before re-running.
