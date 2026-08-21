/**
 * hooks tests — the session-start and post-tool-use handlers, failing open.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { sessionStart, postToolUse } from "./hooks.js";
import { sessionsDir } from "./sessionstate.js";
import { readTranscriptPath } from "./sessionstate.js";

function sessionsDirFor(root: string): string {
  return sessionsDir(root, "", () => ["", false]);
}

function tmp(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-hooks-"));
}

// --- SessionStart ------------------------------------------------------------

test("sessionStart records transcript path under payload cwd", () => {
  const root = tmp();
  sessionStart({
    session_id: "abc123",
    transcript_path: "/x/abc123.jsonl",
    cwd: root,
  });
  assert.equal(
    readTranscriptPath("abc123", sessionsDirFor(root)),
    "/x/abc123.jsonl",
  );
});

test("sessionStart incomplete payload is a silent no-op", () => {
  for (const payload of [
    { transcript_path: "/x/abc123.jsonl", cwd: tmp() },
    { session_id: "abc123", cwd: tmp() },
  ]) {
    sessionStart(payload);
    const root = payload.cwd as string;
    assert.ok(
      !fs.existsSync(sessionsDirFor(root)),
      "sessions dir was created; want no state written",
    );
  }
});

// --- PostToolUse -------------------------------------------------------------

function logLines(
  root: string,
  sessionID: string,
): Array<Record<string, unknown>> {
  const data = fs.readFileSync(
    path.join(sessionsDirFor(root), `${sessionID}-tool-calls.jsonl`),
    "utf8",
  );
  return data
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as Record<string, unknown>);
}

test("postToolUse appends one JSON line under payload cwd", () => {
  const root = tmp();
  postToolUse({
    session_id: "abc123",
    cwd: root,
    tool_name: "Bash",
    tool_use_id: "tu_1",
    prompt_id: "pr_1",
    duration_ms: 42,
  });
  const events = logLines(root, "abc123");
  assert.equal(events.length, 1);
  assert.deepEqual(events[0], {
    tool: "Bash",
    tool_use_id: "tu_1",
    prompt_id: "pr_1",
    agent_id: null,
    agent_type: null,
    duration_ms: 42,
  });
});

test("postToolUse second call appends rather than overwrites", () => {
  const root = tmp();
  postToolUse({ session_id: "abc123", cwd: root, tool_name: "Bash" });
  postToolUse({ session_id: "abc123", cwd: root, tool_name: "Read" });
  const events = logLines(root, "abc123");
  assert.equal(events.length, 2);
  assert.equal(events[0].tool, "Bash");
  assert.equal(events[1].tool, "Read");
});

test("postToolUse records subagent fields", () => {
  const root = tmp();
  postToolUse({
    session_id: "abc123",
    cwd: root,
    tool_name: "Read",
    agent_id: "agent_1",
    agent_type: "general-purpose",
  });
  const event = logLines(root, "abc123")[0];
  assert.equal(event.agent_id, "agent_1");
  assert.equal(event.agent_type, "general-purpose");
});

test("postToolUse missing session id is a silent no-op", () => {
  const root = tmp();
  postToolUse({ tool_name: "Bash", cwd: root });
  assert.ok(
    !fs.existsSync(sessionsDirFor(root)),
    "sessions dir was created; want no log written",
  );
});
