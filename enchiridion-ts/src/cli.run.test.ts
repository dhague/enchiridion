/**
 * In-process `run()` tests against the esbuild-bundled `dist/cli.cjs` artifact
 * (#330). These prove the import-safe entry: importing the bundle is inert
 * (no main() run, host process survives), and `run(argv)` executes a command
 * in-process — capturing all stdout/stderr, returning the exit code, never
 * calling process.exit, and never leaving process.exitCode set or the host's
 * streams swapped.
 *
 * This is the enabler for OpenCode's plugin-native execution: a plugin imports
 * the bundle and calls run() on the embedded Bun — no `node`/`bun` on PATH.
 *
 * Requires `npm run build` first so `dist/cli.cjs` (and its
 * `node-sqlite3-wasm.wasm` sidecar) exists. When it doesn't, every test is
 * skipped with a pointer to the build step rather than failing — mirroring
 * cli.smoke.test.ts. The `watch` and `hook` subcommands are deliberately not
 * exercised through run() (long-running loop / reads stdin).
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as git from "isomorphic-git";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const distCli = path.join(__dirname, "..", "dist", "cli.cjs");

// Skipped (not failed) when the bundle isn't built, so the module tests can
// run without it; CI and the verification workflow always build first.
const skipReason = fs.existsSync(distCli)
  ? false
  : "dist/cli.cjs not built — run `npm run build` first";

interface RunResult {
  stdout: string;
  stderr: string;
  exitCode: number;
}

// Capture the host's pre-import state so the inert-import and stream-restore
// tests can assert nothing leaked. The require happens at module scope, which
// is itself part of the contract: if importing the bundle killed the host
// (the pre-#330 behaviour), this test file would never reach a single test.
const exitCodeBeforeImport = process.exitCode;
const realStdoutWrite = process.stdout.write;
const realStderrWrite = process.stderr.write;
const realConsoleLog = console.log;
const realConsoleError = console.error;

let run: (argv: string[]) => Promise<RunResult>;
if (!skipReason) {
  const require = createRequire(import.meta.url);
  ({ run } = require(distCli) as {
    run: (argv: string[]) => Promise<RunResult>;
  });
}

/** Scaffold a small committed vault with one searchable page mentioning BM25. */
async function buildCommittedVault(): Promise<string> {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-run-vault-"));
  fs.writeFileSync(path.join(root, ".wiki-root"), "");
  await git.init({ fs, dir: root });
  const write = (rel: string, content: string) => {
    const abs = path.join(root, rel);
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    fs.writeFileSync(abs, content);
  };
  write(
    "wiki/concepts/bm25-ranking.md",
    "---\ntitle: BM25 Ranking\nsummary: BM25 scores a document against a query by term frequency and inverse document frequency.\ntags:\n  - search\nvolatility: stable\nsource_date: 2026-01-01\n---\n\nBM25 is the lexical ranking function the wiki's FTS5 index scores hits with.\n",
  );
  write(
    "wiki/concepts/sourdough-starter.md",
    "---\ntitle: Feeding a Sourdough Starter\nsummary: Daily flour-and-water feeding keeps a starter active.\ntags:\n  - baking\nvolatility: stable\nsource_date: 2026-01-02\n---\n\nA sourdough starter needs equal parts flour and water once a day, kept warm.\n",
  );
  await git.add({ fs, dir: root, filepath: "." });
  await git.commit({
    fs,
    dir: root,
    message: "fixtures",
    author: { name: "test", email: "t@e.com", timestamp: 1, timezoneOffset: 0 },
    committer: {
      name: "test",
      email: "t@e.com",
      timestamp: 1,
      timezoneOffset: 0,
    },
  });
  return root;
}

test(
  "importing the bundle is inert: host survives, exitCode unset",
  { skip: skipReason },
  () => {
    // Merely reaching this test proves the import didn't process.exit the host.
    assert.equal(process.exitCode, exitCodeBeforeImport);
  },
);

test(
  "run([]): bare invocation prints usage to stdout, exitCode 0",
  { skip: skipReason },
  async () => {
    const result = await run([]);
    assert.match(result.stdout, /Usage:/);
    assert.equal(result.stderr, "");
    assert.equal(result.exitCode, 0);
    // The help path never sets it — unset (or 0) is the "not left set" bar.
    assert.ok(
      process.exitCode == null || process.exitCode === 0,
      "host exitCode left set",
    );
    assert.equal(process.stdout.write, realStdoutWrite);
    assert.equal(process.stderr.write, realStderrWrite);
    assert.equal(console.log, realConsoleLog);
    assert.equal(console.error, realConsoleError);
  },
);

