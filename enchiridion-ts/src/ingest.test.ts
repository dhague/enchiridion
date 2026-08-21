/**
 * ingest tests — the IngestPlan resolve/validate/execute pipeline, including
 * the fake-git seam that isolates the executor from real git.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import * as git from "isomorphic-git";
import { decodePlan, resolve, Resolved, ErrPlan, type Plan } from "./ingest.js";
import { Vault } from "./vault.js";
import { VaultGit } from "./vaultgit.js";
import type { Git } from "./commit.js";

// -- In-memory git fake, mirroring vaultgittest.Fake -------------------------

class Fake implements Git {
  notAWorkTree = false;
  added: string[] = [];
  messages: string[] = [];

  async isWorkTree(): Promise<boolean> {
    return !this.notAWorkTree;
  }
  async add(paths: string[]): Promise<void> {
    this.added.push(...paths);
  }
  async commit(message: string): Promise<string> {
    this.messages.push(message);
    return this.messages.length.toString(16).padStart(40, "0");
  }
}

// -- Helpers ---------------------------------------------------------------

/** Lays down a {pageRef: text} map under a fresh temp root. */
function newVault(files: Record<string, string>): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ingest-"));
  for (const [ref, text] of Object.entries(files)) {
    const abs = path.join(root, ...ref.split("/"));
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    fs.writeFileSync(abs, text);
  }
  return root;
}

function decodePlanOK(src: string): Plan {
  return decodePlan(src);
}

/** Resolves against root and throws if resolution errors. */
function resolveOK(plan: Plan, root: string): Resolved {
  return resolve(plan, root);
}

/** Resolves and validates, returning the error text ("" when valid). */
function validationErrors(src: string, root: string): string {
  try {
    resolveOK(decodePlanOK(src), root).validate();
    return "";
  } catch (err) {
    if (!(err instanceof ErrPlan)) throw err;
    return err.message;
  }
}

// --- decoding ---------------------------------------------------------------

test("decodePlan defaults the action to ingest", () => {
  assert.equal(decodePlanOK(`{"title":"T"}`).action, "ingest");
});

test("decodePlan keeps an explicit action", () => {
  assert.equal(
    decodePlanOK(`{"title":"T","action":"synthesize"}`).action,
    "synthesize",
  );
});

// Frontmatter keys are applied in plan order, so a plain object (JS key
// iteration is deterministic but not the plan's order) would make the written
// page vary run to run. ADR-0012 relaxes byte-identical round-tripping; it
// does not license nondeterminism.
test("frontmatter key order is preserved", () => {
  const src = `{"title":"T","pages":[{"op":"create","title":"A","kind":"concept","body":"b",
    "frontmatter":{"summary":"s","volatility":"stable","tags":["x"],"source_date":"2026-01-01"}}]}`;
  const root = newVault({});
  for (let i = 0; i < 8; i++) {
    const resolved = resolveOK(decodePlanOK(src), root);
    const text = resolved.pages[0].page!.text;
    // Key order is preserved and deterministic (ADR-0012 relaxes byte-identical
    // round-tripping, not nondeterministic output); scalar quote style is not
    // part of the contract.
    const want = [
      "title: A\n",
      "summary: s\n",
      "volatility: stable\n",
      "tags:\n",
      "  - x\n",
      "source_date:",
    ];
    let cursor = 0;
    for (const frag of want) {
      const at = text.indexOf(frag, cursor);
      assert.ok(at !== -1, `expected ${JSON.stringify(frag)} in:\n${text}`);
      cursor = at + frag.length;
    }
  }
});

// The write half of #192: a plan's timestamped `source_date` is truncated to
// its canonical date at resolve time.
test("resolve writes a canonical source_date", () => {
  const src = `{"title":"T","pages":[{"op":"create","title":"A","kind":"concept","body":"b",
    "frontmatter":{"summary":"s","source_date":"2026-07-20T14:30:00Z"}}]}`;
  const resolved = resolveOK(decodePlanOK(src), newVault({}));
  resolved.validate();
  const text = resolved.pages[0].page!.text;
  assert.ok(text.includes("2026-07-20"));
  assert.ok(!text.includes("14:30"));
});

