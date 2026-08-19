/**
 * CLI-level smoke tests (#252's "Testing Decisions": one test per
 * subcommand is enough at this level — correctness lives in module tests
 * once those land; these confirm the commander wiring).
 *
 * Spawns `tsx src/cli.ts` as a subprocess for each case so commander's own
 * process.exit()/process.exitCode calls behave exactly as they would for a
 * real invocation, without taking down the test runner.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const cliPath = path.join(__dirname, "cli.ts");
const tsxBin = path.join(__dirname, "..", "node_modules", ".bin", "tsx");

function run(args: string[]): {
  status: number | null;
  stdout: string;
  stderr: string;
} {
  return runEnv(args, {});
}

/** Run the CLI with a custom cwd and/or extra env layered over the current. */
function runEnv(
  args: string[],
  opts: { cwd?: string; env?: Record<string, string> } = {},
): {
  status: number | null;
  stdout: string;
  stderr: string;
} {
  const result = spawnSync(tsxBin, [cliPath, ...args], {
    encoding: "utf8",
    cwd: opts.cwd,
    env: opts.env ? { ...process.env, ...opts.env } : undefined,
  });
  return {
    status: result.status,
    stdout: result.stdout,
    stderr: result.stderr,
  };
}

/** Write a temp markdown page and return its path. */
function writeTempPage(frontmatter: string, body: string): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-page-"));
  const file = path.join(dir, "page.md");
  fs.writeFileSync(file, frontmatter + body);
  return file;
}

test("no arguments: prints help, exits 0 (parity with the Go/cobra binary)", () => {
  const { status, stdout } = run([]);
  assert.equal(status, 0);
  assert.match(stdout, /Usage:/);
  assert.match(stdout, /enchiridion/);
});

test("--help: prints help, exits 0", () => {
  const { status, stdout } = run(["--help"]);
  assert.equal(status, 0);
  assert.match(stdout, /Usage:/);
});

for (const name of ["search", "init", "ingest", "discover", "watch"]) {
  test(`${name}: stub exits non-zero with "not yet implemented"`, () => {
    const { status, stderr } = run([name]);
    assert.notEqual(status, 0);
    assert.match(stderr, /not yet implemented/);
  });
}

test("place: prints the vault-relative path from kind and title", () => {
  const { status, stdout, stderr } = run([
    "place",
    "concept",
    "Connection Pooling",
  ]);
  assert.equal(status, 0, stderr);
  assert.equal(stdout.trim(), "wiki/concepts/connection-pooling.md");
});

test("place: errors non-zero on an unknown kind", () => {
  const { status, stderr } = run(["place", "nonsense", "X"]);
  assert.notEqual(status, 0);
  assert.match(stderr, /unknown kind "nonsense"/);
});

test("place: errors on wrong argument count", () => {
  const { status } = run(["place", "concept"]);
  assert.notEqual(status, 0);
});

test("vault (bare): stub exits non-zero", () => {
  const { status, stderr } = run(["vault"]);
  assert.notEqual(status, 0);
  assert.match(stderr, /not yet implemented/);
});

test("vault root: stub exits non-zero", () => {
  const { status, stderr } = run(["vault", "root"]);
  assert.notEqual(status, 0);
  assert.match(stderr, /not yet implemented/);
});

test("vault move: stub exits non-zero", () => {
  const { status, stderr } = run(["vault", "move", "old.md", "new.md"]);
  assert.notEqual(status, 0);
  assert.match(stderr, /not yet implemented/);
});

test("page get: prints the frontmatter value", () => {
  const file = writeTempPage("---\nkind: concept\n---\nbody\n", "");
  const { status, stdout } = run(["page", "get", file, "kind"]);
  assert.equal(status, 0);
  assert.equal(stdout.trim(), "concept");
});

test("page get: prints a list as ['a', 'b']", () => {
  const file = writeTempPage("---\ntags:\n  - a\n  - b\n---\nbody\n", "");
  const { status, stdout } = run(["page", "get", file, "tags"]);
  assert.equal(status, 0);
  assert.equal(stdout.trim(), "['a', 'b']");
});

test("page get: absent key exits non-zero", () => {
  const file = writeTempPage("---\nkind: concept\n---\nbody\n", "");
  const { status } = run(["page", "get", file, "nope"]);
  assert.notEqual(status, 0);
});

test("page set: writes the file back", () => {
  const file = writeTempPage("---\nkind: concept\n---\nbody\n", "");
  const { status } = run(["page", "set", file, "volatility", "stable"]);
  assert.equal(status, 0);
  assert.equal(
    fs.readFileSync(file, "utf8"),
    "---\nkind: concept\nvolatility: stable\n---\nbody\n",
  );
});

test("page merge: unions values into a list-valued key", () => {
  const file = writeTempPage("---\ntags:\n  - a\n---\nbody\n", "");
  const { status } = run(["page", "merge", file, "tags", '["b", "a"]']);
  assert.equal(status, 0);
  assert.equal(
    fs.readFileSync(file, "utf8"),
    "---\ntags:\n  - a\n  - b\n---\nbody\n",
  );
});

