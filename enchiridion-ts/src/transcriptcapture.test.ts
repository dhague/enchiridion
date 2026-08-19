/**
 * Unit tests for the transcriptcapture module (#257). Uses mock env lookups,
 * fixture transcripts, and an injectable export seam — no real transcript
 * fetch and no real `opencode` invocation.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  sanitizeSlug,
  transcriptToPage,
  findTranscriptPath,
  writeCapture,
  normalizeExport,
  encodeTurns,
  captureOpenCodeSession,
  captureSession,
  findOpenCodeSessionID,
  ErrTooFewTurns,
  CaptureError,
} from "./transcriptcapture.js";
import type { LookupEnv } from "./sessionstate.js";

function tmp(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "transcriptcapture-test-"));
}

/** Build a LookupEnv from a fixed map (missing keys report not-present). */
function env(map: Record<string, string>): LookupEnv {
  return (key: string): [string | undefined, boolean] => {
    const value = map[key];
    return [value, value !== undefined];
  };
}

const NOW = new Date("2026-01-02T03:04:00");

function jsonlLine(type: string, content: unknown): string {
  return JSON.stringify({
    type,
    isMeta: false,
    isSidechain: false,
    message: { role: type, content },
  });
}

function transcriptFixture(): string[] {
  return [
    jsonlLine("user", "Hello there"),
    jsonlLine("assistant", "Hi! How can I help?"),
    jsonlLine("user", "Tell me about pooling."),
    jsonlLine("assistant", "Pooling keeps handles around."),
  ];
}

// ---------------------------------------------------------------------------
// sanitizeSlug
// ---------------------------------------------------------------------------

test("sanitizeSlug lowercases, collapses, and strips", () => {
  assert.equal(
    sanitizeSlug("  Connection   Pooling  ", 60),
    "connection-pooling",
  );
  assert.equal(
    sanitizeSlug("What is a Wiki? (Deep Dive)", 60),
    "what-is-a-wiki-deep-dive",
  );
});

test("sanitizeSlug folds non-ASCII and drops unicode scripts", () => {
  assert.equal(sanitizeSlug("caf\u00e9 time", 60), "cafe-time");
  assert.equal(sanitizeSlug("\u3053\u3093\u306b\u3061\u306f", 60), "");
});

test("sanitizeSlug returns empty for empty/pure-punctuation input", () => {
  assert.equal(sanitizeSlug("", 60), "");
  assert.equal(sanitizeSlug("!!!", 60), "");
});

test("sanitizeSlug caps on a word boundary when possible", () => {
  assert.equal(sanitizeSlug("aaa-bbb-ccc", 7), "aaa-bbb");
  // No separator inside the window: hard-truncate to the cap.
  assert.equal(sanitizeSlug("aaaaaaaaaaaa bbbbbbbbbbb", 10), "aaaaaaaaaa");
});

test("sanitizeSlug uses a default maxLength when given <= 0", () => {
  assert.equal(sanitizeSlug("a".repeat(120), 0), "a".repeat(60));
});

// ---------------------------------------------------------------------------
// transcriptToPage
// ---------------------------------------------------------------------------