test("decodePlan rejects malformed JSON", () => {
  assert.throws(() => decodePlan(`{"title":`), /invalid plan JSON/);
});

test("orderedMap null decodes empty", () => {
  const plan = decodePlanOK(
    `{"title":"T","pages":[{"op":"create","title":"A","frontmatter":null}]}`,
  );
  assert.equal(plan.pages[0].frontmatter.length(), 0);
});

// --- placement + link composition -------------------------------------------

test("resolve places creates by kind and slug", () => {
  const plan = decodePlanOK(`{"title":"T","pages":[
    {"op":"create","title":"Prepared Statements","kind":"concept","body":"b"},
    {"op":"create","title":"Acme Corp","kind":"entity","body":"b"}]}`);
  const resolved = resolveOK(plan, newVault({}));

  const want = [
    "wiki/concepts/prepared-statements.md",
    "wiki/entities/acme-corp.md",
  ];
  for (let i = 0; i < want.length; i++) {
    assert.equal(resolved.pages[i].pageRef, want[i]);
  }
});

test("resolve composes edge links from vault titles", () => {
  const root = newVault({
    "wiki/concepts/existing.md": "---\ntitle: The Existing Page\n---\nbody\n",
  });
  const plan = decodePlanOK(`{"title":"T","pages":[
    {"op":"create","title":"New","kind":"synthesis","body":"b",
     "edges":{"refines":["wiki/concepts/existing.md"]}}]}`);
  const resolved = resolveOK(plan, root);

  const want = '  - "[The Existing Page](../concepts/existing.md)"';
  assert.ok(
    resolved.pages[0].page!.text.includes(want),
    `composed edge missing from:\n${resolved.pages[0].page!.text}\nwant a line ${want}`,
  );
});

// A sibling page this same plan creates supplies the link title, so two new
// pages can link to each other before either exists on disk.
test("resolve composes edge links from sibling plan pages", () => {
  const plan = decodePlanOK(`{"title":"T","pages":[
    {"op":"create","title":"First Page","kind":"concept","body":"b"},
    {"op":"create","title":"Second","kind":"concept","body":"b",
     "edges":{"related":["wiki/concepts/first-page.md"]}}]}`);
  const resolved = resolveOK(plan, newVault({}));

  assert.ok(
    resolved.pages[1].page!.text.includes('"[First Page](first-page.md)"'),
    `sibling title not used:\n${resolved.pages[1].page!.text}`,
  );
});

test("resolve composes raw_source from sentinel", () => {
  const root = newVault({ "raw/a doc (v2).md": "raw\n" });
  const plan = decodePlanOK(`{"title":"T","raw":"raw/a doc (v2).md","pages":[
    {"op":"create","title":"Doc","kind":"source","body":"b","frontmatter":{"raw_source":true}}]}`);
  const resolved = resolveOK(plan, root);

  const want = 'raw_source: "[a doc (v2).md](../../raw/a%20doc%20%28v2%29.md)"';
  assert.ok(
    resolved.pages[0].page!.text.includes(want),
    `raw_source missing from:\n${resolved.pages[0].page!.text}\nwant a line ${want}`,
  );
});

test("resolve normalizes body links", () => {
  const plan = decodePlanOK(`{"title":"T","pages":[
    {"op":"create","title":"A","kind":"concept","body":"See [x](../../raw/spec(v2).md).\\n"}]}`);
  const resolved = resolveOK(plan, newVault({}));

  assert.ok(
    resolved.pages[0].page!.text.includes("spec%28v2%29.md"),
    `body link not re-encoded:\n${resolved.pages[0].page!.text}`,
  );
});

