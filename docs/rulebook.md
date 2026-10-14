# Triad UNO v1 rulebook

This guide describes the four-player game used by the Triad UNO engine and SDK. The SDK's [rules and evaluation guide](rules-evaluation.md) summarizes event scoring and fallback behavior.

## Objective

Be the first player to empty your hand. Play continues after the first player finishes so the remaining players can earn their placements.

## Components and setup

The game uses a 108-card deck and four players. Each colour—red, yellow, green, and blue—has one 0, two of each number from 1 through 9, and two each of Skip, Reverse, and Draw Two. The deck also contains four Wild cards and four Wild Draw Four cards.

Each player receives seven cards. The engine turns over one card to start the discard pile and selects the starting seat. Play normally moves clockwise. A Wild Draw Four cannot be the opening discard; it is shuffled back into the draw pile and another card is revealed.

The opening card takes effect immediately:

- A number card starts a normal turn for the selected player.
- Skip skips that player; the next player begins.
- Draw Two makes that player draw two cards and lose the turn.
- Reverse changes direction; the player immediately before the selected player in the original direction begins.
- Wild lets the selected player choose the opening colour, then take a normal turn.

## Taking a turn

On your turn, play one card that matches the top discard by colour, number, or action symbol. A Wild can be played on any turn and lets you choose the active colour.

A Wild Draw Four can be played only if your hand has no card of the active colour. Wild cards do not count as colour matches. The engine checks this condition. When stacking on an existing draw penalty, a Wild Draw Four is allowed even if you hold the active colour.

If you have a playable card, you must play; drawing is allowed only when no card in your hand is legal. Draw one card. If it is playable, you may play that drawn card or keep it. If it is not playable, it is kept automatically and your turn ends. After drawing, you may not play a different card from your hand.

## Action cards

- **Skip:** The next player in the current direction loses their turn.
- **Reverse:** Direction changes, then play passes to the next player in the new direction.
- **Draw Two:** The next player draws two cards and loses their turn, unless they continue the penalty with another Draw Two or a Wild Draw Four.
- **Wild:** Choose the colour that will be active.
- **Wild Draw Four:** Choose the active colour and pass a four-card penalty to the next player, unless they continue it with a Draw Two or another Wild Draw Four.

Draw penalties can be stacked. Draw Two may follow only Draw Two. Wild Draw Four may follow either draw penalty. The player receiving the chain may play a permitted multiplier or accept the full accumulated penalty, draw as many cards as remain available, and lose the turn. There are no challenges.

When the draw pile runs out, the engine shuffles the discard pile back into a draw pile while keeping the top discard in place. If the game cannot supply the full penalty, the affected player draws as many cards as possible.

## Finishing and placements

UNO declaration is automatic; there is no separate call to make when you have one card. Your placement is recorded when you empty your hand. A finishing Skip or Reverse still takes effect. A finishing Draw Two or Wild Draw Four still applies its penalty before your finish is recorded, unless the next player continues the penalty chain. A finishing Wild or Wild Draw Four still sets the active colour.

Play continues until three players have finished. The remaining player is fourth. If the configured turn limit is reached first (1,000 turns by default), confirmed finishes stand. Players who have not finished share the points assigned to the remaining placement positions equally. A pure four-player tie with no confirmed finishers receives 2.75 points per player under the default 5/3/2/1 schedule.

| Result | Points |
|---|---:|
| 1st | 5 |
| 2nd | 3 |
| 3rd | 2 |
| 4th | 1 |
| Four-way turn-limit draw, no finishers | 2.75 each |

## Information and event handling

You can see your own cards and public information such as the top discard, active colour, direction, player hand sizes, and public game history. Other players' cards and the order of the draw pile are hidden.

In SDK or event play, a malformed, missing, late, or illegal agent response is replaced by a deterministic legal fallback so the game can continue. Fallbacks count against the agent's validation result. See the [rules and evaluation guide](rules-evaluation.md) and [protocol guide](protocol.md) for submission behavior.

## Execution and submission limits

Algorithms and non-language ML models have the same limits: **1 GiB RAM**,
**1 GiB GPU VRAM** when using a GPU, and **1.5 GiB** for the complete frozen
submission and final runtime image. These are binary units (GiB); source size
is measured without Git metadata and image size includes runtime dependencies.
The existing configured turn limit and sharing of remaining placement points
remain in effect; there is no new one-point penalty. See
[resource limits](resource-limits.md) for enforcement and the pre-submission checker.
