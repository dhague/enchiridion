/**
 * commit tests — mirror enchiridion-go/internal/commit/commit_test.go (plus
 * its vaultgittest.Fake), ported to the TS module.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  commit,
  buildMessage,
  stagedPaths,
  ErrGate,
  type Git,
  type Manifest,
} from "./commit.js";
import { VaultGit } from "./vaultgit.js";
import * as git from "isomorphic-git";

// -- In-memory git fake, mirroring vaultgittest.Fake -------------------------

const ErrNoGit = new Error("fake git failure");

class Fake implements Git {
  notAWorkTree = false;
  addErr: Error | null = null;
  commitErr: Error | null = null;
  added: string[] = [];
  messages: string[] = [];

  async isWorkTree(): Promise<boolean> {
    return !this.notAWorkTree;
  }
  async add(paths: string[]): Promise<void> {
    if (this.addErr) throw this.addErr;
    this.added.push(...paths);
  }
  async commit(message: string): Promise<string> {
    if (this.commitErr) throw this.commitErr;
    this.messages.push(message);
    return this.messages.length.toString(16).padStart(40, "0");
  }
}

// ---------------------------------------------------------------------------

test("buildMessage renders the structured format", () => {
  const got = buildMessage({
    title: "Deploy notes",
    action: "ingest",
    created: ["wiki/concepts/a.md"],
    updated: ["wiki/concepts/b.md"],
    superseded: [{ old: "wiki/sources/old.md", new: "wiki/sources/new.md" }],
    source_date: "2026-03-01",
  });
  const want =
    "ingest: Deploy notes\n\n" +
    "created: wiki/concepts/a.md\n" +
    "updated: wiki/concepts/b.md\n" +
    "superseded: wiki/sources/old.md -> wiki/sources/new.md\n" +
    "source-date: 2026-03-01\n";
  assert.equal(got, want);
});

test("buildMessage omits absent sections", () => {
  assert.equal(
    buildMessage({ title: "Bare", action: "synthesize" }),
    "synthesize: Bare\n\n",
  );
});

test("buildMessage defaults the action to ingest", () => {
  assert.ok(buildMessage({ title: "T" }).startsWith("ingest: T"));
});

test("stagedPaths deduplicates in order", () => {
  const m: Manifest = {
    title: "",
    created: ["a.md", "b.md"],
    updated: ["b.md", "c.md"],
    superseded: [{ old: "c.md", new: "d.md" }],
    raw_source: "raw/doc.md",
  };
  assert.deepEqual(stagedPaths(m), [
    "a.md",
    "b.md",
    "c.md",
    "d.md",
    "raw/doc.md",
  ]);
});

// -- Commit ----------------------------------------------------------------

function stagedVault(pages: Record<string, string>): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-commit-"));
  for (const [ref, text] of Object.entries(pages)) {
    const abs = path.join(root, ...ref.split("/"));
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    fs.writeFileSync(abs, text);
  }
  return root;
}

test("commit stages and returns a SHA", async () => {
  const root = stagedVault({
    "wiki/concepts/a.md": "---\ntitle: A\n---\nbody\n",
  });
  const git = new Fake();
  const sha = await commit(
    root,
    { title: "T", created: ["wiki/concepts/a.md"] },
    git,
  );
  assert.equal(sha.length, 40);
  assert.deepEqual(git.added, ["wiki/concepts/a.md"]);
  assert.equal(git.messages.length, 1);
  assert.ok(git.messages[0].startsWith("ingest: T"));
});

test("commit requires a work tree", async () => {
  const git = new Fake();
  git.notAWorkTree = true;
  await assert.rejects(
    commit(
      fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-commit-")),
      { title: "T" },
      git,
    ),
    /is not a git work tree/,
  );
  assert.equal(git.messages.length, 0);
});

test("commit gates on chain of evidence", async () => {
  const root = stagedVault({
    "wiki/concepts/a.md": "---\ntitle: A\n---\nbody\n",
  });
  const git = new Fake();
  await assert.rejects(
    commit(
      root,
      { title: "T", created: ["wiki/concepts/a.md"], raw_source: "raw/doc.md" },
      git,
    ),
    (err: unknown) => err instanceof ErrGate,
  );
  assert.equal(git.added.length, 0);
  assert.equal(git.messages.length, 0);
});

test("commit passes the gate with a stub and back edges", async () => {
  const root = stagedVault({
    "wiki/sources/doc.md":
      '---\nraw_source: "[doc.md](../../raw/doc.md)"\n---\nstub\n',
    "wiki/concepts/a.md":
      '---\nsource:\n  - "[doc.md](../sources/doc.md)"\n---\nbody\n',
    "raw/doc.md": "raw\n",
  });
  const git = new Fake();
  await commit(
    root,
    {
      title: "T",
      created: ["wiki/sources/doc.md", "wiki/concepts/a.md"],
      raw_source: "raw/doc.md",
    },
    git,
  );
  assert.ok(git.added.includes("raw/doc.md"));
});

test("commit without a raw source skips the gate", async () => {
  const root = stagedVault({
    "wiki/synthesis/s.md": "---\ntitle: S\n---\nbody\n",
  });
  const git = new Fake();
  await commit(
    root,
    { title: "S", action: "synthesize", created: ["wiki/synthesis/s.md"] },
    git,
  );
});

test("commit with no paths still commits", async () => {
  const git = new Fake();
  await commit(
    fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-commit-")),
    { title: "empty" },
    git,
  );
  assert.deepEqual(git.added, []);
  assert.equal(git.messages.length, 1);
});

test("commit propagates a git failure", async () => {
  const git = new Fake();
  git.commitErr = ErrNoGit;
  await assert.rejects(
    commit(
      fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-commit-")),
      { title: "T" },
      git,
    ),
    (err: unknown) => err === ErrNoGit,
  );
});

// -- Integration: a real temp-dir vault + git ---------------------------------

const FIXED_SIGNATURE = {
  name: "test",
  email: "test@example.com",
  timestamp: Math.floor(new Date("2026-01-01T00:00:00Z").getTime() / 1000),
  timezoneOffset: 0,
};

/** Init a real empty git repo at root so HEAD exists (no files committed). */
async function initRepo(root: string): Promise<void> {
  await git.init({ fs, dir: root });
  await git.commit({
    fs,
    dir: root,
    message: "initial",
    author: FIXED_SIGNATURE,
    committer: FIXED_SIGNATURE,
  });
}

