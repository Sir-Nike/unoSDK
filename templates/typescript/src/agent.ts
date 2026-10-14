#!/usr/bin/env node
// unoSDK TypeScript starter entrypoint (Node standard library only).
//
// Usage:
//   node src/agent.ts          # speaks newline-JSON on stdin/stdout
//
// EDIT selectAction below to change how moves are picked. The adapter loop at
// the bottom handles initialization, request IDs, JSON serialization,
// flushing, checkpoints, terminal acknowledgements, and stderr logging --
// stdout stays protocol-only.
//
// Erasable-syntax TypeScript only: Node strips the types at load time with
// no build step, so this file must not use enums, namespaces, or parameter
// properties. Requires Node >= 22.18 (type stripping).

import { createInterface } from "node:readline";
import { realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  MAX_PROTOCOL_LINE,
  buildAction,
  buildDone,
  buildReady,
  isCheckpointOk,
  validateOrganizerMessage,
  type JsonValue,
} from "./protocol.ts";

export interface LegalAction {
  action_id: string;
  type: string;
  card_ref?: string;
  card?: { color: string | null; kind: string; value: number | null };
  color?: string;
  [key: string]: unknown;
}

export interface Observation {
  legal_actions?: LegalAction[];
  [key: string]: unknown;
}

export interface Selection {
  action_id: string;
  agentState: unknown;
}

function pendingPenalty(observation: Observation): number {
  let total = 0;
  const history = Array.isArray(observation.history) ? observation.history : [];
  for (const event of history) {
    const item = event as { type?: unknown; card?: { kind?: unknown } };
    if (item.type === "play") {
      if (item.card?.kind === "draw_two") total += 2;
      if (item.card?.kind === "wild_draw_four") total += 4;
    } else if (item.type === "penalty_draw") {
      total = 0;
    }
  }
  const reported = observation.pending_penalty;
  return typeof reported === "number" && reported >= 0 ? reported : total;
}

/**
 * Choose a playable action when one exists, else the first legal action.
 *
 * @param observation - your hand (local h refs), hand sizes, top discard,
 *   active colour, pile sizes, public history, and explicit request-scoped
 *   legal_actions (a0.. in canonical order; a0 always legal).
 * @param agentState - your last accepted checkpoint (or null/undefined).
 * @returns one legal action id plus the new checkpoint (null/undefined keeps
 *   the previous one; must stay JSON and under 1 MiB). Your process may
 *   restart at any time, so anything you need later must be rebuildable from
 *   observation.history + the checkpoint.
 */
export function selectAction(observation: Observation, agentState: unknown): Selection {
  const legal = (observation && observation.legal_actions) || [];
  // A pending penalty offers compatible multiplier plays and a ``draw``
  // action to accept the chain. Continue the chain when possible.
  if (observation?.phase === "TURN" && pendingPenalty(observation) > 0) {
    const stack = legal.find((a) => a.type === "play");
    if (stack) {
      return { action_id: stack.action_id, agentState: agentState ?? null };
    }
  }
  const play = legal.find((a) => a.type === "play");
  const chosen = play || legal[0];
  if (!chosen) {
    throw new Error("selectAction: no legal actions");
  }
  return { action_id: chosen.action_id, agentState: agentState ?? null };
}

function log(...parts: unknown[]): void {
  process.stderr.write(parts.map((p) => String(p)).join(" ") + "\n");
}

function send(obj: object): void {
  const line = JSON.stringify(obj);
  if (Buffer.byteLength(line, "utf8") > MAX_PROTOCOL_LINE) {
    throw new Error("protocol message exceeds 4 MiB line limit");
  }
  process.stdout.write(line + "\n");
}

export function runAgent(): void {
  const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
  rl.on("line", (raw: string) => {
    const line = raw.trim();
    if (!line) return;
    let msg: { type?: unknown; [key: string]: unknown };
    try {
      msg = JSON.parse(line) as { type?: unknown; [key: string]: unknown };
    } catch {
      log("agent: ignoring non-JSON input line");
      return;
    }
    const err = validateOrganizerMessage(msg);
    if (err !== null) {
      log(`agent: invalid organizer message: ${err}`);
      return;
    }
    try {
      const requestId = msg["request_id"] as string;
      if (msg.type === "initialize") {
        send(buildReady(requestId, null));
      } else if (msg.type === "act") {
        const observation = msg["observation"] as Observation;
        const { action_id, agentState } = selectAction(
          observation,
          (msg["agent_state"] as JsonValue) ?? null
        );
        if (typeof action_id !== "string") {
          log("agent: selectAction must return a string action_id");
          process.exit(1);
        }
        if (
          agentState !== null &&
          agentState !== undefined &&
          !isCheckpointOk(agentState)
        ) {
          log("agent: checkpoint exceeds 1 MiB; failing this response");
          process.exit(1);
        }
        send(buildAction(requestId, action_id, agentState ?? null));
      } else if (msg.type === "game_over") {
        send(buildDone(requestId));
        // Wait for another initialize; the organizer closes stdin at shutdown.
      }
    } catch (e) {
      log(`agent: fatal error: ${e instanceof Error ? e.stack ?? e.message : e}`);
      process.exit(1);
    }
  });
}

// Realpath both sides: argv[1] may contain symlinks (e.g. macOS /tmp) while
// import.meta.url is already resolved. A plain string comparison would then
// silently skip startup.
const invokedAsScript =
  typeof process.argv[1] === "string" &&
  realpathSync(fileURLToPath(import.meta.url)) === realpathSync(process.argv[1].startsWith("file:") ? fileURLToPath(process.argv[1]) : process.argv[1]);

if (invokedAsScript) {
  runAgent();
}
