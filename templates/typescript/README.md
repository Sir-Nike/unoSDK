# UNO TypeScript starter (Triad UNO v1)

TypeScript submission for the Triad UNO v1 event (protocol 1.0), running on
Node with the standard library only and **no build step**: the starter uses
erasable-syntax TypeScript (interfaces, annotations, `as` casts -- no enums,
namespaces, or parameter properties), which Node >= 22.18 strips at load
time. This is the complete submission alternative from the unoSDK
`templates/typescript/` directory, copied over the repository root -- swap
instructions live in `docs/typescript.md` of the SDK repository.

- `src/agent.ts` — entrypoint (`node src/agent.ts`) plus
  `selectAction(observation, agentState)`: **edit this function** to change
  how moves are picked. It chooses a playable action when one exists, else
  the first legal action. Exported for tests and harnesses.
- `src/protocol.ts` — vendored protocol helpers (message validation,
  builders, 4 MiB / 1 MiB bounds).
- `src/schemas/` — vendored wire schemas; `src/VENDOR.json` records the
  organizer revision they were taken from.
- `tsconfig.json` — strict + `erasableSyntaxOnly` for editors and optional
  `tsc --noEmit` checks (TypeScript 5.8+; not needed to run the agent).

Run the lifecycle smoke check with plain Python 3 (no dependencies):

```sh
uv run --locked python tools/check.py --agent ts --agent-path src/agent.ts --cwd .
```

Then validate real games with a UNO engine checkout:

```sh
triad-uno validate-agent --cmd "node $PWD/src/agent.ts" --games 3 --restart-test
```

Protocol notes: stdout carries replies only (log to stderr), echo every
`request_id` exactly, answer from the current `legal_actions` (`a0` is
always safe), keep checkpoints JSON under 1 MiB, and stay correct after a
restart -- rebuild from `observation.history` plus your checkpoint.
