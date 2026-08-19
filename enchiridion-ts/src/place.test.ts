import { test } from "node:test";
import assert from "node:assert/strict";
import {
  FolderKinds,
  Kinds,
  KindFolders,
  MaxSlugLength,
  folderToKind,
  path,
  slugify,
} from "./place.js";

test("slugify lowercases and kebab-cases a title", () => {
  const cases: Array<[string, string]> = [
    ["Connection Pooling", "connection-pooling"],
    ["What's in a name?", "whats-in-a-name"],
    ["What’s in a name?", "whats-in-a-name"], // curly apostrophe too
    ["  Leading & trailing  ", "leading-trailing"],
    ["CamelCase/slash_underscore", "camelcase-slash-underscore"],
    ["Ünïcödé título", "n-c-d-t-tulo"],
    ["---", ""],
  ];
  for (const [title, want] of cases) {
    assert.equal(slugify(title, 0), want);
  }
});

test("slugify truncates at a hyphen boundary", () => {
  const title =
    "the quick brown fox jumps over the lazy dog and keeps on running forever";
  const got = slugify(title, MaxSlugLength);
  assert.ok(got.length <= MaxSlugLength);
  assert.equal(got, "the-quick-brown-fox-jumps-over-the-lazy-dog-and-keeps-on");
});

test("slugify hard-cuts when there's no usable hyphen boundary", () => {
  const got = slugify("a".repeat(100), 20);
  assert.equal(got, "a".repeat(20));
});

test("folderToKind singularizes per ADR-0008", () => {
  const cases: Array<[string, string]> = [
    ["decisions", "decision"],
    ["people", "people"], // no trailing s: used verbatim
    ["synthesis", "synthesi"],
  ];
  for (const [folder, want] of cases) {
    assert.equal(folderToKind(folder), want);
  }
});

test("folderKinds inverts kindFolders", () => {
  for (const [kind, folder] of Object.entries(KindFolders)) {
    assert.equal(FolderKinds[folder], kind);
  }
  // `synthesis` is the ADR-0008 exception: no distinct plural, so the
  // canonical lookup must win over the singularization rule.
  assert.equal(FolderKinds["synthesis"], "synthesis");
});

test("path resolves a canonical kind to its folder", () => {
  assert.equal(
    path("concept", "Connection Pooling"),
    "wiki/concepts/connection-pooling.md",
  );
});

test("path accepts discovered (custom) kinds", () => {
  assert.equal(
    path("decision", "Use FTS5", { decision: "decisions" }),
    "wiki/decisions/use-fts5.md",
  );
});

test("path rejects an unknown kind", () => {
  assert.throws(() => path("nonsense", "X"), /unknown kind "nonsense"/);
});

test("kinds are in the canonical order", () => {
  assert.deepEqual(Kinds, ["concept", "entity", "source", "synthesis"]);
});
