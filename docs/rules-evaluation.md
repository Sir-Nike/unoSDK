> Event runtime 2: follow [docker-submission.md](docker-submission.md) for mandatory image labels, UID, limits and preflight; [organizer RUNNING.md](../../UNO/RUNNING.md) defines the four-player setup and scoring. Older source-upload instructions below are historical.

# Rules and evaluation: Triad UNO v1, placements, scoring, fallbacks

## Event rules (Triad UNO v1)

- **Deck:** 108 cards. Per colour (red, yellow, green, blue): one 0, two of
  each 1..9, two skip, two reverse, two draw_two (25 per colour, 100 total),
  plus four wild and four Wild Draw Four.
- **Deal:** seven cards per seat, round-robin 0..3. Starter chosen by the
  seeded setup; direction starts clockwise.
- **Normal play:** match the active colour, the number value, or the action
  symbol. Wild is always playable and sets the active colour. Wild Draw Four
  is playable **only** when your hand holds no card of the active colour
  (wilds ignored) -- the engine enforces this; the action simply is not
  offered otherwise.
- **Restricted drawing:** draw only when no card can legally be played. Draw
  exactly one card when cards remain. If it is playable you may play it (with
  colour choice for wilds) or keep it; other cards cannot be played that
  turn. Otherwise it is kept automatically and the turn ends.
- **Penalties and stacking:** A Draw Two may be stacked on Draw Two, and a
  Wild Draw Four may be stacked on either multiplier. The recipient may play a
  compatible multiplier or choose `draw` to accept the accumulated penalty;
  accepting draws the full amount (as many as possible when short) and loses
  that turn. A stacked Wild Draw Four bypasses the ordinary active-colour
  restriction.
- **Skip / Reverse:** skip the next seat in the current direction; reverse
  flips direction, then play proceeds to the next seat in the new direction.
- **UNO declaration:** automatic. **Challenges:** none.
- **Recycling:** when the draw pile empties, every discard except the top is
  shuffled into a new draw pile. If cards still run short, draw as many as
  possible (possibly zero; a zero-draw turn simply passes).
- **Initial discard:** number → starter acts; skip → starter skipped; draw_two
  → starter draws 2 and is skipped; reverse → direction flips and the seat
  before the selected starter begins; wild → starter chooses the colour, then
  takes a normal turn; Wild Draw Four is reshuffled and re-dealt as opener.
- **Length:** 1,000 completed turns by default. Choosing the initial colour is
  not a turn; `AFTER_DRAW` belongs to the same turn.

## Winning and placements

Players finish in the order they empty their hands; finished seats are
skipped and play continues until every placement is known. A final-card draw
penalty is applied **before** recording the finish, and a finishing Wild /
Wild Draw Four still sets the active colour. When three seats have finished,
the last one is automatically fourth, so `placements` has length 4.

At the turn limit, confirmed finishers retain their placements. Unfinished
players tie across the remaining positions and split those position rewards
equally. A game with no confirmed finishers is a four-way draw.

## Draw scoring and standings

Each game awards placement points:

| Result | Points |
|---|---|
| 1st (first to empty hand) | 5 |
| 2nd | 3 |
| 3rd | 2 |
| 4th | 1 |
| Pure four-way turn-limit draw | 2.75 each |

Track standings use aggregate placement points over scheduled games (the fixed
schedule gives every team the same game count). Published uncertainty is
measured over four-game seat-rotation blocks. For equal track scores, compare
victories against the immediately higher-ranked team. A tie for first instead
compares victories against teams in the next lower score tier (rank 3 when
exactly two teams tie for first). A victory means earning more
placement points than that opponent in a scheduled game they both played. If
still tied, compare first-place finishes, then team names alphabetically. This
produces unique final ranks. Practice games never count.

## Fallback behavior

If your agent faults -- malformed output, wrong request id, illegal action
id, crash, oversize output, or timeout -- the organizer applies a
deterministic legal fallback and the game continues:

| Phase | Fallback |
|---|---|
| Normal turn with a playable card | First legal play in canonical order |
| Normal turn with no legal play | Draw, keep automatically, end turn |
| After drawing | Keep the drawn card |
| Initial wild colour | Most common colour in your hand |

Canonical order: ordinary numbers, then coloured actions
(skip/reverse/draw_two), then wild, then Wild Draw Four; within a category by
fixed colour then value/kind order. You get another opportunity on your next
turn -- there is no elimination for repeated faults -- but every fallback is
counted, and validation requires zero fallbacks (see `local-testing.md`).
