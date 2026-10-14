"use strict";
// Vendored wire helpers for the unoSDK TypeScript starter (Node stdlib only).
// Mirrors the organizer protocol 1.0: builders, message validation, and the
// published resource bounds. See src/schemas/*.json for the authoritative
// wire contract and VENDOR.json (sibling of schemas/) for the source revision.
//
// Erasable-syntax TypeScript only (interfaces, annotations, `as` casts):
// Node strips the types at load time with no build step, so this file must
// not use enums, namespaces, or parameter properties. Requires Node >= 22.18
// (type stripping); the Dockerfile pins a compatible image.

export const PROTOCOL_VERSION = "1.0";
export const MAX_PROTOCOL_LINE = 4 * 1024 * 1024; // 4 MiB per message
export const MAX_CHECKPOINT = 1024 * 1024; // 1 MiB serialized agent_state

const ORGANIZER_MSGS = new Set(["initialize", "act", "game_over"]);

export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [key: string]: JsonValue };

export interface OrganizerMessage {
  type: string;
  protocol_version?: unknown;
  request_id?: unknown;
  game_id?: unknown;
  seat?: unknown;
  observation?: unknown;
  agent_state?: JsonValue;
  outcome?: unknown;
  [key: string]: unknown;
}

export interface ReadyMessage {
  type: "ready";
  request_id: string;
  agent_state: JsonValue;
}

export interface ActionMessage {
  type: "action";
  request_id: string;
  action_id: string;
  agent_state: JsonValue;
}

export interface DoneMessage {
  type: "done";
  request_id: string;
}

export function checkpointSize(value: unknown): number {
  if (value === null || value === undefined) return 0;
  return Buffer.byteLength(JSON.stringify(value), "utf8");
}

export function isCheckpointOk(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  try {
    return checkpointSize(value) <= MAX_CHECKPOINT;
  } catch {
    return false;
  }
}

export function buildReady(requestId: string, agentState: unknown): ReadyMessage {
  return { type: "ready", request_id: requestId, agent_state: (agentState ?? null) as JsonValue };
}

export function buildAction(
  requestId: string,
  actionId: string,
  agentState: unknown
): ActionMessage {
  return {
    type: "action",
    request_id: requestId,
    action_id: actionId,
    agent_state: (agentState === undefined ? null : agentState) as JsonValue,
  };
}

export function buildDone(requestId: string): DoneMessage {
  return { type: "done", request_id: requestId };
}

export function validateOrganizerMessage(msg: unknown): string | null {
  if (typeof msg !== "object" || msg === null) return "message must be an object";
  const m = msg as OrganizerMessage;
  if (!ORGANIZER_MSGS.has(m.type)) return "unknown organizer message type";
  for (const key of ["protocol_version", "request_id", "game_id"] as const) {
    if (typeof m[key] !== "string" || !m[key]) return `missing or invalid ${key}`;
  }
  if (m.protocol_version !== PROTOCOL_VERSION) return "unsupported protocol version";
  if (m.type === "initialize") {
    const config = "config" in m ? m.config : {};
    if (typeof config !== "object" || config === null || Array.isArray(config)) {
      return "invalid config";
    }
    const players = "players" in config ? config.players : 4;
    if (typeof players !== "number" || !Number.isInteger(players) || players !== 4) {
      return "invalid players";
    }
    if (typeof m.seat !== "number" || !Number.isInteger(m.seat) || m.seat < 0 || m.seat >= 4) {
      return "invalid seat";
    }
  } else if (m.type === "act") {
    if (typeof m.observation !== "object" || m.observation === null) {
      return "act requires observation";
    }
    if ("agent_state" in m && !isCheckpointOk(m.agent_state)) {
      return "oversized agent_state";
    }
  } else if (m.type === "game_over" && (typeof m.outcome !== "object" || m.outcome === null)) {
    return "game_over requires outcome";
  }
  return null;
}
