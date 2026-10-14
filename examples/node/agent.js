#!/usr/bin/env node
// Minimal non-Python agent (Node stdlib only). Always plays a0 — the
// canonical fallback, which is always legal — and echoes the checkpoint.
// Usage: node agent.js
const readline = require("readline");

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });

rl.on("line", (raw) => {
  const line = raw.trim();
  if (!line) return;
  let msg;
  try {
    msg = JSON.parse(line);
  } catch {
    process.stderr.write("node-agent: ignoring non-JSON line\n");
    return;
  }
  const send = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");
  if (msg.type === "initialize") {
    send({ type: "ready", request_id: msg.request_id, agent_state: null });
  } else if (msg.type === "act") {
    const legal = (msg.observation && msg.observation.legal_actions) || [];
    if (!legal.length) {
      process.stderr.write("node-agent: no legal actions\n");
      process.exit(1);
    }
    send({ type: "action", request_id: msg.request_id, action_id: legal[0].action_id, agent_state: msg.agent_state ?? null });
  } else if (msg.type === "game_over") {
    send({ type: "done", request_id: msg.request_id });
  } else {
    process.stderr.write(`node-agent: unknown message type ${msg.type}\n`);
  }
});