test("integration: writes a structured commit message and returns the SHA", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-commit-"));
  await initRepo(root);
  const pages = {
    "wiki/sources/doc.md":
      '---\nraw_source: "[doc.md](../../raw/doc.md)"\n---\nstub\n',
    "wiki/concepts/a.md":
      '---\nsource:\n  - "[doc.md](../sources/doc.md)"\n---\nbody\n',
    "raw/doc.md": "raw\n",
  };
  for (const [ref, text] of Object.entries(pages)) {
    const abs = path.join(root, ...ref.split("/"));
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    fs.writeFileSync(abs, text);
  }
  const repo = new VaultGit(root);

  const sha = await commit(
    root,
    {
      title: "Deploy notes",
      action: "ingest",
      created: ["wiki/sources/doc.md", "wiki/concepts/a.md"],
      source_date: "2026-03-01",
      raw_source: "raw/doc.md",
    },
    repo,
  );

  const log = await git.log({ fs, dir: root, depth: 1 });
  const head = log[0];
  assert.equal(head.oid, sha);
  assert.equal(
    head.commit.message,
    "ingest: Deploy notes\n\n" +
      "created: wiki/sources/doc.md\n" +
      "created: wiki/concepts/a.md\n" +
      "source-date: 2026-03-01\n",
  );
});

test("integration: a manifest failing chain-of-evidence is rejected at commit time", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-commit-"));
  await initRepo(root);
  const abs = path.join(root, "wiki", "concepts", "a.md");
  fs.mkdirSync(path.dirname(abs), { recursive: true });
  fs.writeFileSync(abs, "---\ntitle: A\n---\nbody\n");
  const repo = new VaultGit(root);

  await assert.rejects(
    commit(
      root,
      { title: "T", created: ["wiki/concepts/a.md"], raw_source: "raw/doc.md" },
      repo,
    ),
    (err: unknown) => err instanceof ErrGate,
  );
});
