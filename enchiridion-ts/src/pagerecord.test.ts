import { test } from "node:test";
import assert from "node:assert/strict";
import { loadRecords, newPageRecord, supersedes } from "./pagerecord.js";

const conceptPage = `---
title: Connection pooling
summary: Reusing database connections across requests.
tags:
  - database
  - performance
source_date: 2026-07-20
volatility: evolving
raw_source: "[notes.md](../../raw/notes%20%281%29.md)"
supersedes:
  - "[Old pooling](old-pooling.md)"
refines:
  - "[Databases](../entities/databases.md)"
---

Body text.
`;

test("newPageRecord decodes the frontmatter schema", () => {
  const rec = newPageRecord("wiki/concepts/pooling.md", conceptPage);
  assert.equal(rec.kind, "concept");
  assert.equal(rec.title, "Connection pooling");
  assert.equal(rec.sourceDate, "2026-07-20");
  assert.equal(rec.volatility, "evolving");
  assert.deepEqual(rec.tags, ["database", "performance"]);
});

test("newPageRecord resolves edge targets to vault-relative", () => {
  const rec = newPageRecord("wiki/concepts/pooling.md", conceptPage);
  assert.deepEqual(rec.edges, [
    { key: "raw_source", targets: ["raw/notes (1).md"] },
    { key: "supersedes", targets: ["wiki/concepts/old-pooling.md"] },
    { key: "refines", targets: ["wiki/entities/databases.md"] },
  ]);
});

test("newPageRecord quotes dates back as ISO strings", () => {
  const rec = newPageRecord(
    "wiki/concepts/a.md",
    "---\nsource_date: 2026-01-02\n---\n",
  );
  assert.equal(rec.sourceDate, "2026-01-02");
});

test("newPageRecord truncates a timestamp to its date", () => {
  for (const scalar of [
    "2026-07-20T14:30:00Z",
    "2026-07-20T14:30:00+05:00",
    '"2026-07-20T14:30:00Z"',
    '"2026-07-20 10:30:00"',
  ]) {
    const rec = newPageRecord(
      "wiki/concepts/timed.md",
      `---\nsource_date: ${scalar}\n---\n`,
    );
    assert.equal(rec.sourceDate, "2026-07-20", `for source_date: ${scalar}`);
  }
});

test("newPageRecord keeps a non-date verbatim", () => {
  const rec = newPageRecord(
    "wiki/concepts/a.md",
    '---\nsource_date: "summer 2026"\n---\n',
  );
  assert.equal(rec.sourceDate, "summer 2026");
});

test("newPageRecord derives custom kinds per ADR-0008", () => {
  const rec = newPageRecord(
    "wiki/decisions/use-fts5.md",
    "---\ntitle: Use FTS5\n---\n",
  );
  assert.equal(rec.kind, "decision");
});

test("newPageRecord derives kinds from all canonical folders", () => {
  const byFolder: Record<string, string> = {
    concepts: "concept",
    entities: "entity",
    sources: "source",
    synthesis: "synthesis",
  };
  for (const [folder, kind] of Object.entries(byFolder)) {
    const rec = newPageRecord(`wiki/${folder}/a.md`, "---\ntitle: X\n---\n");
    assert.equal(rec.kind, kind, `for folder ${folder}`);
  }
});

test("newPageRecord rejects pages at the wrong depth", () => {
  for (const ref of [
    "wiki/loose.md",
    "wiki/concepts/nested/deep.md",
    "raw/notes.md",
  ]) {
    assert.throws(
      () => newPageRecord(ref, "---\ntitle: X\n---\n"),
      /not directly under a wiki kind-folder/,
      `for ref ${ref}`,
    );
  }
});

test("newPageRecord accepts a page with no frontmatter", () => {
  const rec = newPageRecord("wiki/concepts/bare.md", "Just a body.\n");
  assert.equal(rec.title, "");
  assert.deepEqual(rec.edges, []);
});

test("newPageRecord rejects an edge that is not a markdown link", () => {
  assert.throws(
    () =>
      newPageRecord(
        "wiki/concepts/a.md",
        "---\nrelated:\n  - wiki/concepts/b.md\n---\n",
      ),
    /not a markdown link/,
  );
});

test("loadRecords inverts supersedes", () => {
  const pages: Record<string, string> = {
    "wiki/concepts/new.md":
      '---\ntitle: New\nsupersedes:\n  - "[Old](old.md)"\n---\n',
    "wiki/concepts/old.md": "---\ntitle: Old\n---\n",
  };
  const records = loadRecords(pages);
  assert.deepEqual(records["wiki/concepts/old.md"].supersededBy, [
    "wiki/concepts/new.md",
  ]);
  assert.deepEqual(records["wiki/concepts/new.md"].supersededBy, []);
});

test("supersedes resolves a percent-encoded target to its decoded page ref", () => {
  const rec = newPageRecord(
    "wiki/concepts/new.md",
    '---\nsupersedes:\n  - "[Old](old%20name%20%28draft%29%20%231.md)"\n---\n',
  );
  assert.deepEqual(supersedes(rec), ["wiki/concepts/old name (draft) #1.md"]);
});
