> Event runtime 2: follow [docker-submission.md](docker-submission.md) for mandatory image labels, UID, limits and preflight; [organizer RUNNING.md](../../UNO/RUNNING.md) defines current four-player setup and scoring. Older source-upload instructions below are historical.

# Local testing and self-play

The bundled `local_evaluator/` contains the authoritative UNO rules,
filtered observations, simulator, simple opponents and protocol runner. Python
3.11+ is sufficient; no evaluator checkout, server or third-party package is needed.
Run `uv sync --locked` once, then use these commands from this repository's root:

```sh
uv run --locked python tools/local_match.py --games 3
uv run --locked python tools/local_match.py --baseline-only --games 10
uv run --locked python tools/local_match.py --candidate /path/to/submission --games 3
uv run --locked python tools/local_match.py --games 3 --report reports/local.json
uv run --locked python tools/local_match.py --restart-test --games 3
```

Default matches launch an independent instance of the candidate in every seat;
this is full-game self-play through the submission protocol. `--baseline-only`
runs heuristic opponents in process for fast simulation. `--cmd` overrides the
candidate manifest's entrypoint, including other languages. Relative files and
weights resolve from the candidate directory. `--seed`, `--max-turns` and
`--timeout` control the run. Any fault or fallback returns exit status 2; status
0 means the protocol run passed, including games ending at the turn cap. This
checks correctness, not competitive strength. Local subprocesses are trusted
code and do not enforce Docker, RAM, CPU, GPU or production network restrictions.
Validate your container separately before submission.

UNO follows the event rulebook: every match has exactly four players and one deck. The
organizer, Python and TypeScript validators, schemas, and bundled evaluator reject other table sizes.

For training, expose the bundled package from the repository root:

```sh
export PYTHONPATH="$PWD/local_evaluator/src"
```

Use `triad_uno.simulator.run_game` with four agents implementing
`select(observation) -> action_id`.
The simulator uses the same transition and observation code as evaluation.
Use only the filtered observation in your policy; full simulator state is for
local training/debugging. Submitted agents must run without `local_evaluator`.
The directory is outside `src/` and is not copied by the submission Dockerfile.

`local_evaluator/VENDOR.json` pins the upstream revision and source/bundle hashes.
Organizers can refresh and verify the allowlisted snapshot with:

```sh
uv run --locked python tools/vendor_local_evaluator.py /path/to/UNO
uv run --locked python tools/vendor_local_evaluator.py /path/to/UNO --check
uv run --locked pytest tests/test_local_evaluator.py
```

Refresh both the SDK and its derived benchmark repo from the same clean upstream
revision. Do not hand-edit bundled rules. The snapshot excludes organizer portal,
submission storage, scheduling and deployment modules. Shared transport retains
its upstream cleanup behavior; the local tool uses subprocesses only.

During development, `--working-tree` explicitly permits a snapshot from an
uncommitted organizer checkout. Its manifest records the base revision, marks
the source as `working-tree`, and hashes every source and bundled file. Use the
same flag with `--check` to verify it. Protocol vendor metadata also records exact
hashes for overrides to its base revision; commit and refresh these snapshots
before publishing a release.
