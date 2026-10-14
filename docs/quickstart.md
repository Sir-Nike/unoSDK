> Event runtime 2: follow [docker-submission.md](docker-submission.md) for mandatory image labels, UID, limits and preflight. The participant submission is a Linux/amd64 image identified by its immutable digest.

# Quickstart: from fork to a submitted UNO agent

You submit an **executable agent**, not a weights file. It speaks
newline-JSON over stdin/stdout (see `protocol.md`), loads in ≤ 60 s, and
answers each decision in ≤ 5 s. Python is the default path; TypeScript is
covered in `typescript.md`.

## 1. Fork and run the default agent

Install uv 0.11.28 first.

```sh
git clone <your-fork-url> uno-agent && cd uno-agent
uv sync --locked
uv run --locked python tools/check.py --agent python
```

Expect `SMOKE PASSED (python): 8 checks`. That script drives the agent
through the whole protocol lifecycle -- initialize, TURN, AFTER_DRAW,
initial-colour choice, a kill-and-respawn restart, and game_over -- with no
dependencies beyond Python 3.11+ itself.

## 2. Edit the strategy

Open `src/strategy.py` and change `select_action(observation, agent_state)`:

```python
def select_action(observation, agent_state):
    legal = observation["legal_actions"]
    # your logic here; return (action_id, new_agent_state)
    return legal[0]["action_id"], agent_state
```

Rules inside `select_action`:

- Return one `action_id` from the current `legal_actions`. `a0` is always
  legal (it matches the organizer fallback in canonical order).
- `agent_state` is your optional JSON checkpoint (≤ 1 MiB). Return `None`
  (or the value you received) to keep the previous one. Process memory may
  vanish between turns: rebuild beliefs from `observation["history"]` plus
  this checkpoint.
- Never `print()` to stdout -- protocol replies only. Log to stderr.
- Never assume `h0..` / `a0..` ids persist past the current request.

The adapter (`src/uno_agent/adapter.py`) and entrypoint (`src/agent.py`)
need no changes.

## 3. Test

```sh
uv run --locked python tools/check.py --agent python # fast lifecycle smoke test
uv run --locked pytest                    # full suite: lifecycle + compat
```

Then play real games with the bundled local evaluator
(see `local-testing.md`):

```sh
uv run --locked python tools/local_match.py --games 3 --restart-test
```

Expect `VALIDATION PASSED` with zero fallbacks. The `--restart-test` flag
kills your process mid-game to prove your restart path works.

## 4. Build

```sh
docker build --platform linux/amd64 -t uno-agent:dev .
echo '{"type":"initialize","protocol_version":"1.0","request_id":"r0","game_id":"g","seat":0,"ruleset":"triad-uno-v1","config":{"max_turns":1000}}' \
  | docker run -i --rm uno-agent:dev
```

Expect one `ready` line on stdout. The starter has no third-party runtime
dependencies. If you add any, declare them with `uv add PACKAGE`, commit the
updated `pyproject.toml` and `uv.lock`, and add an install step to the image
build that uses a prebuilt wheelhouse. Runtime evaluation has no network access.
For the Dockerfile, copy the pinned uv binary from the official uv image and
install with `uv pip install --system --offline --no-index --find-links
/wheels --require-hashes --requirement requirements.lock`. Export the runtime
lock with `uv export --locked --no-dev --format requirements.txt --output-file
requirements.lock`; uv does not have `pip download` or `pip wheel` subcommands,
so wheelhouse creation must fetch and verify the target-platform artifact URLs
and hashes recorded by `uv.lock` before the image build. Preload and digest-pin
both the Python base image and the uv image when building offline.

## 5. Freeze and publish your image

Review and commit your source, dependency lock, weights and provenance. Build
from that exact revision. Publish the tested image to the organizer-announced
registry:

```sh
docker tag uno-agent:dev YOUR_REGISTRY/YOUR_TEAM:submission
docker push YOUR_REGISTRY/YOUR_TEAM:submission
docker image inspect YOUR_REGISTRY/YOUR_TEAM:submission --format '{{json .RepoDigests}}'
```

Submit the immutable `registry/repository@sha256:<64 hex>` reference, as described
in [submission.md](submission.md). Tags alone are not accepted. Keep the source
repository URL and exact commit SHA in your provenance record.

Check storage with `uv run --locked python tools/check_submission_size.py --image uno-agent:dev`.
See [resource limits](resource-limits.md) before adding ML dependencies.
