import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Markers, Vault, hasMarker, pageRefs, resolveRoot } from "./vault.js";
import type { LookupEnv } from "./vault.js";
import { Page } from "./wikipage.js";

function env(pairs: Record<string, string>): LookupEnv {
  return (key: string): [string | undefined, boolean] => {
    if (key in pairs) return [pairs[key], true];
    return [undefined, false];
  };
}

function resolve(p: string): string {
  const abs = path.resolve(p);
  try {
    return fs.realpathSync(abs);
  } catch {
    return abs;
  }
}

function writeVault(pages: Record<string, string>): Vault {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-vault-"));
  for (const [ref, text] of Object.entries(pages)) {
    const abs = path.join(root, ...ref.split("/"));
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    fs.writeFileSync(abs, text);
  }
  return new Vault(root);
}

test("resolveRoot prefers WIKI_ROOT env", () => {
  // $WIKI_ROOT wins even when `start` is itself a marked vault — the
  // query-from-anywhere mode depends on it (ADR-0004).
  const elsewhere = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ss-"));
  const start = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ss-"));
  fs.mkdirSync(path.join(start, "wiki"));

  const { root } = resolveRoot(start, env({ WIKI_ROOT: elsewhere }));
  assert.equal(root, resolve(elsewhere));
});

test("resolveRoot ignores an empty WIKI_ROOT", () => {
  const start = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ss-"));
  fs.mkdirSync(path.join(start, "wiki"));

  const { root } = resolveRoot(start, env({ WIKI_ROOT: "" }));
  assert.equal(root, resolve(start));
});

test("resolveRoot walks up to the nearest marker", () => {
  for (const marker of Markers) {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ss-"));
    const markerPath = path.join(root, marker);
    if (marker === "wiki") fs.mkdirSync(markerPath);
    else fs.writeFileSync(markerPath, "");
    const deep = path.join(root, "a", "b", "c");
    fs.mkdirSync(deep, { recursive: true });

    const { root: got } = resolveRoot(deep, env({}));
    assert.equal(got, resolve(root));
  }
});

test("resolveRoot falls back to start", () => {
  const start = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ss-"));
  const { root } = resolveRoot(start, env({}));
  assert.equal(root, resolve(start));
});

test("hasMarker recognises both markers", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ss-"));
  fs.mkdirSync(path.join(root, "wiki"));
  assert.equal(hasMarker(root), true);
  const root2 = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ss-"));
  fs.writeFileSync(path.join(root2, ".wiki-root"), "");
  assert.equal(hasMarker(root2), true);
  const root3 = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ss-"));
  assert.equal(hasMarker(root3), false);
});

test("pageRefs walks only wiki markdown", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ss-"));
  fs.mkdirSync(path.join(root, "wiki", "concepts"), { recursive: true });
  fs.mkdirSync(path.join(root, "raw"), { recursive: true });
  fs.writeFileSync(path.join(root, "wiki", "concepts", "a.md"), "");
  fs.writeFileSync(path.join(root, "wiki", "concepts", ".gitkeep"), "");
  fs.writeFileSync(path.join(root, "wiki", "concepts", "notes.txt"), "");
  fs.writeFileSync(path.join(root, "raw", "should-not-appear.md"), "");

  assert.deepEqual(pageRefs(root), ["wiki/concepts/a.md"]);
});