// An update starts from the on-disk page, so a re-ingest's existing edges and
// the fresh plan's edges are both present afterwards.
test("update merges list-valued keys onto disk state", () => {
  const root = newVault({
    "wiki/concepts/a.md":
      "---\ntitle: A\ntags:\n  - old\n" +
      'related:\n  - "[B](b.md)"\n---\nold body\n',
    "wiki/concepts/b.md": "---\ntitle: B\n---\nb\n",
    "wiki/concepts/c.md": "---\ntitle: C\n---\nc\n",
  });
  const plan = decodePlanOK(`{"title":"T","pages":[
    {"op":"update","title":"A","page_ref":"wiki/concepts/a.md",
     "frontmatter":{"tags":["new"]},"edges":{"related":["wiki/concepts/c.md"]}}]}`);
  const resolved = resolveOK(plan, root);

  const text = resolved.pages[0].page!.text;
  for (const want of ["- old", "- new", '"[B](b.md)"', '"[C](c.md)"']) {
    assert.ok(text.includes(want), `update lost ${want}:\n${text}`);
  }
  assert.ok(
    text.includes("old body"),
    `update omitting body should keep on-disk body:\n${text}`,
  );
});

test("update replaces body when given", () => {
  const root = newVault({
    "wiki/concepts/a.md": "---\ntitle: A\n---\nold body\n",
  });
  const plan = decodePlanOK(`{"title":"T","pages":[
    {"op":"update","title":"A","page_ref":"wiki/concepts/a.md","body":"new body\\n"}]}`);
  const resolved = resolveOK(plan, root);

  const text = resolved.pages[0].page!.text;
  assert.ok(
    !text.includes("old body") && text.includes("new body"),
    `body not replaced:\n${text}`,
  );
});

test("create accepts a vault-discovered kind", () => {
  const root = newVault({ "wiki/decisions/.keep": "" });
  const plan = decodePlanOK(`{"title":"T","pages":[
    {"op":"create","title":"Use Go","kind":"decision","body":"b"}]}`);
  const resolved = resolveOK(plan, root);

  assert.equal(resolved.pages[0].pageRef, "wiki/decisions/use-go.md");
  resolved.validate();
});

// --- shape validation -------------------------------------------------------

test("shape validation reports every problem at once", () => {
  const got = validationErrors(`{"pages":[{"op":"create"}]}`, "");
  for (const want of [
    "plan.title is required",
    "pages[0].title is required",
    "pages[0].kind is required for op=create",
    "pages[0].body is required for op=create",
  ]) {
    assert.ok(got.includes(want), `missing ${want} in: ${got}`);
  }
});

test("shape validation rejects an empty plan", () => {
  const got = validationErrors(`{"title":"T"}`, "");
  assert.ok(got.includes("at least one page"), got);
});

test("shape validation rejects a bad op", () => {
  const got = validationErrors(
    `{"title":"T","pages":[{"op":"delete","title":"A"}]}`,
    "",
  );
  assert.ok(
    got.includes(`pages[0].op must be 'create' or 'update', got "delete"`),
    got,
  );
});

test("shape validation rejects page_ref on create", () => {
  const got = validationErrors(
    `{"title":"T","pages":[
    {"op":"create","title":"A","kind":"concept","body":"b","page_ref":"wiki/concepts/a.md"}]}`,
    "",
  );
  assert.ok(got.includes("page_ref must not be set for op=create"), got);
});

test("shape validation rejects kind on update", () => {
  const got = validationErrors(
    `{"title":"T","pages":[
    {"op":"update","title":"A","page_ref":"wiki/concepts/a.md","kind":"concept"}]}`,
    "",
  );
  assert.ok(got.includes("kind must not be set for op=update"), got);
});

test("shape validation rejects an unknown kind", () => {
  const got = validationErrors(
    `{"title":"T","pages":[
    {"op":"create","title":"A","kind":"nonsense","body":"b"}]}`,
    "",
  );
  assert.ok(got.includes(`pages[0].kind "nonsense" is not a valid kind`), got);
});

test("shape validation rejects a non-true raw_source", () => {
  const got = validationErrors(
    `{"title":"T","raw":"raw/d.md","pages":[
    {"op":"create","title":"A","kind":"source","body":"b","frontmatter":{"raw_source":"x"}}]}`,
    "",
  );
  assert.ok(
    got.includes("raw_source must be true (derived from plan.raw)"),
    got,
  );
});

