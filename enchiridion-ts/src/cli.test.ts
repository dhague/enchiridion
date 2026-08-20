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
import * as git from "isomorphic-git";

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

/** Run the CLI with `input` piped to its stdin. */
function runWithStdin(
  args: string[],
  input: string,
): {
  status: number | null;
  stdout: string;
  stderr: string;
} {
  const result = spawnSync(tsxBin, [cliPath, ...args], {
    encoding: "utf8",
    input,
  });
  return {
    status: result.status,
    stdout: result.stdout,
    stderr: result.stderr,
  };
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

test("vault (bare): prints the resolved root", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-vault-"));
  fs.mkdirSync(path.join(root, "wiki"));
  const { status, stdout, stderr } = runEnv(["vault"], {
    cwd: root,
    env: { WIKI_ROOT: root },
  });
  assert.equal(status, 0, stderr);
  assert.equal(stdout.trim(), fs.realpathSync(root));
});

test("vault root: prints the resolved root", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-vault-"));
  fs.writeFileSync(path.join(root, ".wiki-root"), "");
  const { status, stdout, stderr } = runEnv(["vault", "root"], {
    cwd: root,
    env: { WIKI_ROOT: root },
  });
  assert.equal(status, 0, stderr);
  assert.equal(stdout.trim(), fs.realpathSync(root));
});

test("vault move: moves a page and fixes inbound links", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-vault-"));
  fs.mkdirSync(path.join(root, "wiki", "concepts"), { recursive: true });
  fs.mkdirSync(path.join(root, "wiki", "entities"), { recursive: true });
  fs.writeFileSync(
    path.join(root, "wiki", "concepts", "a.md"),
    "See [B](b.md).\n",
  );
  fs.writeFileSync(
    path.join(root, "wiki", "concepts", "b.md"),
    "Back to [A](a.md).\n",
  );
  const { status, stdout, stderr } = runEnv(
    ["vault", "move", "wiki/concepts/b.md", "wiki/entities/b.md"],
    { cwd: root, env: { WIKI_ROOT: root } },
  );
  assert.equal(status, 0, stderr);
  // The moved page is newly written and the referencing page changed — both
  // are reported, sorted (matches MovePage's changed set).
  assert.equal(stdout.trim(), "wiki/concepts/a.md\nwiki/entities/b.md");
  assert.equal(
    fs.existsSync(path.join(root, "wiki", "concepts", "b.md")),
    false,
  );
  assert.ok(
    fs
      .readFileSync(path.join(root, "wiki", "concepts", "a.md"), "utf8")
      .includes("../entities/b.md"),
  );
});

test("vault move: wrong argument count errors non-zero", () => {
  const { status } = run(["vault", "move", "only-one-arg"]);
  assert.notEqual(status, 0);
});

test("vault move: missing source errors non-zero", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-vault-"));
  fs.mkdirSync(path.join(root, "wiki"));
  const { status } = runEnv(
    ["vault", "move", "wiki/concepts/missing.md", "wiki/entities/missing.md"],
    { cwd: root, env: { WIKI_ROOT: root } },
  );
  assert.notEqual(status, 0);
});

