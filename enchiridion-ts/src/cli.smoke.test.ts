/**
 * Full-CLI smoke tests against the esbuild-bundled `dist/cli.cjs` artifact
 * (#266). One smoke test per subcommand, each asserting stdout + exit code
 * against a representative golden input. This complements cli.test.ts (which
 * runs `tsx src/cli.ts`): the bundle's `.cjs` + `.wasm` sidecar are the real
 * artifacts under test here — no ts-node, no source maps in the smoke run.
 *
 * The same file runs under both runtimes via the CI matrix (`npm test` on
 * Node.js, `npm run test:bun` on Bun, both globbing `src/*.test.ts`). Each
 * test spawns the current process's runtime (`process.execPath` — node under
 * `npm test`, bun under `npm run test:bun`) against the bundle, so the Node
 * leg exercises `node dist/cli.cjs` and the Bun leg `bun dist/cli.cjs`.
 *
 * Requires `npm run build` first so `dist/cli.cjs` (and its
 * `node-sqlite3-wasm.wasm` sidecar) exists. When it doesn't, every test is
 * skipped with a pointer to the build step rather than failing — so the
 * module tests can run standalone.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as git from "isomorphic-git";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const distCli = path.join(__dirname, "..", "dist", "cli.cjs");

// The current process's runtime — `process.execPath` is the node binary under
// `npm test` and the bun binary under `npm run test:bun`, so the one spawn
// covers both legs of the CI matrix.
const runtimeName = process.versions.bun !== undefined ? "bun" : "node";

// Skipped (not failed) when the bundle isn't built, so the module tests can
// run without it; CI and the verification workflow always build first.
const skipReason = fs.existsSync(distCli)
  ? false
  : "dist/cli.cjs not built — run `npm run build` first";

/** Run the bundled CLI synchronously, layering optional env/cwd/stdin. */
function runBundled(
  args: string[],
  opts: { cwd?: string; env?: Record<string, string>; input?: string } = {},
): { status: number | null; stdout: string; stderr: string } {
  const result = spawnSync(process.execPath, [distCli, ...args], {
    encoding: "utf8",
    cwd: opts.cwd,
    env: opts.env ? { ...process.env, ...opts.env } : undefined,
    input: opts.input,
  });
  return {
    status: result.status,
    stdout: result.stdout,
    stderr: result.stderr,
  };
}

/** Write a temp markdown page and return its absolute path. */
function writeTempPage(frontmatter: string, body: string): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-smoke-page-"));
  const file = path.join(dir, "page.md");
  fs.writeFileSync(file, frontmatter + body);
  return file;
}