test("shape validation rejects raw_source without plan.raw", () => {
  const got = validationErrors(
    `{"title":"T","pages":[
    {"op":"create","title":"A","kind":"source","body":"b","frontmatter":{"raw_source":true}}]}`,
    "",
  );
  assert.ok(got.includes("raw_source is true but plan.raw is not set"), got);
});

test("shape validation rejects a non-date source_date", () => {
  const got = validationErrors(
    `{"title":"T","pages":[
    {"op":"create","title":"A","kind":"concept","body":"b",
     "frontmatter":{"source_date":"summer 2026"}}]}`,
    "",
  );
  assert.ok(
    got.includes(
      "pages[0].frontmatter.source_date must be a valid date (YYYY-MM-DD), got summer 2026",
    ),
    got,
  );
});

test("shape validation rejects an invalid calendar date source_date", () => {
  const got = validationErrors(
    `{"title":"T","pages":[
    {"op":"create","title":"A","kind":"concept","body":"b",
     "frontmatter":{"source_date":"2026-02-30"}}]}`,
    "",
  );
  assert.ok(
    got.includes(
      "pages[0].frontmatter.source_date must be a valid date (YYYY-MM-DD), got 2026-02-30",
    ),
    got,
  );
});

test("shape validation accepts a timestamp source_date", () => {
  const got = validationErrors(
    `{"title":"T","pages":[
    {"op":"create","title":"A","kind":"concept","body":"b",
     "frontmatter":{"source_date":"2026-07-20T14:30:00Z"}}]}`,
    "",
  );
  assert.equal(got, "");
});

test("shape validation treats null source_date as absent", () => {
  const got = validationErrors(
    `{"title":"T","pages":[
    {"op":"create","title":"A","kind":"concept","body":"b",
     "frontmatter":{"source_date":null}}]}`,
    "",
  );
  assert.equal(got, "");
});

test("shape validation treats null raw_source as absent", () => {
  const got = validationErrors(
    `{"title":"T","pages":[
    {"op":"create","title":"A","kind":"concept","body":"b",
     "frontmatter":{"raw_source":null}}]}`,
    "",
  );
  assert.equal(got, "");
});

// --- semantic validation ----------------------------------------------------

test("semantic validation rejects an existing create target", () => {
  const root = newVault({ "wiki/concepts/a.md": "---\ntitle: A\n---\nb\n" });
  const got = validationErrors(
    `{"title":"T","pages":[
    {"op":"create","title":"A","kind":"concept","body":"b"}]}`,
    root,
  );
  assert.ok(
    got.includes("create target wiki/concepts/a.md already exists"),
    got,
  );
});

test("semantic validation rejects a directory at a create target", () => {
  const root = newVault({});
  fs.mkdirSync(path.join(root, "wiki", "concepts", "a.md"), {
    recursive: true,
  });
  const got = validationErrors(
    `{"title":"T","pages":[
    {"op":"create","title":"A","kind":"concept","body":"b"}]}`,
    root,
  );
  assert.ok(
    got.includes("create target wiki/concepts/a.md already exists"),
    got,
  );
});

test("resolve refuses unmigrated kind folders", () => {
  const root = newVault({
    "wiki/concept/old.md": "---\ntitle: Old\n---\nold\n",
  });
  assert.throws(
    () =>
      resolve(
        decodePlanOK(`{"title":"T","pages":[
      {"op":"create","title":"A","kind":"concept","body":"b"}]}`),
        root,
      ),
    (err: unknown) =>
      (err as Error).message.includes("wiki/concept") &&
      (err as Error).message.includes("git mv wiki/concept/* wiki/concepts/"),
  );
});

test("resolve accepts a synthesis folder", () => {
  const root = newVault({ "wiki/synthesis/s.md": "---\ntitle: S\n---\ns\n" });
  resolve(
    decodePlanOK(`{"title":"T","pages":[
    {"op":"create","title":"A","kind":"concept","body":"b"}]}`),
    root,
  );
});