test("vault move resolves the vault root; place resolves none (boundary)", () => {
  // `place` is pure path computation: it must succeed from a directory with
  // no vault marker and no WIKI_ROOT, resolving no vault root at all.
  const plain = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-vault-"));
  const place = runEnv(["place", "concept", "A Thing"], {
    cwd: plain,
    env: { WIKI_ROOT: "" },
  });
  assert.equal(place.status, 0, place.stderr);
  assert.equal(place.stdout.trim(), "wiki/concepts/a-thing.md");

  // A `vault` subcommand with no marker anywhere above cwd and no WIKI_ROOT
  // falls back to cwd as the root (ADR-0004 step 3) — so `vault root` prints
  // the cwd itself, not a path computed in isolation.
  const vault = runEnv(["vault", "root"], {
    cwd: plain,
    env: { WIKI_ROOT: "" },
  });
  assert.equal(vault.status, 0, vault.stderr);
  assert.equal(vault.stdout.trim(), fs.realpathSync(plain));
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

test("hook session-start: reads stdin, records transcript path, exits 0", () => {
  const project = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-hook-"));
  const sessionID = "hook-sess-1";
  const payload = JSON.stringify({
    session_id: sessionID,
    transcript_path: "/x/transcript.jsonl",
    cwd: project,
  });
  const { status, stderr } = runWithStdin(["hook", "session-start"], payload);
  assert.equal(status, 0, stderr);
  const stateDir = path.join(project, ".claude", "wiki-knowledge", "sessions");
  assert.equal(
    fs.readFileSync(path.join(stateDir, `${sessionID}.json`), "utf8"),
    JSON.stringify({ transcript_path: "/x/transcript.jsonl" }),
  );
});

test("hook session-start: malformed stdin fails open, exits 0", () => {
  const { status, stderr } = runWithStdin(
    ["hook", "session-start"],
    "not json",
  );
  assert.equal(status, 0, stderr);
});

test("hook post-tool-use: reads stdin, appends one JSON line, exits 0", () => {
  const project = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-hook-"));
  const sessionID = "hook-sess-2";
  const payload = JSON.stringify({
    session_id: sessionID,
    cwd: project,
    tool_name: "Bash",
    tool_use_id: "tu_1",
    prompt_id: "pr_1",
    duration_ms: 42,
  });
  const { status, stderr } = runWithStdin(["hook", "post-tool-use"], payload);
  assert.equal(status, 0, stderr);
  const logDir = path.join(project, ".claude", "wiki-knowledge", "sessions");
  const lines = fs
    .readFileSync(path.join(logDir, `${sessionID}-tool-calls.jsonl`), "utf8")
    .trim()
    .split("\n");
  assert.equal(lines.length, 1);
  const event = JSON.parse(lines[0]);
  assert.equal(event.tool, "Bash");
  assert.equal(event.duration_ms, 42);
});

test("hook post-tool-use: malformed stdin fails open, exits 0", () => {
  const { status, stderr } = runWithStdin(
    ["hook", "post-tool-use"],
    "not json",
  );
  assert.equal(status, 0, stderr);
});

test("hook (bare): errors listing the events, non-zero", () => {
  const { status, stderr } = run(["hook"]);
  assert.notEqual(status, 0);
  assert.match(stderr, /name the event, one of session-start, post-tool-use/);
});

test("init: scaffolds a vault, commits it, and prints the root", () => {
  const root = path.join(
    fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-init-")),
    "vault",
  );
  const { status, stdout, stderr } = run(["init", root, "--mode", "dedicated"]);
  assert.equal(status, 0, stderr);
  assert.equal(stdout.trim(), path.resolve(root));
  for (const folder of ["concepts", "entities", "sources", "synthesis"]) {
    assert.ok(
      fs.existsSync(path.join(root, "wiki", folder, ".gitkeep")),
      `${folder} missing`,
    );
  }
  assert.ok(fs.existsSync(path.join(root, "raw", ".gitkeep")));
  assert.ok(fs.existsSync(path.join(root, ".gitignore")));
  // The scaffold is committed — the vault's git history is complete from page
  // one.
  const { status: logStatus } = spawnSync("git", ["-C", root, "log"], {
    encoding: "utf8",
  });
  assert.equal(logStatus, 0);
});

test("init: requires --mode", () => {
  const root = path.join(
    fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-init-")),
    "vault",
  );
  const { status, stderr } = run(["init", root]);
  assert.notEqual(status, 0);
  assert.match(stderr, /mode/);
});

test("init: refuses a directory that already looks like a vault", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-init-"));
  fs.mkdirSync(path.join(root, "wiki"));
  const { status, stderr } = run(["init", root, "--mode", "dedicated"]);
  assert.notEqual(status, 0);
  assert.match(stderr, /already looks like a vault/);
});

test("init: query-from-anywhere requires --plugin-root", () => {
  const root = path.join(
    fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-init-")),
    "vault",
  );
  const { status, stderr } = run([
    "init",
    root,
    "--mode",
    "query-from-anywhere",
  ]);
  assert.notEqual(status, 0);
  assert.match(stderr, /requires a plugin root/);
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

test("ingest: executes a plan against a real git vault, printing the SHA first", async () => {
  const root = fs.mkdtempSync(
    path.join(os.tmpdir(), "enchiridion-cli-ingest-"),
  );
  fs.writeFileSync(path.join(root, ".wiki-root"), "");
  await git.init({ fs, dir: root });
  await git.commit({
    fs,
    dir: root,
    message: "initial",
    author: { name: "test", email: "t@e.com", timestamp: 1, timezoneOffset: 0 },
    committer: {
      name: "test",
      email: "t@e.com",
      timestamp: 1,
      timezoneOffset: 0,
    },
  });
  fs.mkdirSync(path.join(root, "raw"), { recursive: true });
  fs.writeFileSync(path.join(root, "raw", "doc.md"), "raw\n");
  fs.mkdirSync(path.join(root, "wiki", "concepts"), { recursive: true });
  fs.writeFileSync(
    path.join(root, "wiki", "concepts", "a.md"),
    "---\ntitle: A\n---\nbody\n",
  );

  const planPath = path.join(root, "plan.json");
  fs.writeFileSync(
    planPath,
    JSON.stringify({
      title: "Deploy notes",
      action: "ingest",
      source_date: "2026-03-01",
      raw: "raw/doc.md",
      pages: [
        {
          op: "create",
          title: "Doc",
          kind: "source",
          body: "stub body\n",
          frontmatter: { summary: "the doc", raw_source: true },
        },
        {
          op: "create",
          title: "Prepared Statements",
          kind: "concept",
          body: "page body\n",
          frontmatter: { summary: "s" },
          edges: { source: ["wiki/sources/doc.md"] },
        },
        {
          op: "update",
          title: "A",
          page_ref: "wiki/concepts/a.md",
          body: "new body\n",
          edges: { source: ["wiki/sources/doc.md"] },
        },
      ],
    }),
  );

  const { status, stdout, stderr } = runEnv(["ingest", "--plan", planPath], {
    cwd: root,
    env: { WIKI_ROOT: root, CLAUDE_CODE_SESSION_ID: "" },
  });
  assert.equal(status, 0, stderr);
  // The commit SHA is always the first line of stdout.
  const firstLine = stdout.split("\n")[0];
  assert.match(firstLine, /^[0-9a-f]{40}$/);
  // The pages were written and committed.
  assert.ok(fs.existsSync(path.join(root, "wiki", "sources", "doc.md")));
  assert.ok(
    fs.existsSync(
      path.join(root, "wiki", "concepts", "prepared-statements.md"),
    ),
  );
  const { status: logStatus } = spawnSync(
    "git",
    ["-C", root, "log", "--oneline"],
    {
      encoding: "utf8",
    },
  );
  assert.equal(logStatus, 0);
});

test("ingest: --dry-run prints the describe, writes nothing", async () => {
  const root = fs.mkdtempSync(
    path.join(os.tmpdir(), "enchiridion-cli-ingest-"),
  );
  fs.writeFileSync(path.join(root, ".wiki-root"), "");
  await git.init({ fs, dir: root });
  await git.commit({
    fs,
    dir: root,
    message: "initial",
    author: { name: "test", email: "t@e.com", timestamp: 1, timezoneOffset: 0 },
    committer: {
      name: "test",
      email: "t@e.com",
      timestamp: 1,
      timezoneOffset: 0,
    },
  });

  const planPath = path.join(root, "plan.json");
  fs.writeFileSync(
    planPath,
    JSON.stringify({
      title: "T",
      pages: [{ op: "create", title: "A", kind: "concept", body: "b\n" }],
    }),
  );

  const { status, stdout, stderr } = runEnv(
    ["ingest", "--plan", planPath, "--dry-run"],
    { cwd: root, env: { WIKI_ROOT: root } },
  );
  assert.equal(status, 0, stderr);
  assert.equal(stdout.trim(), "ingest: T\n  create wiki/concepts/a.md");
  assert.ok(!fs.existsSync(path.join(root, "wiki", "concepts", "a.md")));
});

test("ingest: --plan and --ignore are mutually exclusive, one required", () => {
  const { status, stderr } = run(["ingest"]);
  assert.notEqual(status, 0);
  assert.match(stderr, /--plan|--ignore/);
});

test("ingest: --ignore appends to the folder's .ingestignore", () => {
  const root = fs.mkdtempSync(
    path.join(os.tmpdir(), "enchiridion-cli-ingest-"),
  );
  fs.writeFileSync(path.join(root, ".wiki-root"), "");
  fs.mkdirSync(path.join(root, "raw", "emails"), { recursive: true });
  fs.writeFileSync(path.join(root, "raw", "emails", "foo.eml"), "x");

  const { status, stderr } = runEnv(
    ["ingest", "--ignore", "raw/emails/foo.eml", "--ignore-comment", "done"],
    { cwd: root, env: { WIKI_ROOT: root } },
  );
  assert.equal(status, 0, stderr);
  const ignoreFile = fs.readFileSync(
    path.join(root, "raw", "emails", ".ingestignore"),
    "utf8",
  );
  assert.equal(ignoreFile, "foo.eml  # done\n");
});

test("ingest: --ignore rejects a path outside raw/", () => {
  const root = fs.mkdtempSync(
    path.join(os.tmpdir(), "enchiridion-cli-ingest-"),
  );
  fs.writeFileSync(path.join(root, ".wiki-root"), "");
  const { status, stderr } = runEnv(["ingest", "--ignore", "notes/x.md"], {
    cwd: root,
    env: { WIKI_ROOT: root },
  });
  assert.notEqual(status, 0);
  assert.match(stderr, /under raw\//);
});

test("commit: writes one structured commit per manifest, printing the SHA", async () => {
  const root = fs.mkdtempSync(
    path.join(os.tmpdir(), "enchiridion-cli-commit-"),
  );
  fs.writeFileSync(path.join(root, ".wiki-root"), "");
  await git.init({ fs, dir: root });
  await git.commit({
    fs,
    dir: root,
    message: "initial",
    author: { name: "test", email: "t@e.com", timestamp: 1, timezoneOffset: 0 },
    committer: {
      name: "test",
      email: "t@e.com",
      timestamp: 1,
      timezoneOffset: 0,
    },
  });

  fs.mkdirSync(path.join(root, "wiki", "concepts"), { recursive: true });
  fs.writeFileSync(
    path.join(root, "wiki", "concepts", "a.md"),
    "---\ntitle: A\n---\nbody\n",
  );

  const manifest = path.join(root, "manifest.json");
  fs.writeFileSync(
    manifest,
    JSON.stringify({ title: "T", created: ["wiki/concepts/a.md"] }),
  );

  const { status, stdout, stderr } = runEnv(
    ["commit", "--manifest", manifest],
    {
      cwd: root,
      env: { WIKI_ROOT: root },
    },
  );
  assert.equal(status, 0, stderr);
  assert.match(stdout.trim(), /^[0-9a-f]{40}$/);
});

test("commit: a missing --manifest flag errors non-zero", () => {
  const { status, stderr } = run(["commit"]);
  assert.notEqual(status, 0);
  assert.match(stderr, /manifest/);
});

test("commit: a manifest failing chain-of-evidence is rejected non-zero", async () => {
  const root = fs.mkdtempSync(
    path.join(os.tmpdir(), "enchiridion-cli-commit-"),
  );
  fs.mkdirSync(path.join(root, "wiki", "concepts"), { recursive: true });
  fs.writeFileSync(
    path.join(root, "wiki", "concepts", "a.md"),
    "---\ntitle: A\n---\nbody\n",
  );
  fs.writeFileSync(path.join(root, ".wiki-root"), "");

  await git.init({ fs, dir: root });
  await git.add({ fs, dir: root, filepath: "." });
  await git.commit({
    fs,
    dir: root,
    message: "initial",
    author: { name: "test", email: "t@e.com", timestamp: 1, timezoneOffset: 0 },
    committer: {
      name: "test",
      email: "t@e.com",
      timestamp: 1,
      timezoneOffset: 0,
    },
  });

  const manifest = path.join(root, "manifest.json");
  fs.writeFileSync(
    manifest,
    JSON.stringify({
      title: "T",
      created: ["wiki/concepts/a.md"],
      raw_source: "raw/doc.md",
    }),
  );

  const { status, stderr } = runEnv(["commit", "--manifest", manifest], {
    cwd: root,
    env: { WIKI_ROOT: root },
  });
  assert.notEqual(status, 0);
  assert.match(stderr, /needs a sources\/ page/);
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

test("superseded-by: resolves a seed to its current head", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ssby-"));
  fs.mkdirSync(path.join(root, "wiki", "concepts"), { recursive: true });
  fs.writeFileSync(
    path.join(root, "wiki", "concepts", "old.md"),
    "---\ntitle: Old\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n\n",
  );
  fs.writeFileSync(
    path.join(root, "wiki", "concepts", "new.md"),
    '---\ntitle: New\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\nsupersedes:\n  - "[Old](old.md)"\n---\n\n',
  );
  fs.writeFileSync(path.join(root, ".wiki-root"), "");

  const { status, stdout, stderr } = runEnv(
    ["superseded-by", "wiki/concepts/old.md"],
    { cwd: root, env: { WIKI_ROOT: root } },
  );
  assert.equal(status, 0, stderr);
  assert.equal(stdout.trim(), "wiki/concepts/old.md  ->  wiki/concepts/new.md");
});

test("superseded-by: a current page prints (current)", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ssby-"));
  fs.mkdirSync(path.join(root, "wiki", "concepts"), { recursive: true });
  fs.writeFileSync(
    path.join(root, "wiki", "concepts", "a.md"),
    "---\ntitle: A\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n\n",
  );
  fs.writeFileSync(path.join(root, ".wiki-root"), "");

  const { status, stdout, stderr } = runEnv(
    ["superseded-by", "wiki/concepts/a.md"],
    { cwd: root, env: { WIKI_ROOT: root } },
  );
  assert.equal(status, 0, stderr);
  assert.equal(stdout.trim(), "wiki/concepts/a.md  (current)");
});

test("superseded-by: no args errors non-zero", () => {
  const { status } = run(["superseded-by"]);
  assert.notEqual(status, 0);
});

test("ingest-scan: lists eligible raw files", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-iscan-"));
  fs.mkdirSync(path.join(root, "raw"), { recursive: true });
  fs.mkdirSync(path.join(root, "wiki", "concepts"), { recursive: true });
  fs.writeFileSync(path.join(root, "raw", "foo.md"), "raw");
  fs.writeFileSync(
    path.join(root, "wiki", "concepts", "a.md"),
    "---\ntitle: A\n---\n\n",
  );
  fs.writeFileSync(path.join(root, ".wiki-root"), "");

  const { status, stdout, stderr } = runEnv(["ingest-scan", "--json"], {
    cwd: root,
    env: { WIKI_ROOT: root },
  });
  assert.equal(status, 0, stderr);
  const records = stdout
    .trim()
    .split("\n")
    .map((l) => JSON.parse(l));
  assert.equal(records.length, 1);
  assert.equal(records[0].kind, "eligible");
  assert.equal(records[0].raw_rel, "raw/foo.md");
  assert.equal(records[0].reason, "never-ingested");
});

test("watch --dequeue: removes one queue entry and exits", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-watch-"));
  const wk = path.join(root, ".wiki-knowledge");
  fs.mkdirSync(wk, { recursive: true });
  fs.writeFileSync(path.join(wk, "watch-queue.jsonl"), "raw/a.md\nraw/b.md\n");
  fs.writeFileSync(path.join(root, ".wiki-root"), "");

  const { status, stderr } = runEnv(["watch", "--dequeue", "raw/a.md"], {
    cwd: root,
    env: { WIKI_ROOT: root },
  });
  assert.equal(status, 0, stderr);
  const remaining = fs
    .readFileSync(path.join(wk, "watch-queue.jsonl"), "utf8")
    .trim()
    .split("\n");
  assert.deepEqual(remaining, ["raw/b.md"]);
});

test("watch: without --dequeue requires a lock and errors when held", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-watch-"));
  const wk = path.join(root, ".wiki-knowledge");
  fs.mkdirSync(wk, { recursive: true });
  fs.mkdirSync(path.join(root, "raw"), { recursive: true });
  fs.writeFileSync(path.join(root, ".wiki-root"), "");
  // A live lock (this pid is alive) makes a second watcher refuse to start.
  fs.writeFileSync(
    path.join(wk, "watch.lock"),
    JSON.stringify({ pid: process.pid, started_at: new Date().toISOString() }),
  );

  const { status, stderr } = runEnv(["watch", "--poll-interval", "1"], {
    cwd: root,
    env: { WIKI_ROOT: root },
  });
  assert.notEqual(status, 0);
  assert.match(stderr, /another watcher is already running/);
});

// ---------------------------------------------------------------------------
// discover — needs a committed vault (search is a view of committed history)
// ---------------------------------------------------------------------------

/** Write a page under a vault's wiki/, then init+commit the whole vault. */
async function buildCommittedVault(): Promise<string> {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-disc-"));
  fs.writeFileSync(path.join(root, ".wiki-root"), "");
  await git.init({ fs, dir: root });

  const write = (rel: string, content: string) => {
    const abs = path.join(root, rel);
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    fs.writeFileSync(abs, content);
  };
  write(
    "wiki/concepts/connection-pooling.md",
    "---\ntitle: Connection Pooling in Postgres\nsummary: Reuse connections instead of opening a new one per request.\ntags:\n  - database\nvolatility: stable\n---\n\nConnection pooling reduces per-request handshake overhead by reusing a fixed set of open connections across callers.\n",
  );
  write(
    "wiki/concepts/sourdough-starter.md",
    "---\ntitle: Feeding a Sourdough Starter\nsummary: Daily flour-and-water feeding keeps a starter active.\ntags:\n  - baking\nvolatility: stable\n---\n\nA sourdough starter needs equal parts flour and water once a day, kept warm, to stay active enough to leaven bread.\n",
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

test("discover: single-page mode finds the overlapping page and emits one candidate per line", async () => {
  const root = await buildCommittedVault();
  const { status, stdout, stderr } = runEnv(
    ["discover", "--title", "Connection Pooling in Postgres"],
    { cwd: root, env: { WIKI_ROOT: root } },
  );
  assert.equal(status, 0, stderr);
  const lines = stdout.trim().split("\n");
  assert.ok(lines.length > 0);
  const first = JSON.parse(lines[0]);
  assert.equal(first.page_ref, "wiki/concepts/connection-pooling.md");
});

test("discover: --plan emits the pages and vocabulary payload", async () => {
  const root = await buildCommittedVault();
  const planPath = path.join(root, "draft.json");
  fs.writeFileSync(
    planPath,
    JSON.stringify({
      title: "Draft",
      pages: [
        {
          op: "create",
          title: "Connection Pooling in Postgres",
          kind: "concept",
          frontmatter: { summary: "" },
          body: "",
        },
      ],
    }),
  );
  const { status, stdout, stderr } = runEnv(["discover", "--plan", planPath], {
    cwd: root,
    env: { WIKI_ROOT: root },
  });
  assert.equal(status, 0, stderr);
  const payload = JSON.parse(stdout);
  assert.ok(Array.isArray(payload.pages));
  assert.ok(Array.isArray(payload.vocabulary));
  assert.equal(payload.pages.length, 1);
  assert.equal(payload.pages[0].title, "Connection Pooling in Postgres");
});

test("discover: --plan - reads the draft from stdin", async () => {
  const root = await buildCommittedVault();
  const draft = JSON.stringify({
    title: "Draft",
    pages: [
      {
        op: "create",
        title: "Feeding a Sourdough Starter",
        kind: "concept",
        frontmatter: { summary: "" },
        body: "",
      },
    ],
  });
  const result = spawnSync(tsxBin, [cliPath, "discover", "--plan", "-"], {
    encoding: "utf8",
    input: draft,
    cwd: root,
    env: { ...process.env, WIKI_ROOT: root },
  });
  assert.equal(result.status, 0, result.stderr);
  const payload = JSON.parse(result.stdout);
  assert.ok(Array.isArray(payload.pages));
  assert.equal(payload.pages.length, 1);
});

test("discover: --plan with --tags-containing emits the bracket list", async () => {
  const root = await buildCommittedVault();
  const planPath = path.join(root, "draft.json");
  fs.writeFileSync(planPath, JSON.stringify({ title: "Draft", pages: [] }));
  const { status, stdout, stderr } = runEnv(
    ["discover", "--plan", planPath, "--tags-containing", "data"],
    { cwd: root, env: { WIKI_ROOT: root } },
  );
  assert.equal(status, 0, stderr);
  const lines = stdout.trim().split("\n");
  // pages payload is indented JSON spanning several lines; the bracket list is
  // the final line.
  const last = lines[lines.length - 1];
  assert.match(last, /^\[.*database.*\]$/);
});
