> Event runtime 2: follow [docker-submission.md](docker-submission.md) for mandatory image labels, UID, limits and preflight; [organizer RUNNING.md](../../UNO/RUNNING.md) defines the four-player setup and scoring. Older source-upload instructions below are historical.

Create a new TypeScript starter without replacing or deleting Python root files: `python tools/use_typescript.py ../my-uno-ts`. Requires Node 22.18+. If you add packages, package a lockfile and install during the image build (offline npm ci requires a prepopulated cache).

# TypeScript setup: swap the Python starter for the Node one

The TypeScript alternative is a complete submission under
`templates/typescript/`. It implements the same protocol with the Node
standard library only and **no build step**: the sources use
erasable-syntax TypeScript (interfaces, annotations, `as` casts -- no enums,
namespaces, or parameter properties), which Node >= 22.18 strips at load
time, so `node src/agent.ts` runs directly. No initializer or generator is
required -- you replace the corresponding root files by hand.

## 1. Replace the root files

From the repository root, copy the template over the Python files:

```sh
cp templates/typescript/submission.json submission.json
cp templates/typescript/Dockerfile Dockerfile
cp templates/typescript/package.json package.json
cp templates/typescript/tsconfig.json tsconfig.json
cp templates/typescript/README.md README.md
cp templates/typescript/provenance.md provenance.md
cp templates/typescript/src/agent.ts src/agent.ts
cp templates/typescript/src/protocol.ts src/protocol.ts
mkdir -p src/schemas
cp templates/typescript/src/schemas/*.json src/schemas/
cp templates/typescript/src/VENDOR.json src/VENDOR.json
rm -f requirements.txt src/strategy.py src/agent.py \
      src/uno_agent/adapter.py src/uno_agent/protocol.py
rm -rf src/uno_agent  # replaced by src/protocol.ts
git status             # review: src/ holds agent.ts + protocol.ts + schemas/
```

After the swap, the root layout is the TS submission:

```text
submission.json   # entrypoint ["node", "src/agent.ts"], language typescript
Dockerfile        # node:24-slim image (type stripping needs >= 22.18)
package.json      # type: module, no dependencies
tsconfig.json     # strict + erasableSyntaxOnly (editors / optional tsc --noEmit)
README.md         # TS agent readme (replaces the Python one)
src/agent.ts      # entrypoint + selectAction
src/protocol.ts   # vendored protocol helpers
src/schemas/      # vendored wire schemas
src/VENDOR.json   # source revision record
provenance.md
```

Keep `docs/`, `tests/`, and `tools/` -- they cover both languages.

## 2. Edit the strategy

Open `src/agent.ts` and change
`selectAction(observation, agentState)`:

```ts
export function selectAction(observation: Observation, agentState: unknown): Selection {
  const legal = (observation && observation.legal_actions) || [];
  // your logic here; return { action_id, agentState }
  return { action_id: legal[0].action_id, agentState: agentState ?? null };
}
```

It is exported so tests and harnesses can import it directly. The adapter
loop (`runAgent` at the bottom of the file) handles initialization, request
IDs, JSON serialization, flushing, checkpoints, terminal acknowledgements,
and stderr logging -- stdout stays protocol-only.

Stay inside erasable syntax: if you add enums, namespaces, or parameter
properties, Node can no longer run the file without a compile step and your
submission breaks. `tsconfig.json` enforces this for anyone running
`tsc --noEmit` (TypeScript 5.8+); the agent itself never needs `tsc`.

## 3. Run the equivalent checks

```sh
node --version                        # need Node 22.18+ to run .ts
uv run --locked python tools/check.py --agent ts # fast lifecycle smoke test (8 checks)
uv run --locked pytest                    # full suite incl. test_ts_agent
```

`tools/check.py --agent ts` drives `templates/typescript/src/agent.ts`. Once
you have replaced the root files as above, check the swapped layout instead:

```sh
uv run --locked python tools/check.py --agent ts --agent-path src/agent.ts --cwd .
```

Then play real games with a UNO checkout (`local-testing.md`):

```sh
triad-uno validate-agent --cmd "node $PWD/src/agent.ts" --games 3 --restart-test
```

Expect `VALIDATION PASSED` with zero fallbacks.

## 4. Build and submit

```sh
docker build -t uno-agent-ts:dev .
echo '{"type":"initialize","protocol_version":"1.0","request_id":"r0","game_id":"g","seat":2,"ruleset":"triad-uno-v1","config":{"max_turns":1000}}' \
  | docker run -i --rm uno-agent-ts:dev node src/agent.ts
```

Expect one `ready` line. Prepare the commit and submit the repository URL
plus full commit SHA exactly as in `submission.md`.