test("semantic validation rejects a missing update target", () => {
  const got = validationErrors(
    `{"title":"T","pages":[
    {"op":"update","title":"A","page_ref":"wiki/concepts/gone.md"}]}`,
    newVault({}),
  );
  assert.ok(got.includes("page_ref wiki/concepts/gone.md does not exist"), got);
});

test("semantic validation rejects an unresolvable edge target", () => {
  const got = validationErrors(
    `{"title":"T","pages":[
    {"op":"create","title":"A","kind":"concept","body":"b",
     "edges":{"related":["wiki/concepts/nowhere.md"]}}]}`,
    newVault({}),
  );
  assert.ok(
    got.includes('related target "wiki/concepts/nowhere.md" does not resolve'),
    got,
  );
});

test("semantic validation accepts a sibling create as an edge target", () => {
  const got = validationErrors(
    `{"title":"T","pages":[
    {"op":"create","title":"First Page","kind":"concept","body":"b"},
    {"op":"create","title":"Second","kind":"concept","body":"b",
     "edges":{"related":["wiki/concepts/first-page.md"]}}]}`,
    newVault({}),
  );
  assert.equal(got, "");
});

test("semantic validation enforces path length", () => {
  const root = newVault({});
  const longTitle = "x".repeat(60);
  const got = validationErrors(
    `{"title":"T","pages":[{"op":"create","title":"${longTitle}","kind":"concept","body":"b"}]}`,
    path.join(root, "d".repeat(150)),
  );
  assert.ok(got.includes("exceeds 255 chars"), got);
});

test("semantic validation runs chain of evidence", () => {
  const root = newVault({ "raw/doc.md": "raw\n" });
  const got = validationErrors(
    `{"title":"T","raw":"raw/doc.md","pages":[
    {"op":"create","title":"A","kind":"concept","body":"b"}]}`,
    root,
  );
  assert.ok(
    got.includes("needs a sources/ page whose raw_source points at it"),
    got,
  );
});

test("validate without a vault skips semantic checks", () => {
  const got = validationErrors(
    `{"title":"T","pages":[
    {"op":"update","title":"A","page_ref":"wiki/concepts/gone.md"}]}`,
    "",
  );
  assert.equal(got, "");
});

// --- execution --------------------------------------------------------------

const wholeIngestPlan = `{
  "title":"Deploy notes","action":"ingest","source_date":"2026-03-01","raw":"raw/doc.md",
  "pages":[
    {"op":"create","title":"Doc","kind":"source","body":"stub body\\n",
     "frontmatter":{"summary":"the doc","raw_source":true}},
    {"op":"create","title":"Prepared Statements","kind":"concept","body":"page body\\n",
     "frontmatter":{"summary":"s","volatility":"stable"},
     "edges":{"source":["wiki/sources/doc.md"],"supersedes":["wiki/concepts/old.md"]}}]}`;

test("execute writes pages and commits", async () => {
  const root = newVault({
    "raw/doc.md": "raw\n",
    "wiki/concepts/old.md": "---\ntitle: Old\n---\nold\n",
  });
  const resolved = resolveOK(decodePlanOK(wholeIngestPlan), root);
  resolved.validate();

  const git = new Fake();
  const sha = await resolved.execute(git);
  assert.equal(sha.length, 40);

  const v = new Vault(root);
  for (const ref of [
    "wiki/sources/doc.md",
    "wiki/concepts/prepared-statements.md",
  ]) {
    assert.ok(v.exists(ref), `${ref} was not written`);
  }

  const message = git.messages[0];
  for (const want of [
    "ingest: Deploy notes",
    "created: wiki/sources/doc.md",
    "created: wiki/concepts/prepared-statements.md",
    "superseded: wiki/concepts/old.md -> wiki/concepts/prepared-statements.md",
    "source-date: 2026-03-01",
  ]) {
    assert.ok(
      message.includes(want),
      `commit message missing ${want}:\n${message}`,
    );
  }
  assert.ok(
    git.added.join(",").includes("raw/doc.md"),
    `raw artifact not staged: ${git.added}`,
  );
});