test("hook session-start: stub exits non-zero", () => {
  const { status, stderr } = run(["hook", "session-start"]);
  assert.notEqual(status, 0);
  assert.match(stderr, /not yet implemented/);
});

test("save-session: writes a raw capture and prints its vault-relative path", () => {
  const project = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ss-"));
  const vault = path.join(project, "vault");
  fs.mkdirSync(vault, { recursive: true });
  fs.writeFileSync(path.join(vault, ".wiki-root"), "");

  const stateDir = path.join(project, ".claude", "wiki-knowledge", "sessions");
  fs.mkdirSync(stateDir, { recursive: true });
  const sessionID = "abc123-deadbeef";
  const transcript = path.join(project, `${sessionID}.jsonl`);
  const lines = [
    JSON.stringify({
      type: "user",
      isMeta: false,
      isSidechain: false,
      message: { role: "user", content: "hello" },
    }),
    JSON.stringify({
      type: "assistant",
      isMeta: false,
      isSidechain: false,
      message: { role: "assistant", content: "world" },
    }),
  ];
  fs.writeFileSync(transcript, lines.join("\n"));
  fs.writeFileSync(
    path.join(stateDir, `${sessionID}.json`),
    JSON.stringify({ transcript_path: transcript }),
  );

  // cwd must be inside `project` (which carries `.claude`) so the SessionStart
  // hook's state is found; WIKI_ROOT points at the vault. OPENCODE_SESSION_ID is
  // cleared so an inherited value can't divert this onto the OpenCode path.
  const { status, stdout, stderr } = runEnv(
    ["save-session", "--slug", "a session"],
    {
      cwd: project,
      env: {
        CLAUDE_CODE_SESSION_ID: sessionID,
        OPENCODE_SESSION_ID: "",
        WIKI_ROOT: vault,
      },
    },
  );
  assert.equal(status, 0, stderr);
  const rel = stdout.trim();
  assert.match(
    rel,
    /^raw\/conversations\/\d{4}-\d{2}-\d{2}-\d{4}-a-session-abc123\.md$/,
  );
  assert.ok(fs.existsSync(path.join(vault, ...rel.split("/"))));
});

test("save-session: errors and exits non-zero when no session id is set", () => {
  const project = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ss-"));
  const vault = path.join(project, "vault");
  fs.mkdirSync(vault, { recursive: true });
  fs.writeFileSync(path.join(vault, ".wiki-root"), "");

  const { status, stderr } = runEnv(["save-session"], {
    cwd: project,
    env: {
      WIKI_ROOT: vault,
      // Explicitly clear any inherited session-id vars (the outer process may
      // run inside a real session) so this exercises the no-id path.
      CLAUDE_CODE_SESSION_ID: "",
      OPENCODE_SESSION_ID: "",
    },
  });
  assert.notEqual(status, 0);
  assert.match(stderr, /CLAUDE_CODE_SESSION_ID|OPENCODE_SESSION_ID/);
});

test("tool-call-stats: prints the summary for a session log", () => {
  const project = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-tcs-"));
  const sessionID = "abc123-deadbeef";
  const logDir = path.join(project, ".claude", "wiki-knowledge", "sessions");
  fs.mkdirSync(logDir, { recursive: true });
  fs.writeFileSync(
    path.join(logDir, `${sessionID}-tool-calls.jsonl`),
    `${JSON.stringify({ tool: "Bash", prompt_id: "p1" })}\n` +
      `${JSON.stringify({ tool: "Bash", prompt_id: "p1" })}\n` +
      `${JSON.stringify({ tool: "Read", prompt_id: "p2" })}\n`,
  );

  const { status, stdout, stderr } = runEnv(
    ["tool-call-stats", "--session-id", sessionID],
    {
      cwd: project,
    },
  );
  assert.equal(status, 0, stderr);
  assert.match(stdout, /Total tool calls: 3/);
  assert.match(stdout, /Bash/);
  assert.match(stdout, /Read/);
  assert.match(stdout, /1\.5 calls\/prompt/);
});

test("tool-call-stats: errors when no session id is set", () => {
  const project = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-tcs-"));
  const { status, stderr } = runEnv(["tool-call-stats"], {
    cwd: project,
    env: { CLAUDE_CODE_SESSION_ID: "" },
  });
  assert.notEqual(status, 0);
  assert.match(stderr, /no session_id/);
});

test("tool-call-stats: errors when no log exists for the session", () => {
  const project = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-tcs-"));
  const { status, stderr } = runEnv(
    ["tool-call-stats", "--session-id", "nope-none"],
    { cwd: project },
  );
  assert.notEqual(status, 0);
  assert.match(stderr, /no log found/);
});

test("unknown command: commander itself errors non-zero", () => {
  const { status } = run(["totally-bogus-command"]);
  assert.notEqual(status, 0);
});

test("place: prints the vault-relative path for a valid kind and title", () => {
  const { status, stdout } = run(["place", "concept", "Connection Pooling"]);
  assert.equal(status, 0);
  assert.equal(stdout.trim(), "wiki/concepts/connection-pooling.md");
});

test("place: unknown kind errors non-zero", () => {
  const { status, stderr } = run(["place", "nonsense", "X"]);
  assert.notEqual(status, 0);
  assert.match(stderr, /unknown kind/);
});