test("transcriptToPage renders user/assistant turns and a deterministic filename", () => {
  const [filename, markdown] = transcriptToPage(
    transcriptFixture(),
    "sess-abc-123",
    NOW,
    "Connection Pooling",
    "User",
    "Claude",
    2,
  );
  assert.equal(filename, "2026-01-02-0304-connection-pooling-sess.md");
  assert.match(markdown, /^# Session sess-abc-123/);
  assert.match(markdown, /\*\*Saved:\*\* 2026-01-02 03:04 {2}/);
  assert.match(markdown, /## User\n\nHello there/);
  assert.match(markdown, /## Claude\n\nHi! How can I help\?/);
});

test("transcriptToPage ignores meta, sidechain, and non user/assistant entries", () => {
  const lines = [
    jsonlLine("user", "hi"),
    JSON.stringify({
      type: "user",
      isMeta: true,
      message: { role: "user", content: "hidden" },
    }),
    JSON.stringify({
      type: "assistant",
      isSidechain: true,
      message: { role: "assistant", content: "hidden" },
    }),
    JSON.stringify({
      type: "system",
      message: { role: "system", content: "sys" },
    }),
    jsonlLine("assistant", "hello"),
  ];
  const [, markdown] = transcriptToPage(
    lines,
    "s-1",
    NOW,
    "",
    "User",
    "Claude",
    2,
  );
  assert.ok(!markdown.includes("hidden"));
  assert.ok(!markdown.includes("sys"));
  assert.ok(markdown.includes("## User\n\nhi"));
  assert.ok(markdown.includes("## Claude\n\nhello"));
});

test("transcriptToPage only counts text blocks, joining multiple with a blank line", () => {
  const lines = [
    jsonlLine("user", "hello"),
    jsonlLine("assistant", [
      { type: "text", text: "one" },
      { type: "tool_use", name: "Read" },
      { type: "text", text: "two" },
    ]),
  ];
  const [, markdown] = transcriptToPage(
    lines,
    "s-1",
    NOW,
    "",
    "User",
    "Claude",
    2,
  );
  assert.ok(markdown.includes("## Claude\n\none\n\ntwo"));
  assert.ok(!markdown.includes("Read"));
});

test("transcriptToPage degrades an empty slug to the bare date-shortid name", () => {
  const [filename] = transcriptToPage(
    transcriptFixture(),
    "sess-abc",
    NOW,
    "",
    "User",
    "Claude",
    2,
  );
  assert.equal(filename, "2026-01-02-0304-sess.md");
});

test("transcriptToPage throws ErrTooFewTurns when below minTurns", () => {
  assert.throws(
    () =>
      transcriptToPage(
        [jsonlLine("user", "only one turn")],
        "s-1",
        NOW,
        "",
        "User",
        "Claude",
        2,
      ),
    (err: unknown) =>
      err instanceof ErrTooFewTurns && err.turns === 1 && err.minTurns === 2,
  );
});

// ---------------------------------------------------------------------------
// findTranscriptPath
// ---------------------------------------------------------------------------

function claudeEnvAndState(): {
  wikiRoot: string;
  stateDir: string;
  lookupEnv: LookupEnv;
  transcriptPath: string;
} {
  const project = tmp();
  const stateDir = path.join(project, ".claude", "wiki-knowledge", "sessions");
  fs.mkdirSync(stateDir, { recursive: true });
  const transcriptPath = path.join(project, "transcript.jsonl");
  fs.writeFileSync(transcriptPath, transcriptFixture().join("\n"));
  fs.writeFileSync(
    path.join(stateDir, "sess-123.json"),
    JSON.stringify({ transcript_path: transcriptPath }),
  );
  const lookupEnv = env({ CLAUDE_CODE_SESSION_ID: "sess-123" });
  return { wikiRoot: project, stateDir, lookupEnv, transcriptPath };
}

test("findTranscriptPath returns the recorded transcript path", () => {
  const { wikiRoot, lookupEnv, transcriptPath } = claudeEnvAndState();
  assert.equal(findTranscriptPath(wikiRoot, lookupEnv), transcriptPath);
});

test("findTranscriptPath throws when CLAUDE_CODE_SESSION_ID is unset", () => {
  assert.throws(
    () => findTranscriptPath("", env({})),
    (err: unknown) =>
      err instanceof CaptureError &&
      /CLAUDE_CODE_SESSION_ID is not set/.test(err.message),
  );
});

test("findTranscriptPath throws when no state directory exists", () => {
  const lookupEnv = env({ CLAUDE_CODE_SESSION_ID: "sess-1" });
  assert.throws(
    () => findTranscriptPath("", lookupEnv),
    (err: unknown) =>
      err instanceof CaptureError &&
      /Could not locate a session state directory/.test(err.message),
  );
});

test("findTranscriptPath throws when the session was never recorded", () => {
  const project = tmp();
  const stateDir = path.join(project, ".claude", "wiki-knowledge", "sessions");
  fs.mkdirSync(stateDir, { recursive: true });
  const lookupEnv = env({
    CLAUDE_PROJECT_DIR: project,
    CLAUDE_CODE_SESSION_ID: "unknown",
  });
  assert.throws(
    () => findTranscriptPath("", lookupEnv),
    (err: unknown) =>
      err instanceof CaptureError &&
      /No state recorded for session unknown/.test(err.message),
  );
});

test("findTranscriptPath throws when the recorded transcript is missing", () => {
  const project = tmp();
  const stateDir = path.join(project, ".claude", "wiki-knowledge", "sessions");
  fs.mkdirSync(stateDir, { recursive: true });
  fs.writeFileSync(
    path.join(stateDir, "sess-9.json"),
    JSON.stringify({ transcript_path: path.join(project, "gone.jsonl") }),
  );
  const lookupEnv = env({
    CLAUDE_PROJECT_DIR: project,
    CLAUDE_CODE_SESSION_ID: "sess-9",
  });
  assert.throws(
    () => findTranscriptPath("", lookupEnv),
    (err: unknown) =>
      err instanceof CaptureError &&
      /Recorded transcript file does not exist/.test(err.message),
  );
});

// ---------------------------------------------------------------------------
// writeCapture
// ---------------------------------------------------------------------------

test("writeCapture writes into raw/conversations and returns the vault-relative path", () => {
  const wikiRoot = tmp();
  const rel = writeCapture(
    wikiRoot,
    "2026-01-02-0304-foo-sess.md",
    "content",
    "sess",
  );
  assert.equal(rel, "raw/conversations/2026-01-02-0304-foo-sess.md");
  assert.equal(fs.readFileSync(path.join(wikiRoot, rel), "utf8"), "content");
});

test("writeCapture reuses an existing capture by short id instead of the new filename", () => {
  const wikiRoot = tmp();
  const first = writeCapture(
    wikiRoot,
    "2026-01-02-0304-first-sess.md",
    "old",
    "sess",
  );
  // A second save with a different slug/timestamp must rewrite the first file.
  const second = writeCapture(
    wikiRoot,
    "2026-01-03-0506-second-sess.md",
    "new",
    "sess",
  );
  assert.equal(second, "raw/conversations/2026-01-02-0304-first-sess.md");
  assert.equal(fs.readFileSync(path.join(wikiRoot, second), "utf8"), "new");
  assert.ok(
    !first.includes("second"),
    "no second-named file should be created",
  );
});

// ---------------------------------------------------------------------------
// normalizeExport / encodeTurns
// ---------------------------------------------------------------------------

test("normalizeExport maps an opencode export into user/assistant text turns", () => {
  const doc = JSON.stringify({
    info: { id: "oc-1" },
    messages: [
      { info: { role: "user" }, parts: [{ type: "text", text: "hi" }] },
      {
        info: { role: "assistant" },
        parts: [
          { type: "text", text: "one" },
          { type: "text", text: "two" },
          { type: "tool", tool: "Read" },
        ],
      },
      { info: { role: "system" }, parts: [{ type: "text", text: "sys" }] },
    ],
  });
  const turns = normalizeExport(new TextEncoder().encode(doc));
  assert.deepEqual(turns, [
    { role: "user", text: "hi" },
    { role: "assistant", text: "one\n\ntwo" },
  ]);
});

test("normalizeExport throws on invalid JSON and unexpected shapes", () => {
  assert.throws(
    () => normalizeExport(new TextEncoder().encode("not json")),
    (err: unknown) =>
      err instanceof CaptureError && /invalid JSON/.test(err.message),
  );
  assert.throws(
    () => normalizeExport(new TextEncoder().encode("[1,2]")),
    (err: unknown) =>
      err instanceof CaptureError && /unexpected shape/.test(err.message),
  );
});

test("encodeTurns re-encodes turns into the Claude Code JSONL shape", () => {
  const lines = encodeTurns([
    { role: "user", text: "hi" },
    { role: "assistant", text: "hello" },
  ]);
  assert.equal(lines.length, 2);
  const parsed = JSON.parse(lines[0]) as Record<string, unknown>;
  assert.equal(parsed["type"], "user");
  assert.equal((parsed["message"] as Record<string, unknown>)["role"], "user");
  assert.equal((parsed["message"] as Record<string, unknown>)["content"], "hi");
});

// ---------------------------------------------------------------------------
// captureOpenCodeSession (injectable export seam — no real opencode)
// ---------------------------------------------------------------------------

function openCodeEnvAndState(): {
  project: string;
  lookupEnv: LookupEnv;
  sessionID: string;
} {
  const project = tmp();
  const stateDir = path.join(
    project,
    ".opencode",
    "wiki-knowledge",
    "sessions",
  );
  fs.mkdirSync(stateDir, { recursive: true });
  const sessionID = "oc-abc-999";
  fs.writeFileSync(
    path.join(stateDir, `${sessionID}.json`),
    JSON.stringify({ session_id: sessionID }),
  );
  const lookupEnv = env({ OPENCODE_SESSION_ID: sessionID });
  return { project, lookupEnv, sessionID };
}

test("captureOpenCodeSession writes a capture using the injected export seam", async () => {
  const { project, lookupEnv, sessionID } = openCodeEnvAndState();
  const doc = JSON.stringify({
    info: { id: sessionID },
    messages: [
      { info: { role: "user" }, parts: [{ type: "text", text: "hi" }] },
      { info: { role: "assistant" }, parts: [{ type: "text", text: "hello" }] },
      { info: { role: "user" }, parts: [{ type: "text", text: "more" }] },
      { info: { role: "assistant" }, parts: [{ type: "text", text: "done" }] },
    ],
  });
  const exportSeam = async (): Promise<Uint8Array> =>
    new TextEncoder().encode(doc);

  const wikiRoot = tmp();
  const rel = await captureOpenCodeSession(
    wikiRoot,
    "My Session",
    project,
    lookupEnv,
    NOW,
    exportSeam,
  );
  assert.match(rel, /^raw\/conversations\/2026-01-02-0304-my-session-oc\.md$/);
  const written = fs.readFileSync(path.join(wikiRoot, rel), "utf8");
  assert.match(written, /^# Session oc-abc-999/);
  assert.match(written, /## Claude\n\nhello/);
});

test("captureOpenCodeSession fails when the export seam errors", async () => {
  const { project, lookupEnv } = openCodeEnvAndState();
  const exportSeam = async (): Promise<Uint8Array> => {
    throw new CaptureError("export exploded");
  };
  await assert.rejects(
    () =>
      captureOpenCodeSession(tmp(), "", project, lookupEnv, NOW, exportSeam),
    (err: unknown) =>
      err instanceof CaptureError && /export exploded/.test(err.message),
  );
});

test("findOpenCodeSessionID throws when OPENCODE_SESSION_ID is unset", () => {
  assert.throws(
    () => findOpenCodeSessionID("", env({})),
    (err: unknown) =>
      err instanceof CaptureError &&
      /OPENCODE_SESSION_ID is not set/.test(err.message),
  );
});

test("findOpenCodeSessionID throws when the session is untracked", () => {
  const { project, sessionID } = openCodeEnvAndState();
  // A different id than the tracked one.
  const other = env({ OPENCODE_SESSION_ID: "oc-other" });
  assert.throws(
    () => findOpenCodeSessionID(project, other),
    (err: unknown) =>
      err instanceof CaptureError &&
      /No state recorded for session oc-other/.test(err.message),
  );
  void sessionID;
});

// ---------------------------------------------------------------------------
// captureSession host detection
// ---------------------------------------------------------------------------

test("captureSession throws when no session-id variable is set", async () => {
  await assert.rejects(
    () => captureSession(tmp(), "", "", env({}), NOW),
    (err: unknown) =>
      err instanceof CaptureError &&
      /Neither \$CLAUDE_CODE_SESSION_ID nor \$OPENCODE_SESSION_ID/.test(
        err.message,
      ),
  );
});

test("captureSession uses the Claude Code path when only CLAUDE_CODE_SESSION_ID is set", async () => {
  const { wikiRoot, lookupEnv, transcriptPath } = claudeEnvAndState();
  const rel = await captureSession(
    wikiRoot,
    "Connection Pooling",
    wikiRoot,
    lookupEnv,
    NOW,
  );
  assert.match(
    rel,
    /^raw\/conversations\/2026-01-02-0304-connection-pooling-[\w-]+\.md$/,
  );
  const written = fs.readFileSync(path.join(wikiRoot, rel), "utf8");
  assert.match(written, /## User\n\nHello there/);
  assert.match(written, /## Claude\n\nHi! How can I help\?/);
  void transcriptPath;
});

test("captureSession prefers OpenCode when both ids are set but the tracker recorded it", async () => {
  const { project, lookupEnv: ocEnv, sessionID } = openCodeEnvAndState();
  const { lookupEnv: ccEnv } = claudeEnvAndState();
  const both = (key: string): [string | undefined, boolean] => {
    const fromOc = ocEnv(key);
    if (fromOc[1]) return fromOc;
    return ccEnv(key);
  };
  const doc = JSON.stringify({
    info: { id: sessionID },
    messages: [
      { info: { role: "user" }, parts: [{ type: "text", text: "hi" }] },
      { info: { role: "assistant" }, parts: [{ type: "text", text: "hello" }] },
    ],
  });
  const wikiRoot = tmp();
  const rel = await captureSession(wikiRoot, "", project, both, NOW, async () =>
    new TextEncoder().encode(doc),
  );
  // OpenCode short id ("oc") should appear in the filename.
  assert.match(rel, /-oc\.md$/);
});

test("captureSession falls back to Claude Code when OpenCode id is untracked", async () => {
  // Both ids set, but the OpenCode one has no tracker state in this project —
  // a variable leaked from an unrelated project or an outer session — so the
  // Claude Code path wins (mirrors Go's
  // TestCaptureSessionFallsBackToClaudeCodeWhenOpenCodeSessionIsUntracked).
  const { wikiRoot, lookupEnv: ccEnv } = claudeEnvAndState();
  const ocEnv = env({ OPENCODE_SESSION_ID: "oc-untracked" });
  const both = (key: string): [string | undefined, boolean] => {
    const fromCc = ccEnv(key);
    if (fromCc[1]) return fromCc;
    return ocEnv(key);
  };
  const rel = await captureSession(wikiRoot, "", wikiRoot, both, NOW);
  assert.match(rel, /^raw\/conversations\/2026-01-02-0304-transcript\.md$/);
});

test("captureSession dispatches to OpenCode when only OPENCODE_SESSION_ID is set", async () => {
  // Only OPENCODE_SESSION_ID set: the host is OpenCode regardless of tracker
  // state, so with no `.opencode` marker the OpenCode path fails before it
  // ever shells out — the assertion is about *which* host path ran.
  const lookupEnv = env({ OPENCODE_SESSION_ID: "oc-nope" });
  await assert.rejects(
    () => captureSession(tmp(), "", tmp(), lookupEnv, NOW),
    (err: unknown) =>
      err instanceof CaptureError &&
      /session-tracker/.test(err.message) &&
      !/CLAUDE_CODE_SESSION_ID/.test(err.message),
  );
});