test("pageRefs on a vault with no wiki dir is empty, not an error", () => {
  assert.deepEqual(
    pageRefs(fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ss-"))),
    [],
  );
});

test("load and write round-trip", () => {
  const v = writeVault({});
  const text = "---\ntitle: A\n---\nbody\n";
  v.write("wiki/concepts/a.md", new Page(text));
  assert.equal(v.load("wiki/concepts/a.md").text, text);
  assert.equal(v.exists("wiki/concepts/a.md"), true);
  assert.equal(v.exists("wiki/concepts"), false);
});

test("exists versus occupied on a directory", () => {
  const v = writeVault({ "wiki/concepts/a.md/nested.md": "x\n" });
  assert.equal(v.exists("wiki/concepts/a.md"), false);
  assert.equal(v.occupied("wiki/concepts/a.md"), true);
  assert.equal(v.occupied("wiki/concepts/absent.md"), false);
});

test("legacyKindFolders finds pre-ADR-0008 singular folders", () => {
  const v = writeVault({
    "wiki/concept/old.md": "x\n",
    "wiki/source/old.md": "x\n",
    "wiki/concepts/new.md": "x\n",
    "wiki/synthesis/s.md": "x\n",
    "wiki/decisions/d.md": "x\n",
  });
  assert.deepEqual(v.legacyKindFolders(), ["concept", "source"]);
});

test("legacyKindFolders on a migrated vault", () => {
  const v = writeVault({ "wiki/concepts/a.md": "x\n" });
  assert.deepEqual(v.legacyKindFolders(), []);
});

test("loadWikiPages never walks raw", () => {
  const v = writeVault({
    "wiki/concepts/a.md": "a\n",
    "wiki/sources/s.md": "s\n",
    "raw/doc.md": "raw\n",
  });
  const pages = v.loadWikiPages();
  assert.equal(Object.keys(pages).length, 2);
  assert.ok(!("raw/doc.md" in pages));
});

test("discoveredKinds skips canonical folders", () => {
  const v = writeVault({
    "wiki/concepts/a.md": "a\n",
    "wiki/decisions/d.md": "d\n",
    "wiki/people/p.md": "p\n",
  });
  assert.deepEqual(v.discoveredKinds(), {
    decision: "decisions",
    people: "people",
  });
});

test("discoveredKinds on a vault without a wiki dir", () => {
  const v = new Vault(
    fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ss-")),
  );
  assert.deepEqual(v.discoveredKinds(), {});
});

test("set and merge write back", () => {
  const v = writeVault({
    "wiki/concepts/a.md": "---\ntitle: A\ntags:\n  - x\n---\nbody\n",
  });
  v.set("wiki/concepts/a.md", "volatility", "stable");
  v.merge("wiki/concepts/a.md", "tags", ["x", "y"]);
  const page = v.load("wiki/concepts/a.md");
  assert.equal(page.getString("volatility"), "stable");
  assert.deepEqual(page.getStringList("tags"), ["x", "y"]);
});

test("movePage fixes links and removes original", () => {
  const v = writeVault({
    "wiki/concepts/a.md":
      '---\nrelated:\n  - "[B](b.md)"\n---\nSee [B](b.md).\n',
    "wiki/concepts/b.md": "Back to [A](a.md).\n",
    "wiki/sources/s.md": "Nothing to fix here.\n",
  });

  const changed = v.movePage("wiki/concepts/b.md", "wiki/entities/b.md");
  assert.deepEqual(changed, ["wiki/concepts/a.md", "wiki/entities/b.md"]);
  assert.equal(v.exists("wiki/concepts/b.md"), false);
  const moved = v.load("wiki/entities/b.md");
  assert.match(moved.text, /\(\.\.\/concepts\/a\.md\)/);
  const inbound = v.load("wiki/concepts/a.md");
  assert.equal(
    (inbound.text.match(/\(\.\.\/entities\/b\.md\)/g) ?? []).length,
    2,
  );
});

test("movePage missing source errors", () => {
  const v = writeVault({ "wiki/concepts/a.md": "a\n" });
  assert.throws(() =>
    v.movePage("wiki/concepts/missing.md", "wiki/entities/missing.md"),
  );
});

test("movePage onto itself changes nothing", () => {
  const v = writeVault({ "wiki/concepts/a.md": "See [self](a.md).\n" });
  const changed = v.movePage("wiki/concepts/a.md", "wiki/concepts/a.md");
  assert.deepEqual(changed, []);
  assert.equal(v.exists("wiki/concepts/a.md"), true);
});

test("rewriteInboundLinks for a non-page target", () => {
  const v = writeVault({
    "wiki/sources/s.md":
      '---\nraw_source: "[old.md](../../raw/old.md)"\n---\nstub\n',
    "wiki/concepts/a.md": "Unrelated.\n",
  });
  const changed = v.rewriteInboundLinks("raw/old.md", "raw/new.md");
  assert.deepEqual(changed, ["wiki/sources/s.md"]);
  const stub = v.load("wiki/sources/s.md");
  assert.match(stub.text, /\(\.\.\/\.\.\/raw\/new\.md\)/);
  assert.equal(v.exists("raw/new.md"), false);
  assert.equal(v.exists("raw/old.md"), false);
});

test("pages decodes records", () => {
  const v = writeVault({
    "wiki/concepts/a.md": "---\ntitle: A\ntags:\n  - x\n---\nbody\n",
    "wiki/sources/s.md":
      '---\ntitle: S\nsupersedes:\n  - "[A](../concepts/a.md)"\n---\nstub\n',
  });
  const pages = v.pages();
  const a = pages["wiki/concepts/a.md"];
  assert.equal(a.title, "A");
  assert.equal(a.kind, "concept");
  assert.deepEqual(a.supersededBy, ["wiki/sources/s.md"]);
});