test("execute is idempotent", async () => {
  const root = newVault({
    "raw/doc.md": "raw\n",
    "wiki/concepts/old.md": "---\ntitle: Old\n---\nold\n",
  });
  const plan = decodePlanOK(wholeIngestPlan);

  const first = resolveOK(plan, root);
  first.validate();
  await first.execute(new Fake());
  const before = readAll(root);

  // The second run's creates now collide, so re-executing the resolved plan
  // directly is the rerun-after-fix path: same bytes out.
  await first.execute(new Fake());
  const after = readAll(root);
  for (const [ref, text] of Object.entries(before)) {
    assert.equal(after[ref], text, `${ref} changed on re-execute`);
  }
});

test("execute without a root is an error", async () => {
  const resolved = resolveOK(decodePlanOK(`{"title":"T","pages":[]}`), "");
  await assert.rejects(
    resolved.execute(new Fake()),
    (err: unknown) => err instanceof ErrPlan,
  );
});

test("execute refuses an unresolved page", async () => {
  const resolved = resolveOK(
    decodePlanOK(
      `{"title":"T","pages":[{"op":"create","title":"A","kind":"nonsense","body":"b"}]}`,
    ),
    newVault({}),
  );
  await assert.rejects(
    resolved.execute(new Fake()),
    (err: unknown) => err instanceof ErrPlan,
  );
});

test("execute records updates, not creates", async () => {
  const root = newVault({ "wiki/concepts/a.md": "---\ntitle: A\n---\nbody\n" });
  const resolved = resolveOK(
    decodePlanOK(`{"title":"T","pages":[
    {"op":"update","title":"A","page_ref":"wiki/concepts/a.md","body":"new\\n"}]}`),
    root,
  );
  resolved.validate();
  const git = new Fake();
  await resolved.execute(git);
  assert.ok(git.messages[0].includes("updated: wiki/concepts/a.md"));
  assert.ok(!git.messages[0].includes("created:"));
});

test("execute a synthesis save", async () => {
  const root = newVault({ "wiki/concepts/a.md": "---\ntitle: A\n---\nbody\n" });
  const resolved = resolveOK(
    decodePlanOK(`{"title":"Q","action":"synthesize","pages":[
    {"op":"create","title":"Answer","kind":"synthesis","body":"b",
     "edges":{"source":["wiki/concepts/a.md"]}}]}`),
    root,
  );
  resolved.validate();
  const git = new Fake();
  await resolved.execute(git);
  assert.ok(git.messages[0].startsWith("synthesize: Q"));
});

test("execute truncates the manifest source date", async () => {
  const root = newVault({});
  const resolved = resolveOK(
    decodePlanOK(`{"title":"T","source_date":"2026-07-20T14:30:00Z","pages":[
    {"op":"create","title":"A","kind":"concept","body":"b"}]}`),
    root,
  );
  resolved.validate();
  const git = new Fake();
  await resolved.execute(git);
  assert.ok(git.messages[0].includes("source-date: 2026-07-20"));
  assert.ok(!git.messages[0].includes("14:30"));
});

test("describe", () => {
  const resolved = resolveOK(
    decodePlanOK(`{"title":"T","pages":[
    {"op":"create","title":"A","kind":"concept","body":"b"}]}`),
    newVault({}),
  );
  assert.equal(resolved.describe(), "ingest: T\n  create wiki/concepts/a.md");
});

function readAll(root: string): Record<string, string> {
  return new Vault(root).loadWikiPages();
}

// --- Integration: a real temp-dir vault + git --------------------------------

const FIXED_SIGNATURE = {
  name: "test",
  email: "test@example.com",
  timestamp: Math.floor(new Date("2026-01-01T00:00:00Z").getTime() / 1000),
  timezoneOffset: 0,
};

/** Init a real empty git repo at root so HEAD exists. */
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

/** The acceptance-criteria multi-page plan: create/update ops, a typed edge, a
 * raw_source link, and a separate synthesize action plan. */