test(
  "run(['--help']): help to stdout, exitCode 0",
  { skip: skipReason },
  async () => {
    const result = await run(["--help"]);
    assert.match(result.stdout, /Usage:/);
    assert.equal(result.exitCode, 0);
  },
);

test(
  "run(['place', ...]): computes a vault-relative path in-process",
  { skip: skipReason },
  async () => {
    const result = await run(["place", "concept", "Connection Pooling"]);
    assert.equal(result.stdout.trim(), "wiki/concepts/connection-pooling.md");
    assert.equal(result.stderr, "");
    assert.equal(result.exitCode, 0);
  },
);

test(
  "run(['place', 'nonsense', 'X']): error to stderr, exitCode non-zero, host exitCode untouched",
  { skip: skipReason },
  async () => {
    const result = await run(["place", "nonsense", "X"]);
    assert.notEqual(result.exitCode, 0);
    assert.match(result.stderr, /unknown kind/);
    // The failure is reported in the result, never left on the host process.
    assert.ok(
      process.exitCode == null || process.exitCode === 0,
      "host exitCode left set",
    );
  },
);

test(
  "after runs: streams restored to the real ones, host can still write",
  { skip: skipReason },
  async () => {
    await run(["place", "concept", "Restored Streams"]);
    assert.equal(process.stdout.write, realStdoutWrite);
    assert.equal(process.stderr.write, realStderrWrite);
    assert.equal(console.log, realConsoleLog);
    assert.equal(console.error, realConsoleError);
    assert.ok(
      process.exitCode == null || process.exitCode === 0,
      "host exitCode left set",
    );
    // Writing now reaches the real stream, not a captured array.
    assert.equal(process.stdout.write(""), true);
  },
);

test(
  "run(['search', ...]): FTS hits return in-process (wasm loads)",
  { skip: skipReason },
  async () => {
    const root = await buildCommittedVault();
    const prevWikiRoot = process.env.WIKI_ROOT;
    process.env.WIKI_ROOT = root;
    try {
      const result = await run(["search", "bm25", "--limit", "2"]);
      assert.equal(result.exitCode, 0, result.stderr);
      assert.match(result.stdout, /wiki\/concepts\/bm25-ranking\.md/);
      assert.equal(process.exitCode, 0);
      assert.equal(process.stdout.write, realStdoutWrite);
      assert.equal(process.stderr.write, realStderrWrite);
      assert.equal(console.log, realConsoleLog);
      assert.equal(console.error, realConsoleError);
    } finally {
      if (prevWikiRoot === undefined) delete process.env.WIKI_ROOT;
      else process.env.WIKI_ROOT = prevWikiRoot;
    }
  },
);

// save-session: OPENCODE_SESSION_ID visible to run() via process.env (#399)
//
// The wiki-enchiridion OpenCode plugin runs the bundle in-process — the
// session-tracker's shell.env hook never fires for it. The plugin therefore
// injects context.sessionID into process.env.OPENCODE_SESSION_ID before
// calling run().  This test simulates the result of that injection by setting
// OPENCODE_SESSION_ID directly in process.env, then calling run(['save-session',
// ...]).  We assert that the command moves past the "neither ID is set" check
// (i.e., it reads the env var) before failing on the tracker state check,
// which is the expected failure when no .opencode/wiki-knowledge/sessions/
// directory exists in cwd's ancestor chain.
test(
  "run(['save-session']): reads OPENCODE_SESSION_ID from process.env (not 'neither ID' error)",
  { skip: skipReason },
  async () => {
    const prevSessionID = process.env.OPENCODE_SESSION_ID;
    process.env.OPENCODE_SESSION_ID = "test-opencode-session-id";
    try {
      const result = await run(["save-session", "--slug", "test-session"]);
      // Must not succeed (no real OpenCode session), but the failure must NOT be
      // the "neither $CLAUDE_CODE_SESSION_ID nor $OPENCODE_SESSION_ID" error —
      // that error means the env var was invisible, i.e. the bug is present.
      assert.notEqual(result.exitCode, 0);
      assert.ok(
        !result.stderr.includes("Neither $CLAUDE_CODE_SESSION_ID"),
        `Expected OPENCODE_SESSION_ID to be read; got: ${result.stderr.trim()}`,
      );
      // The expected error is about the tracker state (state not located), not
      // about the ID being absent.
      assert.match(result.stderr, /OPENCODE_SESSION_ID|session-tracker/);
    } finally {
      if (prevSessionID === undefined) delete process.env.OPENCODE_SESSION_ID;
      else process.env.OPENCODE_SESSION_ID = prevSessionID;
    }
  },
);
