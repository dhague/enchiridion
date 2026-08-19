/**
 * Unit tests for the toolcallstats module (#257). Uses fixture JSONL logs in
 * temp dirs — no real hook or Claude Code session involved.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { logPath, readLog, summarize, formatSummary } from "./toolcallstats.js";
import type { Summary } from "./toolcallstats.js";

function tmp(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "toolcallstats-test-"));
}

/** Write a fixture JSONL log for `id` under `stateDir` and return its path. */
function writeLog(stateDir: string, id: string, lines: string[]): string {
  fs.mkdirSync(stateDir, { recursive: true });
  const file = path.join(stateDir, `${id}-tool-calls.jsonl`);
  fs.writeFileSync(file, lines.join("\n"));
  return file;
}

// ---------------------------------------------------------------------------
// logPath
// ---------------------------------------------------------------------------

test("logPath joins stateDir with the session file name", () => {
  const stateDir = tmp();
  assert.equal(
    logPath("sess-1", stateDir),
    path.join(stateDir, "sess-1-tool-calls.jsonl"),
  );
});

// ---------------------------------------------------------------------------
// readLog
// ---------------------------------------------------------------------------

test("readLog returns an empty array when no log exists", () => {
  const stateDir = tmp();
  assert.deepEqual(readLog("nope", stateDir), []);
});

test("readLog parses each line oldest-first and skips blank/malformed lines", () => {
  const stateDir = tmp();
  writeLog(stateDir, "sess-1", [
    '{"tool":"Read","prompt_id":"p1"}',
    "",
    "not json",
    '{"tool":"Write","prompt_id":"p1"}',
  ]);
  const events = readLog("sess-1", stateDir);
  assert.equal(events.length, 2);
  assert.equal(events[0]["tool"], "Read");
  assert.equal(events[1]["tool"], "Write");
});

// ---------------------------------------------------------------------------
// summarize
// ---------------------------------------------------------------------------

test("summarize counts totals, a stable histogram, and distinct prompts", () => {
  const events = [
    { tool: "Read", prompt_id: "p1" },
    { tool: "Write", prompt_id: "p1" },
    { tool: "Read", prompt_id: "p2" },
    { tool: "Read", prompt_id: "p2" },
    { tool: "Bash", prompt_id: "p2" },
  ];
  const s = summarize(events);
  assert.equal(s.total, 5);
  // Histogram: Read=3, Write=1, Bash=1. Ties (Write/Bash) break by first-seen.
  assert.deepEqual(s.byTool, [
    { tool: "Read", count: 3 },
    { tool: "Write", count: 1 },
    { tool: "Bash", count: 1 },
  ]);
  assert.equal(s.prompts, 2);
  assert.equal(s.hasCallsPerPrompt, true);
  assert.equal(s.callsPerPrompt, 2.5);
});

test("summarize maps a missing tool to '?'", () => {
  const s = summarize([{ prompt_id: "p1" }, { prompt_id: "p1" }]);
  assert.deepEqual(s.byTool, [{ tool: "?", count: 2 }]);
});

test("summarize reports no calls-per-prompt when there are no prompts", () => {
  const s = summarize([{ tool: "Read" }, { tool: "Read" }]);
  assert.equal(s.prompts, 0);
  assert.equal(s.hasCallsPerPrompt, false);
  assert.equal(s.callsPerPrompt, 0);
});

test("summarize handles an empty event list", () => {
  const s = summarize([]);
  assert.equal(s.total, 0);
  assert.deepEqual(s.byTool, []);
  assert.equal(s.prompts, 0);
  assert.equal(s.hasCallsPerPrompt, false);
});

test("summarize ignores empty and non-string prompt_ids", () => {
  const events = [
    { tool: "Read", prompt_id: "" },
    { tool: "Read", prompt_id: 7 },
    { tool: "Read" },
  ];
  const s = summarize(events);
  assert.equal(s.prompts, 0);
});

// ---------------------------------------------------------------------------
// formatSummary
// ---------------------------------------------------------------------------

test("formatSummary renders the fixed text the CLI prints", () => {
  const s: Summary = {
    total: 5,
    byTool: [
      { tool: "Read", count: 3 },
      { tool: "Write", count: 1 },
      { tool: "Bash", count: 1 },
    ],
    prompts: 2,
    callsPerPrompt: 2.5,
    hasCallsPerPrompt: true,
  };
  const out = formatSummary(s);
  assert.match(out, /Total tool calls: 5/);
  assert.match(out, /\s*3\s+Read/);
  assert.match(out, /\s*1\s+Write/);
  assert.match(out, /\s*1\s+Bash/);
  assert.match(
    out,
    /Prompts \(proxy for turns, not exact — see #99\): 2, 2.5 calls\/prompt/,
  );
});

test("formatSummary omits the prompts line when there are no prompts", () => {
  const s: Summary = {
    total: 2,
    byTool: [{ tool: "Read", count: 2 }],
    prompts: 0,
    callsPerPrompt: 0,
    hasCallsPerPrompt: false,
  };
  const out = formatSummary(s);
  assert.match(out, /Total tool calls: 2/);
  assert.ok(!out.includes("Prompts"), "no prompts line expected");
});
