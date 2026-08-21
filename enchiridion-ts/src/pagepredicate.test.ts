/**
 * Tests for the pagepredicate module (#310) — the one definition of what
 * counts as a page. The predicate and the disk enumerator are the shared rule
 * the disk walk, the git walk, and the index's status count all delegate to.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { enumeratePageRefs, isPageRef } from "./pagepredicate.js";

// ---------------------------------------------------------------------------
// isPageRef
// ---------------------------------------------------------------------------

test("isPageRef accepts pages directly under a kind-folder", () => {
  for (const ref of [
    "wiki/concepts/a.md",
    "wiki/entities/b.md",
    "wiki/sources/s.md",
    "wiki/synthesis/t.md",
    "wiki/decisions/d.md", // custom kind-folder (ADR-0008: folder → kind)
    "wiki/decisions/use-fts5.md",
  ]) {
    assert.equal(isPageRef(ref), true, `for ref ${ref}`);
  }
});

test("isPageRef rejects the generated wiki/_index.md — never a page", () => {
  assert.equal(isPageRef("wiki/_index.md"), false);
});

test("isPageRef rejects a nested page — a structural error, not a page", () => {
  assert.equal(isPageRef("wiki/concepts/nested/deep.md"), false);
});

test("isPageRef rejects a markdown file at the wiki root (no kind-folder)", () => {
  assert.equal(isPageRef("wiki/a.md"), false);
  assert.equal(isPageRef("wiki/notes.md"), false);
});

test("isPageRef rejects non-pages", () => {
  for (const ref of [
    "raw/notes.md", // outside wiki/
    "wiki/concepts/a", // not markdown
    "wiki/concepts.md", // a file at the wiki root
    "wiki/concepts/a.txt",
    "wiki/concepts/",
    "wiki",
    "",
  ]) {
    assert.equal(isPageRef(ref), false, `for ref ${ref}`);
  }
});

// ---------------------------------------------------------------------------
// enumeratePageRefs
// ---------------------------------------------------------------------------

test("enumeratePageRefs counts only pages, using the same rule as isPageRef", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-pp-"));
  const write = (rel: string, text = "") => {
    const abs = path.join(root, ...rel.split("/"));
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    fs.writeFileSync(abs, text);
  };
  write("wiki/concepts/a.md");
  write("wiki/entities/b.md");
  write("wiki/decisions/d.md");
  write("wiki/_index.md", "generated table");
  write("wiki/loose.md");
  write("wiki/concepts/nested/deep.md");
  write("wiki/concepts/nested/other.md");
  write("wiki/concepts/a.txt");
  write("raw/notes.md");

  assert.deepEqual(enumeratePageRefs(root), [
    "wiki/concepts/a.md",
    "wiki/decisions/d.md",
    "wiki/entities/b.md",
  ]);
});

test("enumeratePageRefs on a vault with no wiki dir is empty, not an error", () => {
  assert.deepEqual(
    enumeratePageRefs(
      fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-pp-")),
    ),
    [],
  );
});
