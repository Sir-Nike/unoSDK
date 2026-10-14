# Event runtime update (2026-10-11)

Images now follow the runtime 2 contract (UID 10000, exec ENTRYPOINT, resource/SDK labels). See [Docker submission guide](docs/docker-submission.md). The walkthroughs below use four players/seven cards as defaults; the runtime supports configuration.

# unoSDK — participant starter for Triad UNO v1

Fork this repository and edit it directly. The repo root is a ready-to-run
**Python** submission (standard library only); a complete **TypeScript**
alternative lives under `templates/typescript/`.

- `src/agent.py` — entrypoint (`python src/agent.py`)
- `src/strategy.py` — **`select_action(observation, agent_state)`: edit this**
- `src/uno_agent/` — protocol adapter + vendored wire contract (do not edit
  unless you are re-vendoring; see `src/uno_agent/VENDOR.json`)
- `templates/typescript/` — full TS submission + swap instructions
- `examples/` — reference agents (counter, heuristic, random, torch, node)
  using the hook API; smoke-test any of them with `tools/check.py`
- `docs/` — rulebook, quickstart, JavaScript setup, protocol, rules/evaluation,
  local testing, training, submission, troubleshooting
- `tools/check.py` — dependency-free protocol smoke check (both languages)
- `tests/` — run with `uv run --locked pytest`

Read the [rulebook](docs/rulebook.md), then start with `docs/quickstart.md`.
Submit a Linux/amd64 Docker image by immutable `repository@sha256` reference
(see [submission instructions](docs/submission.md)).

GPU agents have a 1 GiB per-agent VRAM admission allowance. See the submission
guide for the current enforcement limitation and model sizing guidance.

## Bundled local evaluator

Run `uv run --locked python tools/local_match.py --games 3` for protocol self-play without
a separate evaluator checkout. See [local testing](docs/local-testing.md) for
baselines, training, validation and snapshot provenance.

## Participant resource limits

RAM is limited to **1 GiB**, GPU VRAM to **1 GiB**, and participant code/assets to
**1.5 GiB**. Organizer-approved dependency bases are exempt from image accounting. Existing turn-limit scoring and timing rules remain unchanged.
See [resource limits and the Git-aware size checker](docs/resource-limits.md).

## Contributor setup

Install uv 0.11.28, then run `uv sync --locked` to create the pinned local
environment. Use `uv run --locked pytest` for the Python tests and
`uv run --locked python tools/check.py --agent python` or `--agent ts` for the
protocol smoke checks. The submitted agent itself remains standard-library
only by default.
