/**
 * ingestscan tests — the raw/ eligibility sweep and its .ingestignore policy.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import * as git from "isomorphic-git";
import { VaultGit } from "./vaultgit.js";
import {
  ReasonChangedSinceIngestion,
  ReasonNeverIngested,
  scan,
  walkRaw,
  type Candidate,
  type Git,
} from "./ingestscan.js";

// fakeGit scripts the lenient git facts the sweep reads, mirroring
// wiki-plugin/tests/fake_vault_git.py's last_commit_dates/dirty state.
class fakeGit implements Git {
  constructor(
    readonly lastCommitDates: Record<string, string>,
    readonly dirty: Record<string, boolean>,
  ) {}

  async lastCommitDate(rel: string): Promise<string> {
    return this.lastCommitDates[rel] ?? "";
  }

  async porcelainMentions(rel: string): Promise<boolean> {
    return this.dirty[rel] ?? false;
  }
}

function tmpRoot(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ingestscan-test-"));
}

function write(root: string, rel: string, content: string): void {
  const p = path.join(root, ...rel.split("/"));
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, content);
}

function seedVault(root: string): void {
  for (const dir of [
    "wiki/concepts",
    "wiki/entities",
    "wiki/sources",
    "wiki/synthesis",
    "raw",
  ]) {
    fs.mkdirSync(path.join(root, ...dir.split("/")), { recursive: true });
  }
}

function deterministicSignature(offsetHours: number) {
  const base = new Date("2026-01-01T00:00:00Z").getTime() / 1000;
  const offset = offsetHours * 3600;
  return {
    name: "test",
    email: "test@example.com",
    timestamp: Math.floor(base + offset),
    timezoneOffset: 0,
  };
}

async function commitAll(root: string, message: string): Promise<string> {
  await git.add({ fs, dir: root, filepath: "." });
  return git.commit({
    fs,
    dir: root,
    message,
    author: deterministicSignature(1),
    committer: deterministicSignature(1),
  });
}

// --- WalkRaw -----------------------------------------------------------------

test("walkRaw yields every file recursively", () => {
  const root = tmpRoot();
  seedVault(root);
  write(root, "raw/a.md", "a");
  write(root, "raw/notes/b.md", "b");
  assert.deepEqual(walkRaw(root, ""), ["raw/a.md", "raw/notes/b.md"]);
});

test("walkRaw skips instructions and policy", () => {
  const root = tmpRoot();
  seedVault(root);
  write(root, "raw/INGESTION.md", "hints");
  write(root, "raw/.ingestignore", "*.tmp\n");
  write(root, "raw/real.md", "r");
  assert.deepEqual(walkRaw(root, ""), ["raw/real.md"]);
});

test("walkRaw scoped to one folder", () => {
  const root = tmpRoot();
  seedVault(root);
  write(root, "raw/a.md", "a");
  write(root, "raw/notes/b.md", "b");
  assert.deepEqual(walkRaw(root, "notes"), ["raw/notes/b.md"]);
});

test("walkRaw missing folder yields nothing", () => {
  const root = tmpRoot();
  seedVault(root);
  assert.deepEqual(walkRaw(root, "nope"), []);
});

// --- Scan: ignored -----------------------------------------------------------

test("scan: file matching its own folder's .ingestignore is ignored", async () => {
  const root = tmpRoot();
  seedVault(root);
  write(root, "raw/.ingestignore", "*.tmp\n");
  write(root, "raw/foo.tmp", "junk");
  write(root, "raw/real.md", "real");

  const result = await scan(root, "", null);
  assert.deepEqual(rawRels(result.eligible), ["raw/real.md"]);
  assert.deepEqual(result.ignored, ["raw/foo.tmp"]);
});

test("scan: parent's .ingestignore does not apply", async () => {
  const root = tmpRoot();
  seedVault(root);
  write(root, "raw/.ingestignore", "*.tmp\n");
  write(root, "raw/notes/foo.tmp", "junk");

  const result = await scan(root, "", null);
  assert.deepEqual(rawRels(result.eligible), ["raw/notes/foo.tmp"]);
  assert.deepEqual(result.ignored, []);
});

test("scan: own folder's .ingestignore overrides back-pointers", async () => {
  const root = tmpRoot();
  seedVault(root);
  write(
    root,
    "wiki/sources/foo.md",
    '---\ntitle: Foo\nraw_source: "[foo.md](../../raw/foo.md)"\n---\n# Foo\n',
  );
  write(root, "raw/.ingestignore", "foo.md\n");
  write(root, "raw/foo.md", "raw");

  const result = await scan(root, "", null);
  assert.deepEqual(result.eligible, []);
  assert.deepEqual(result.ignored, ["raw/foo.md"]);
});

// --- Scan: eligibility -------------------------------------------------------

test("scan: file with no back-pointer is never-ingested", async () => {
  const root = tmpRoot();
  seedVault(root);
  write(root, "raw/foo.md", "raw");

  const result = await scan(root, "", null);
  assert.equal(result.eligible.length, 1);
  const cand = result.eligible[0];
  assert.equal(cand.rawRel, "raw/foo.md");
  assert.equal(cand.reason, ReasonNeverIngested);
  assert.deepEqual(cand.backPointers, []);
});

test("scan: file with back-pointer but no git is still offered", async () => {
  const root = tmpRoot();
  seedVault(root);
  write(
    root,
    "wiki/sources/foo.md",
    '---\ntitle: Foo\nraw_source: "[foo.md](../../raw/foo.md)"\n---\n# Foo\n',
  );
  write(root, "raw/foo.md", "raw");

  // No git here: the real repo's lenient surface returns ""/false, and the
  // absent page date fails toward offering.
  const result = await scan(root, "", null);
  assert.equal(result.eligible.length, 1);
  const cand = result.eligible[0];
  assert.equal(cand.reason, ReasonChangedSinceIngestion);
  assert.deepEqual(cand.backPointers, ["wiki/sources/foo.md"]);
});

test("scan: percent-encoded raw_source still matches", async () => {
  const root = tmpRoot();
  seedVault(root);
  write(
    root,
    "wiki/sources/my-notes.md",
    "---\ntitle: My Notes\n" +
      'raw_source: "[My Notes (draft).md](../../raw/My%20Notes%20%28draft%29.md)"\n' +
      "---\n# My Notes\n",
  );
  write(root, "raw/My Notes (draft).md", "raw");

  const result = await scan(root, "", null);
  assert.equal(result.eligible.length, 1);
  const cand = result.eligible[0];
  assert.equal(cand.rawRel, "raw/My Notes (draft).md");
  assert.equal(cand.reason, ReasonChangedSinceIngestion);
  assert.deepEqual(cand.backPointers, ["wiki/sources/my-notes.md"]);
});

// --- Scan: git-backed eligibility -------------------------------------------

test("scan: strictly-newer fake git facts offer the file", async () => {
  const root = tmpRoot();
  seedVault(root);
  write(
    root,
    "wiki/sources/foo.md",
    '---\ntitle: Foo\nraw_source: "[foo.md](../../raw/foo.md)"\n---\n# Foo\n',
  );
  write(root, "raw/foo.md", "raw");
  const gitFake = new fakeGit(
    {
      "raw/foo.md": "2026-02-01",
      "wiki/sources/foo.md": "2026-01-01",
    },
    {},
  );

  const result = await scan(root, "", gitFake);
  assert.equal(result.eligible.length, 1);
  const cand = result.eligible[0];
  assert.equal(cand.reason, ReasonChangedSinceIngestion);
  assert.equal(cand.rawRel, "raw/foo.md");
});

test("scan: dirty fake overrides equal dates", async () => {
  const root = tmpRoot();
  seedVault(root);
  write(
    root,
    "wiki/sources/foo.md",
    '---\ntitle: Foo\nraw_source: "[foo.md](../../raw/foo.md)"\n---\n# Foo\n',
  );
  write(root, "raw/foo.md", "raw");
  const gitFake = new fakeGit(
    { "raw/foo.md": "2026-01-01", "wiki/sources/foo.md": "2026-01-01" },
    { "raw/foo.md": true },
  );

  const result = await scan(root, "", gitFake);
  assert.equal(result.eligible.length, 1);
  assert.equal(result.eligible[0].reason, ReasonChangedSinceIngestion);
});

test("scan: same commit means not offered", async () => {
  const root = tmpRoot();
  const repo = new VaultGit(root);
  await repo.init();
  write(root, "raw/notes.md", "raw notes");
  write(
    root,
    "wiki/sources/notes.md",
    '---\ntitle: Notes\nraw_source: "[notes.md](../../raw/notes.md)"\n---\n# Notes\n',
  );
  await commitAll(root, "ingest notes");

  const result = await scan(root, "", null);
  assert.equal(result.eligible.length, 0);
  assert.equal(result.ignored.length, 0);
});

test("scan: dirty working tree overrides date equality", async () => {
  const root = tmpRoot();
  const repo = new VaultGit(root);
  await repo.init();
  write(root, "raw/notes.md", "raw notes");
  write(
    root,
    "wiki/sources/notes.md",
    '---\ntitle: Notes\nraw_source: "[notes.md](../../raw/notes.md)"\n---\n# Notes\n',
  );
  await commitAll(root, "ingest notes");
  // Edit the raw file but DON'T commit; dirty status flips the offer.
  write(root, "raw/notes.md", "raw notes v2 (uncommitted)");

  const result = await scan(root, "", null);
  assert.equal(result.eligible.length, 1);
  assert.equal(result.eligible[0].reason, ReasonChangedSinceIngestion);
});

test("scan: staged (but uncommitted) modification is detected (#366)", async () => {
  const root = tmpRoot();
  const repo = new VaultGit(root);
  await repo.init();
  write(root, "raw/notes.md", "raw notes");
  write(
    root,
    "wiki/sources/notes.md",
    '---\ntitle: Notes\nraw_source: "[notes.md](../../raw/notes.md)"\n---\n# Notes\n',
  );
  await commitAll(root, "ingest notes");
  write(root, "raw/notes.md", "raw notes v2 (staged only)");
  await git.add({ fs, dir: root, filepath: "raw/notes.md" });

  const result = await scan(root, "", null);
  assert.equal(result.eligible.length, 1);
  assert.equal(result.eligible[0].reason, ReasonChangedSinceIngestion);
});

test("scan: raw file in subfolder matched by back-pointer in wiki/sources", async () => {
  // Regression test for #299: raw/notes/foo.md linked via "../../raw/notes/foo.md"
  // must not be reported as never-ingested — the path resolution was never broken,
  // but wiki/_index.md (see next test) prevented the back-pointer map from forming.
  const root = tmpRoot();
  seedVault(root);
  write(root, "raw/notes/foo.md", "raw notes content");
  write(
    root,
    "wiki/sources/foo-notes.md",
    '---\ntitle: Foo\nraw_source: "[foo.md](../../raw/notes/foo.md)"\n---\n# Foo\n',
  );

  // No git: lenient defaults mean the file is offered (changed-since-ingestion),
  // but it must NOT be reported as never-ingested.
  const result = await scan(root, "", null);
  assert.equal(result.eligible.length, 1);
  const cand = result.eligible[0];
  assert.equal(cand.rawRel, "raw/notes/foo.md");
  assert.equal(cand.reason, ReasonChangedSinceIngestion);
  assert.deepEqual(cand.backPointers, ["wiki/sources/foo-notes.md"]);
});

test("scan: wiki/_index.md does not break back-pointer recognition", async () => {
  // Regression test for #299: wiki/_index.md was included in page enumeration,
  // causing loadRecords to throw and preventing any back-pointer from being built.
  const root = tmpRoot();
  seedVault(root);
  write(root, "wiki/_index.md", "generated table of contents\n");
  write(root, "raw/notes/foo.md", "raw notes content");
  write(
    root,
    "wiki/sources/foo-notes.md",
    '---\ntitle: Foo\nraw_source: "[foo.md](../../raw/notes/foo.md)"\n---\n# Foo\n',
  );

  const result = await scan(root, "", null);
  assert.equal(result.eligible.length, 1);
  const cand = result.eligible[0];
  assert.equal(cand.rawRel, "raw/notes/foo.md");
  assert.equal(cand.reason, ReasonChangedSinceIngestion);
  assert.deepEqual(cand.backPointers, ["wiki/sources/foo-notes.md"]);
});

test("scan: wiki page with CRLF line endings is recognised as a back-pointer", async () => {
  // On Windows, git core.autocrlf=true converts LF → CRLF on checkout.
  const root = tmpRoot();
  seedVault(root);
  write(root, "raw/notes/foo.md", "raw notes content");
  write(
    root,
    "wiki/sources/foo-notes.md",
    '---\r\ntitle: Foo\r\nraw_source: "[foo.md](../../raw/notes/foo.md)"\r\n---\r\n# Foo\r\n',
  );

  const result = await scan(root, "", null);
  assert.equal(result.eligible.length, 1);
  const cand = result.eligible[0];
  assert.equal(cand.rawRel, "raw/notes/foo.md");
  assert.equal(cand.reason, ReasonChangedSinceIngestion);
  assert.deepEqual(cand.backPointers, ["wiki/sources/foo-notes.md"]);
});

test("scan: raw_source casing mismatch does not re-offer as never-ingested (#368)", async () => {
  // LLM agent may title-case the filename in the plan's raw field, producing a
  // raw_source link whose decoded path differs only in case from the actual file.
  // On a case-insensitive filesystem (Windows/macOS) these are the same file;
  // the scanner must not classify the file as never-ingested.
  const root = tmpRoot();
  seedVault(root);
  // File on disk: uppercase "RE"
  write(root, "raw/emails/RE Are we test.eml", "raw email content");
  // raw_source decoded target: "raw/emails/Re Are we test.eml" (title-cased by LLM)
  write(
    root,
    "wiki/sources/re-are-we-test.md",
    "---\ntitle: RE Are we test\n" +
      'raw_source: "[RE Are we test.eml](../../raw/emails/Re%20Are%20we%20test.eml)"\n' +
      "---\n# RE Are we test\n",
  );

  const result = await scan(root, "", null);
  assert.equal(result.eligible.length, 1);
  const cand = result.eligible[0];
  assert.equal(cand.rawRel, "raw/emails/RE Are we test.eml");
  assert.equal(cand.reason, ReasonChangedSinceIngestion);
  assert.deepEqual(cand.backPointers, ["wiki/sources/re-are-we-test.md"]);
});

// --- Scan: shape -------------------------------------------------------------

test("scan: malformed .ingestignore is an error", async () => {
  const root = tmpRoot();
  seedVault(root);
  write(root, "raw/.ingestignore", "sub/*.tmp\n"); // a '/' is rejected
  write(root, "raw/foo.md", "raw");

  await assert.rejects(() => scan(root, "", null));
});

test("scan: empty vault yields empty results", async () => {
  const root = tmpRoot();
  seedVault(root);
  const result = await scan(root, "", null);
  assert.equal(result.eligible.length, 0);
  assert.equal(result.ignored.length, 0);
});

test("scan: scoped to one folder", async () => {
  const root = tmpRoot();
  seedVault(root);
  write(root, "raw/a.md", "a");
  write(root, "raw/notes/b.md", "b");

  const result = await scan(root, "notes", null);
  assert.deepEqual(rawRels(result.eligible), ["raw/notes/b.md"]);
});

function rawRels(cands: Candidate[]): string[] {
  return cands.map((c) => c.rawRel);
}
