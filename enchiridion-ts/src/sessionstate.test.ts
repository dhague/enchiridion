/**
 * Unit tests for the sessionstate module (#257). Uses mock env lookups and
 * temp-dir state so no real Claude Code environment or hook is touched.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  sessionsDir,
  writeTranscriptPath,
  readTranscriptPath,
} from "./sessionstate.js";
import type { LookupEnv } from "./sessionstate.js";

function tmp(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "sessionstate-test-"));
}

/** Build a LookupEnv from a fixed map (missing keys report not-present). */
function env(map: Record<string, string>): LookupEnv {
  return (key: string): [string | undefined, boolean] => {
    const value = map[key];
    return [value, value !== undefined];
  };
}

// ---------------------------------------------------------------------------
// sessionsDir resolution order
// ---------------------------------------------------------------------------

test("sessionsDir: injected root wins over everything", () => {
  const root = tmp();
  const got = sessionsDir(root, "/some/other/cwd", env({}));
  assert.equal(got, path.join(root, ".claude", "wiki-knowledge", "sessions"));
});

test("sessionsDir: CLAUDE_PROJECT_DIR is used when no root", () => {
  const project = tmp();
  const got = sessionsDir(
    "",
    "/some/cwd",
    env({ CLAUDE_PROJECT_DIR: project }),
  );
  assert.equal(
    got,
    path.join(project, ".claude", "wiki-knowledge", "sessions"),
  );
});

test("sessionsDir: walks up to the nearest .claude ancestor", () => {
  const root = tmp();
  const nested = path.join(root, "a", "b");
  fs.mkdirSync(path.join(root, ".claude"), { recursive: true });
  const got = sessionsDir("", nested, env({}));
  assert.equal(got, path.join(root, ".claude", "wiki-knowledge", "sessions"));
});

test("sessionsDir: falls back to cwd when no .claude ancestor exists", () => {
  const cwd = tmp();
  const got = sessionsDir("", cwd, env({}));
  assert.equal(got, path.join(cwd, ".claude", "wiki-knowledge", "sessions"));
});

test("sessionsDir: empty CLAUDE_PROJECT_DIR is ignored", () => {
  const cwd = tmp();
  const got = sessionsDir("", cwd, env({ CLAUDE_PROJECT_DIR: "" }));
  assert.equal(got, path.join(cwd, ".claude", "wiki-knowledge", "sessions"));
});

// ---------------------------------------------------------------------------
// write / read transcript path
// ---------------------------------------------------------------------------

test("writeTranscriptPath then readTranscriptPath round-trips", () => {
  const stateDir = path.join(tmp(), ".claude", "wiki-knowledge", "sessions");
  writeTranscriptPath("sess-123", "/tmp/transcript.jsonl", stateDir);
  assert.equal(
    readTranscriptPath("sess-123", stateDir),
    "/tmp/transcript.jsonl",
  );
});

test("writeTranscriptPath creates the state directory", () => {
  const stateDir = path.join(tmp(), "nested", "state");
  writeTranscriptPath("sess-1", "/t.jsonl", stateDir);
  assert.ok(fs.statSync(stateDir).isDirectory());
  const raw = fs.readFileSync(path.join(stateDir, "sess-1.json"), "utf8");
  assert.deepEqual(JSON.parse(raw), { transcript_path: "/t.jsonl" });
});

test("readTranscriptPath is undefined for a missing session", () => {
  const stateDir = path.join(tmp(), "state");
  assert.equal(readTranscriptPath("nope", stateDir), undefined);
});

test("readTranscriptPath is undefined for unparsable state", () => {
  const stateDir = path.join(tmp(), "state");
  fs.mkdirSync(stateDir, { recursive: true });
  fs.writeFileSync(path.join(stateDir, "bad.json"), "not json");
  assert.equal(readTranscriptPath("bad", stateDir), undefined);
});

test("readTranscriptPath is undefined when transcript_path is not a string", () => {
  const stateDir = path.join(tmp(), "state");
  fs.mkdirSync(stateDir, { recursive: true });
  fs.writeFileSync(
    path.join(stateDir, "x.json"),
    JSON.stringify({ transcript_path: 42 }),
  );
  assert.equal(readTranscriptPath("x", stateDir), undefined);
});