/** Scaffold a small committed vault with one searchable page. */
async function buildCommittedVault(): Promise<string> {
  const root = fs.mkdtempSync(
    path.join(os.tmpdir(), "enchiridion-smoke-vault-"),
  );
  fs.writeFileSync(path.join(root, ".wiki-root"), "");
  await git.init({ fs, dir: root });
  const write = (rel: string, content: string) => {
    const abs = path.join(root, rel);
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    fs.writeFileSync(abs, content);
  };
  write(
    "wiki/concepts/connection-pooling.md",
    "---\ntitle: Connection Pooling\nsummary: Reuse connections instead of opening a new one per request.\ntags:\n  - database\nvolatility: stable\nsource_date: 2026-01-01\n---\n\nConnection pooling reduces per-request handshake overhead by reusing a fixed set of open connections across callers.\n",
  );
  write(
    "wiki/concepts/sourdough-starter.md",
    "---\ntitle: Feeding a Sourdough Starter\nsummary: Daily flour-and-water feeding keeps a starter active.\ntags:\n  - baking\nvolatility: stable\nsource_date: 2026-01-02\n---\n\nA sourdough starter needs equal parts flour and water once a day, kept warm, to stay active enough to leaven bread.\n",
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

// ---------------------------------------------------------------------------
// search
// ---------------------------------------------------------------------------

test(
  `[${runtimeName}] search: query, --status, --reindex against the bundle`,
  { skip: skipReason },
  async () => {
    const root = await buildCommittedVault();

    const query = runBundled(["search", "pooling"], {
      cwd: root,
      env: { WIKI_ROOT: root },
    });
    assert.equal(query.status, 0, query.stderr);
    assert.match(query.stdout, /wiki\/concepts\/connection-pooling\.md/);

    const status = runBundled(["search", "--status"], {
      cwd: root,
      env: { WIKI_ROOT: root },
    });
    assert.equal(status.status, 0, status.stderr);
    assert.match(status.stdout, /pages:\s+2/);
    assert.match(status.stdout, /backend:\s+fts5/);
    assert.match(status.stdout, /schema_version:\s+4/);

    const reindex = runBundled(["search", "--reindex"], {
      cwd: root,
      env: { WIKI_ROOT: root },
    });
    assert.equal(reindex.status, 0, reindex.stderr);
    assert.match(reindex.stdout, /^reindex: 2 pages/);

    const full = runBundled(["search", "--reindex", "--full"], {
      cwd: root,
      env: { WIKI_ROOT: root },
    });
    assert.equal(full.status, 0, full.stderr);
    assert.match(full.stdout, /^full reindex: 2 pages/);

    const asJSON = runBundled(["search", "--json", "pooling"], {
      cwd: root,
      env: { WIKI_ROOT: root },
    });
    assert.equal(asJSON.status, 0, asJSON.stderr);
    const hit = JSON.parse(asJSON.stdout.trim().split("\n")[0]);
    assert.equal(hit.page_ref, "wiki/concepts/connection-pooling.md");
  },
);

test(
  `[${runtimeName}] search: bad --date-field is a flag-parsing error`,
  { skip: skipReason },
  async () => {
    const root = await buildCommittedVault();
    const r = runBundled(["search", "--date-field", "bogus", "x"], {
      cwd: root,
      env: { WIKI_ROOT: root },
    });
    assert.notEqual(r.status, 0);
    assert.match(r.stderr, /source_date' or 'git_date/);
  },
);

// ---------------------------------------------------------------------------
// init
// ---------------------------------------------------------------------------

test(
  `[${runtimeName}] init: scaffolds a vault, commits it, prints the root`,
  { skip: skipReason },
  () => {
    const root = path.join(
      fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-smoke-init-")),
      "vault",
    );
    const { status, stdout, stderr } = runBundled([
      "init",
      root,
      "--mode",
      "dedicated",
    ]);
    assert.equal(status, 0, stderr);
    assert.equal(stdout.trim(), path.resolve(root));
    for (const folder of ["concepts", "entities", "sources", "synthesis"]) {
      assert.ok(fs.existsSync(path.join(root, "wiki", folder, ".gitkeep")));
    }
    assert.ok(fs.existsSync(path.join(root, "raw", ".gitkeep")));
    assert.ok(fs.existsSync(path.join(root, ".gitignore")));
    const { status: logStatus } = spawnSync("git", ["-C", root, "log"], {
      encoding: "utf8",
    });
    assert.equal(logStatus, 0);
  },
);

// ---------------------------------------------------------------------------
// ingest
// ---------------------------------------------------------------------------

test(
  `[${runtimeName}] ingest: executes a real plan, printing the commit SHA`,
  { skip: skipReason },
  async () => {
    const root = fs.mkdtempSync(
      path.join(os.tmpdir(), "enchiridion-smoke-ingest-"),
    );
    fs.writeFileSync(path.join(root, ".wiki-root"), "");
    await git.init({ fs, dir: root });
    await git.commit({
      fs,
      dir: root,
      message: "initial",
      author: {
        name: "test",
        email: "t@e.com",
        timestamp: 1,
        timezoneOffset: 0,
      },
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

    const { status, stdout, stderr } = runBundled(
      ["ingest", "--plan", planPath],
      { cwd: root, env: { WIKI_ROOT: root, CLAUDE_CODE_SESSION_ID: "" } },
    );
    assert.equal(status, 0, stderr);
    assert.match(stdout.split("\n")[0], /^[0-9a-f]{40}$/);
    assert.ok(fs.existsSync(path.join(root, "wiki", "sources", "doc.md")));
  },
);

// ---------------------------------------------------------------------------
// discover
// ---------------------------------------------------------------------------

test(
  `[${runtimeName}] discover: single-page mode emits one candidate per line`,
  { skip: skipReason },
  async () => {
    const root = await buildCommittedVault();
    const { status, stdout, stderr } = runBundled(
      ["discover", "--title", "Connection Pooling in Postgres"],
      { cwd: root, env: { WIKI_ROOT: root } },
    );
    assert.equal(status, 0, stderr);
    const lines = stdout.trim().split("\n");
    assert.ok(lines.length > 0);
    assert.equal(
      JSON.parse(lines[0]).page_ref,
      "wiki/concepts/connection-pooling.md",
    );
  },
);

// ---------------------------------------------------------------------------
// ingest-scan
// ---------------------------------------------------------------------------

test(
  `[${runtimeName}] ingest-scan: lists an eligible raw file`,
  { skip: skipReason },
  async () => {
    const root = fs.mkdtempSync(
      path.join(os.tmpdir(), "enchiridion-smoke-iscan-"),
    );
    fs.mkdirSync(path.join(root, "raw"), { recursive: true });
    fs.mkdirSync(path.join(root, "wiki", "concepts"), { recursive: true });
    fs.writeFileSync(path.join(root, "raw", "foo.md"), "raw");
    fs.writeFileSync(
      path.join(root, "wiki", "concepts", "a.md"),
      "---\ntitle: A\n---\n\n",
    );
    fs.writeFileSync(path.join(root, ".wiki-root"), "");

    const { status, stdout, stderr } = runBundled(["ingest-scan", "--json"], {
      cwd: root,
      env: { WIKI_ROOT: root },
    });
    assert.equal(status, 0, stderr);
    const records = stdout
      .trim()
      .split("\n")
      .map((l) => JSON.parse(l));
    assert.ok(records.length >= 1);
    assert.equal(records[0].kind, "eligible");
    assert.equal(records[0].raw_rel, "raw/foo.md");
  },
);

// ---------------------------------------------------------------------------
// watch
// ---------------------------------------------------------------------------

test(
  `[${runtimeName}] watch --dequeue: removes one queue entry and exits`,
  { skip: skipReason },
  () => {
    const root = fs.mkdtempSync(
      path.join(os.tmpdir(), "enchiridion-smoke-watch-"),
    );
    const wk = path.join(root, ".wiki-knowledge");
    fs.mkdirSync(wk, { recursive: true });
    fs.writeFileSync(
      path.join(wk, "watch-queue.jsonl"),
      "raw/a.md\nraw/b.md\n",
    );
    fs.writeFileSync(path.join(root, ".wiki-root"), "");

    const { status, stderr } = runBundled(["watch", "--dequeue", "raw/a.md"], {
      cwd: root,
      env: { WIKI_ROOT: root },
    });
    assert.equal(status, 0, stderr);
    const remaining = fs
      .readFileSync(path.join(wk, "watch-queue.jsonl"), "utf8")
      .trim()
      .split("\n");
    assert.deepEqual(remaining, ["raw/b.md"]);
  },
);

test(
  `[${runtimeName}] watch: SIGTERM exits cleanly and removes the lock`,
  { skip: skipReason },
  async () => {
    const root = fs.mkdtempSync(
      path.join(os.tmpdir(), "enchiridion-smoke-watch-"),
    );
    fs.mkdirSync(path.join(root, "raw"), { recursive: true });
    fs.writeFileSync(path.join(root, ".wiki-root"), "");
    const lock = path.join(root, ".wiki-knowledge", "watch.lock");

    const child = spawn(
      process.execPath,
      [distCli, "watch", "--poll-interval", "1"],
      {
        cwd: root,
        env: { ...process.env, WIKI_ROOT: root },
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    let out = "";
    child.stdout.on("data", (d: Buffer) => {
      out += d.toString();
    });
    child.stderr.on("data", (d: Buffer) => {
      out += d.toString();
    });

    await new Promise<void>((resolve, reject) => {
      const deadline = setTimeout(
        () => reject(new Error("watch never started")),
        10000,
      );
      const probe = setInterval(() => {
        if (out.includes("watching")) {
          clearTimeout(deadline);
          clearInterval(probe);
          resolve();
        }
      }, 50);
    });

    child.kill("SIGTERM");

    const exitCode = await new Promise<number | null>((resolve) => {
      child.on("close", (code) => resolve(code));
    });
    assert.equal(exitCode, 0);
    assert.match(out, /watcher stopped/);
    assert.ok(!fs.existsSync(lock), "lock file should be removed on exit");
  },
);

// ---------------------------------------------------------------------------
// save-session (Claude Code host path)
// ---------------------------------------------------------------------------

test(
  `[${runtimeName}] save-session: writes a raw capture via the Claude Code path`,
  { skip: skipReason },
  () => {
    const project = fs.mkdtempSync(
      path.join(os.tmpdir(), "enchiridion-smoke-ss-"),
    );
    const vault = path.join(project, "vault");
    fs.mkdirSync(vault, { recursive: true });
    fs.writeFileSync(path.join(vault, ".wiki-root"), "");

    const stateDir = path.join(
      project,
      ".claude",
      "wiki-knowledge",
      "sessions",
    );
    fs.mkdirSync(stateDir, { recursive: true });
    const sessionID = "abc123-deadbeef";
    const transcript = path.join(project, `${sessionID}.jsonl`);
    fs.writeFileSync(
      transcript,
      [
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
      ].join("\n"),
    );
    fs.writeFileSync(
      path.join(stateDir, `${sessionID}.json`),
      JSON.stringify({ transcript_path: transcript }),
    );

    // The OpenCode host path is NOT exercised here: it shells out to `opencode
    // export`, an external tool that isn't guaranteed present or scriptable in
    // CI. The Claude Code path (below) is self-contained and covers the same
    // capture-and-write seam.
    const { status, stdout, stderr } = runBundled(
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
  },
);

// ---------------------------------------------------------------------------
// tool-call-stats
// ---------------------------------------------------------------------------

test(
  `[${runtimeName}] tool-call-stats: prints the summary for a session log`,
  { skip: skipReason },
  () => {
    const project = fs.mkdtempSync(
      path.join(os.tmpdir(), "enchiridion-smoke-tcs-"),
    );
    const sessionID = "abc123-deadbeef";
    const logDir = path.join(project, ".claude", "wiki-knowledge", "sessions");
    fs.mkdirSync(logDir, { recursive: true });
    fs.writeFileSync(
      path.join(logDir, `${sessionID}-tool-calls.jsonl`),
      `${JSON.stringify({ tool: "Bash", prompt_id: "p1" })}\n` +
        `${JSON.stringify({ tool: "Bash", prompt_id: "p1" })}\n` +
        `${JSON.stringify({ tool: "Read", prompt_id: "p2" })}\n`,
    );

    const { status, stdout, stderr } = runBundled(
      ["tool-call-stats", "--session-id", sessionID],
      { cwd: project },
    );
    assert.equal(status, 0, stderr);
    assert.match(stdout, /Total tool calls: 3/);
    assert.match(stdout, /Bash/);
  },
);

// ---------------------------------------------------------------------------
// commit
// ---------------------------------------------------------------------------

test(
  `[${runtimeName}] commit: writes one structured commit, printing the SHA`,
  { skip: skipReason },
  async () => {
    const root = fs.mkdtempSync(
      path.join(os.tmpdir(), "enchiridion-smoke-commit-"),
    );
    fs.writeFileSync(path.join(root, ".wiki-root"), "");
    await git.init({ fs, dir: root });
    await git.commit({
      fs,
      dir: root,
      message: "initial",
      author: {
        name: "test",
        email: "t@e.com",
        timestamp: 1,
        timezoneOffset: 0,
      },
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

    const { status, stdout, stderr } = runBundled(
      ["commit", "--manifest", manifest],
      { cwd: root, env: { WIKI_ROOT: root } },
    );
    assert.equal(status, 0, stderr);
    assert.match(stdout.trim(), /^[0-9a-f]{40}$/);
  },
);

// ---------------------------------------------------------------------------
// superseded-by
// ---------------------------------------------------------------------------

test(
  `[${runtimeName}] superseded-by: resolves a seed to its current head`,
  { skip: skipReason },
  async () => {
    const root = await buildCommittedVault();
    fs.writeFileSync(
      path.join(root, "wiki", "concepts", "old.md"),
      "---\ntitle: Old\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n\n",
    );
    fs.writeFileSync(
      path.join(root, "wiki", "concepts", "new.md"),
      '---\ntitle: New\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\nsupersedes:\n  - "[Old](old.md)"\n---\n\n',
    );

    const { status, stdout, stderr } = runBundled(
      ["superseded-by", "wiki/concepts/old.md"],
      { cwd: root, env: { WIKI_ROOT: root } },
    );
    assert.equal(status, 0, stderr);
    assert.equal(
      stdout.trim(),
      "wiki/concepts/old.md  ->  wiki/concepts/new.md",
    );
  },
);

// ---------------------------------------------------------------------------
// vault (root + move)
// ---------------------------------------------------------------------------

test(
  `[${runtimeName}] vault: bare/root prints the resolved root`,
  { skip: skipReason },
  () => {
    const root = fs.mkdtempSync(
      path.join(os.tmpdir(), "enchiridion-smoke-vault-"),
    );
    fs.mkdirSync(path.join(root, "wiki"));
    const bare = runBundled(["vault"], {
      cwd: root,
      env: { WIKI_ROOT: root },
    });
    assert.equal(bare.status, 0, bare.stderr);
    assert.equal(bare.stdout.trim(), fs.realpathSync(root));

    const sub = runBundled(["vault", "root"], {
      cwd: root,
      env: { WIKI_ROOT: root },
    });
    assert.equal(sub.status, 0, sub.stderr);
    assert.equal(sub.stdout.trim(), fs.realpathSync(root));
  },
);

test(
  `[${runtimeName}] vault move: moves a page and fixes inbound links`,
  { skip: skipReason },
  () => {
    const root = fs.mkdtempSync(
      path.join(os.tmpdir(), "enchiridion-smoke-vault-"),
    );
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

    const { status, stdout, stderr } = runBundled(
      ["vault", "move", "wiki/concepts/b.md", "wiki/entities/b.md"],
      { cwd: root, env: { WIKI_ROOT: root } },
    );
    assert.equal(status, 0, stderr);
    assert.equal(stdout.trim(), "wiki/concepts/a.md\nwiki/entities/b.md");
    assert.ok(
      fs
        .readFileSync(path.join(root, "wiki", "concepts", "a.md"), "utf8")
        .includes("../entities/b.md"),
    );
  },
);

// ---------------------------------------------------------------------------
// page (get / set / merge)
// ---------------------------------------------------------------------------

test(
  `[${runtimeName}] page: get, set, and merge frontmatter`,
  { skip: skipReason },
  () => {
    const file = writeTempPage("---\nkind: concept\n---\nbody\n", "");

    const get = runBundled(["page", "get", file, "kind"]);
    assert.equal(get.status, 0, get.stderr);
    assert.equal(get.stdout.trim(), "concept");

    const set = runBundled(["page", "set", file, "volatility", "stable"]);
    assert.equal(set.status, 0, set.stderr);
    assert.equal(
      fs.readFileSync(file, "utf8"),
      "---\nkind: concept\nvolatility: stable\n---\nbody\n",
    );

    const mergeFile = writeTempPage("---\ntags:\n  - a\n---\nbody\n", "");
    const merge = runBundled([
      "page",
      "merge",
      mergeFile,
      "tags",
      '["b", "a"]',
    ]);
    assert.equal(merge.status, 0, merge.stderr);
    assert.equal(
      fs.readFileSync(mergeFile, "utf8"),
      "---\ntags:\n  - a\n  - b\n---\nbody\n",
    );
  },
);

// ---------------------------------------------------------------------------
// place
// ---------------------------------------------------------------------------

test(
  `[${runtimeName}] place: computes the vault-relative path from kind and title`,
  { skip: skipReason },
  () => {
    const { status, stdout, stderr } = runBundled([
      "place",
      "concept",
      "Connection Pooling",
    ]);
    assert.equal(status, 0, stderr);
    assert.equal(stdout.trim(), "wiki/concepts/connection-pooling.md");
  },
);

// ---------------------------------------------------------------------------
// hook (session-start + post-tool-use)
// ---------------------------------------------------------------------------

test(
  `[${runtimeName}] hook: session-start and post-tool-use read stdin and exit 0`,
  { skip: skipReason },
  () => {
    const project = fs.mkdtempSync(
      path.join(os.tmpdir(), "enchiridion-smoke-hook-"),
    );
    const sessionID = "hook-sess-1";
    const payload = JSON.stringify({
      session_id: sessionID,
      transcript_path: "/x/transcript.jsonl",
      cwd: project,
    });
    const start = runBundled(["hook", "session-start"], { input: payload });
    assert.equal(start.status, 0, start.stderr);
    const stateDir = path.join(
      project,
      ".claude",
      "wiki-knowledge",
      "sessions",
    );
    assert.equal(
      fs.readFileSync(path.join(stateDir, `${sessionID}.json`), "utf8"),
      JSON.stringify({ transcript_path: "/x/transcript.jsonl" }),
    );

    const toolPayload = JSON.stringify({
      session_id: sessionID,
      cwd: project,
      tool_name: "Bash",
      tool_use_id: "tu_1",
      prompt_id: "pr_1",
      duration_ms: 42,
    });
    const tool = runBundled(["hook", "post-tool-use"], { input: toolPayload });
    assert.equal(tool.status, 0, tool.stderr);
    const lines = fs
      .readFileSync(
        path.join(stateDir, `${sessionID}-tool-calls.jsonl`),
        "utf8",
      )
      .trim()
      .split("\n");
    assert.equal(lines.length, 1);
    assert.equal(JSON.parse(lines[0]).tool, "Bash");
  },
);
