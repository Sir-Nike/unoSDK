> Event runtime 2: follow [docker-submission.md](docker-submission.md) for mandatory image labels, UID, limits and preflight; [organizer RUNNING.md](../../UNO/RUNNING.md) defines the four-player setup and scoring. Older source-upload instructions below are historical.

# Protocol reference: observations, history, actions, lifecycle

Wire contract: protocol 1.0, ruleset `triad-uno-v1`. The vendored JSON
Schemas in `src/uno_agent/schemas/` (Python) and `src/schemas/` after the JS
swap are authoritative; the adapters in this repo implement them. Transport
is **newline-delimited JSON over stdin/stdout**: one line is one complete
message, stdout carries protocol messages only, diagnostics go to stderr.

## Lifecycle

1. The organizer spawns one process per seat per game.
2. `initialize` → you load weights and reply `ready` (≤ 60 s startup).
3. `act` → you reply `action` (≤ 5 s per decision, organizer clock).
4. `game_over` → you reply `done`; the container is destroyed.

```json
{"type": "initialize", "protocol_version": "1.0", "request_id": "g-d0", "game_id": "g", "seat": 0, "ruleset": "triad-uno-v1", "config": {"max_turns": 1000}}
{"type": "ready", "request_id": "g-d0", "agent_state": null}
```

## Observations (`act`)

Every decision carries your seat's filtered broadcast: **your** current hand
with request-local `h` refs, every seat's hand size (counts only -- the only
opponent-hand information you ever get), the top discard, the active colour,
public pile sizes, direction, turn numbers, the full public history, the
drawn-card ref in `AFTER_DRAW`, your last accepted checkpoint, and the
explicit legal actions. You never see other hands, deck ids or order, the
seed, RNG state, or opponent identities.

Key fields:

| Field | Meaning |
|---|---|
| `phase` | `TURN`, `AFTER_DRAW` (only the drawn card may be played), `CHOOSE_INITIAL_COLOR` (initial wild), `FINISHED`. |
| `turn_number` / `turns_completed` | 1-indexed current turn / completed turns. |
| `hand` | Your cards as `{card_ref, color, kind, value}`. Refs are reissued per decision. |
| `hand_sizes` | Four counts, seats 0..3. |
| `top_discard` / `active_color` | Public pile top and the colour to match. |
| `draw_pile_size` / `discard_pile_size` | Public pile sizes, not contents. |
| `history` | Full public event list so far (below). Reconstruct beliefs here. |
| `drawn_card_ref` | Points into `hand` in `AFTER_DRAW`, else null. |
| `legal_actions` | Every legal move with `a0..` ids for this request only. During a pending multiplier chain, compatible multiplier cards are `play` actions and `draw` accepts the accumulated penalty. |
| `agent_state` | Your last accepted checkpoint (or null). |
| `finished_order` / `is_finished` | Placements so far; finished seats are skipped. |

Card faces: `color` is red/yellow/green/blue (null for wilds), `kind` is
number/skip/reverse/draw_two/wild/wild_draw_four, `value` is 0..9 for
numbers, else null.

## History events

Events are public and complete: every decision replays the whole list, so a
restarted agent sees everything it is entitled to. Common events (each with
`type`, `turn_number`, `seat`, post-step `hand_sizes`):

| Event | Extra fields |
|---|---|
| `game_start` | `draw_pile_size`, `selected_starter`, `initial_discard` (card). |
| `initial_discard` | `card`. |
| `initial_skip` / `initial_draw_two` / `initial_reverse` | `skipped`/`penalized`+`drawn`/`selected`, `next_seat`. |
| `initial_color_choice` | `color`, `draw_pile_size`, `discard_pile_size`. |
| `play` | `card`, `active_color` (+`from_draw` when the drawn card was played). Multiplier plays also include the public `pending_penalty` total. |
| `draw` | `drawn` (0 or 1). Zero draws happen when even recycling finds no cards. |
| `keep_auto` / `keep` | Drawn card kept automatically / voluntarily. |
| `penalty_draw` | `count`, public requested `penalty`, `cause` (`draw_two`/`wild_draw_four`), `from`. |
| `skip` | `skipped`. `reverse` carries the new `direction`. |
| `finish` / `finish_auto` | `placement`. The last seat is auto-placed 4th. |
| `game_end` | `placements`, `is_draw`, `unfinished`, `reason`. |

Play/draw/keep/penalty/skip/reverse/finish events also carry post-step
`draw_pile_size` and `discard_pile_size`, which is how you detect recycling
(discard shrinks to the top card while the draw pile refills).

## Legal actions and your reply

`legal_actions` entries look like
`{"action_id": "a0", "type": "play", "card_ref": "h3", "card": {...}}` with an
extra `color` for wild plays (four colour variants, ordered by your most
common hand colour). Other types: `draw` (normal TURN draw when no play is
available, or accept a pending multiplier penalty), `keep` (AFTER_DRAW), and
`choose_color` (CHOOSE_INITIAL_COLOR). Reply:

```json
{"type": "action", "request_id": "g-d12", "action_id": "a0", "agent_state": {"memory": 2}}
```

`a0` is always legal: numbers, then coloured actions, then wild, then Wild
Draw Four -- the deterministic organizer fallback. A wrong request id, an
unknown action id, malformed JSON, or stdout noise is a fault and the
fallback is applied; your next 5 s budget includes any restart.

Terminal exchange:

```json
{"type": "game_over", "protocol_version": "1.0", "request_id": "g-d99", "game_id": "g", "outcome": {"placements": [2, 0, 1, 3], "is_draw": false, "unfinished": [], "reason": "all_placed"}, "final_observation": {...}}
{"type": "done", "request_id": "g-d99"}
```

## Checkpoints and restarts

- Checkpoint: any JSON value, ≤ 1 MiB serialized. `null`/absent keeps the
  previous one; checkpoints are only overwritten, never implicitly cleared.
- Late, invalid, oversized, or wrong-id responses never update the
  checkpoint. Only the exact current request can change state.
- After a crash or timeout the replacement process receives the last
  accepted checkpoint plus the full entitled history, and must be correct
  from those alone. Test it with `--restart-test` (see `local-testing.md`).

## Resource limits

| Setting | Value |
|---|---|
| Startup deadline | 60 s |
| Decision deadline | 5 s (delivery + compute + receipt) |
| CPU / RAM per agent | 1 vCPU / 1 GiB |
| Protocol line / checkpoint | 4 MiB / 1 MiB |
| Network during evaluation | none |
| Users / filesystem | non-root, read-only root, 1 GiB tmp |