const multiPagePlan = `{
  "title":"Deploy notes","action":"ingest","source_date":"2026-03-01","raw":"raw/doc.md",
  "pages":[
    {"op":"create","title":"Doc","kind":"source","body":"stub body\\n",
     "frontmatter":{"summary":"the doc","raw_source":true}},
    {"op":"create","title":"Prepared Statements","kind":"concept","body":"page body\\n",
     "frontmatter":{"summary":"s","volatility":"stable"},
     "edges":{"source":["wiki/sources/doc.md"]}},
    {"op":"update","title":"Old","page_ref":"wiki/concepts/old.md","body":"updated body\\n",
     "edges":{"source":["wiki/sources/doc.md"],"related":["wiki/concepts/prepared-statements.md"]}}]}`;

test("integration: a multi-page plan commits pages to a real git vault", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ingest-it-"));
  await initRepo(root);
  const rawDir = path.join(root, "raw");
  fs.mkdirSync(rawDir, { recursive: true });
  fs.writeFileSync(path.join(rawDir, "doc.md"), "raw\n");
  const oldDir = path.join(root, "wiki", "concepts");
  fs.mkdirSync(oldDir, { recursive: true });
  fs.writeFileSync(
    path.join(oldDir, "old.md"),
    "---\ntitle: Old\n---\nold body\n",
  );

  const resolved = resolve(decodePlanOK(multiPagePlan), root);
  resolved.validate();
  const repo = new VaultGit(root);
  const sha = await resolved.execute(repo);

  const log = await git.log({ fs, dir: root, depth: 1 });
  assert.equal(log[0].oid, sha);

  const v = new Vault(root);
  assert.ok(v.exists("wiki/sources/doc.md"));
  assert.ok(v.exists("wiki/concepts/prepared-statements.md"));

  // The source stub points at the raw artifact via a composed raw_source link.
  const stub = v.load("wiki/sources/doc.md").text;
  assert.ok(stub.includes('raw_source: "[doc.md](../../raw/doc.md)"'), stub);

  // The concept page's source edge resolves to the stub.
  const concept = v.load("wiki/concepts/prepared-statements.md").text;
  assert.ok(concept.includes('"[Doc](../sources/doc.md)"'), concept);

  // The update kept the on-disk title but replaced the body and gained an edge.
  const updated = v.load("wiki/concepts/old.md").text;
  assert.ok(updated.includes("title: Old"), updated);
  assert.ok(updated.includes("updated body"), updated);
  assert.ok(
    updated.includes('"[Prepared Statements](prepared-statements.md)"'),
    updated,
  );

  const message = log[0].commit.message;
  assert.ok(message.includes("ingest: Deploy notes"), message);
  assert.ok(message.includes("created: wiki/sources/doc.md"), message);
  assert.ok(
    message.includes("created: wiki/concepts/prepared-statements.md"),
    message,
  );
  assert.ok(message.includes("updated: wiki/concepts/old.md"), message);
  assert.ok(message.includes("source-date: 2026-03-01"), message);
});

test("integration: a synthesize action commits under its own verb", async () => {
  const root = fs.mkdtempSync(
    path.join(os.tmpdir(), "enchiridion-ingest-syn-"),
  );
  await initRepo(root);
  fs.mkdirSync(path.join(root, "wiki", "concepts"), { recursive: true });
  fs.writeFileSync(
    path.join(root, "wiki", "concepts", "a.md"),
    "---\ntitle: A\n---\nbody\n",
  );

  const resolved = resolve(
    decodePlanOK(`{"title":"Q","action":"synthesize","pages":[
      {"op":"create","title":"Answer","kind":"synthesis","body":"b",
       "edges":{"source":["wiki/concepts/a.md"]}}]}`),
    root,
  );
  resolved.validate();
  const sha = await resolved.execute(new VaultGit(root));

  const log = await git.log({ fs, dir: root, depth: 1 });
  assert.equal(log[0].oid, sha);
  assert.ok(
    log[0].commit.message.startsWith("synthesize: Q"),
    log[0].commit.message,
  );
  const v = new Vault(root);
  assert.ok(v.exists("wiki/synthesis/answer.md"));
});
