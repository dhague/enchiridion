/**
 * supersededby tests — resolving supersession chains to their current heads.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { loadRecords, type PageRecord } from "./pagerecord.js";
import { resolve, type Resolution } from "./supersededby.js";

function page(title: string, supersedes: string): string {
  let text = `---\ntitle: ${title}\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n`;
  if (supersedes !== "") {
    text += `supersedes:\n  - "[Old](${supersedes})"\n`;
  }
  text += "---\n\n";
  return text;
}

function records(pages: Record<string, string>): Record<string, PageRecord> {
  return loadRecords(pages);
}

test("current page resolves to itself", () => {
  const recs = records({ "wiki/concepts/a.md": page("A", "") });
  const res = resolve(["wiki/concepts/a.md"], recs);
  assert.deepEqual(res, [
    { seed: "wiki/concepts/a.md", active: "wiki/concepts/a.md", chain: [] },
  ]);
});

test("superseded seed resolves to replacement", () => {
  const recs = records({
    "wiki/concepts/old.md": page("Old", ""),
    "wiki/concepts/new.md": page("New", "old.md"),
  });
  const res = resolve(["wiki/concepts/old.md"], recs);
  assert.equal(res[0].active, "wiki/concepts/new.md");
  assert.deepEqual(res[0].chain, ["wiki/concepts/new.md"]);
});

test("head is returned even when outside the candidate set", () => {
  const recs = records({
    "wiki/concepts/old.md": page("Old", ""),
    "wiki/concepts/new.md": page("New", "old.md"),
  });
  const res = resolve(["wiki/concepts/old.md"], recs);
  assert.equal(res[0].active, "wiki/concepts/new.md");
});

test("multi-hop chain walks to the final head", () => {
  const recs = records({
    "wiki/concepts/a.md": page("A", ""),
    "wiki/concepts/b.md": page("B", "a.md"),
    "wiki/concepts/c.md": page("C", "b.md"),
  });
  const res = resolve(["wiki/concepts/a.md"], recs);
  assert.equal(res[0].active, "wiki/concepts/c.md");
  assert.deepEqual(res[0].chain, ["wiki/concepts/b.md", "wiki/concepts/c.md"]);
});

test("multiple seeds resolve independently", () => {
  const recs = records({
    "wiki/concepts/old.md": page("Old", ""),
    "wiki/concepts/new.md": page("New", "old.md"),
    "wiki/concepts/current.md": page("Current", ""),
  });
  const res = resolve(
    ["wiki/concepts/old.md", "wiki/concepts/current.md"],
    recs,
  );
  const bySeed: Record<string, Resolution> = {};
  for (const r of res) bySeed[r.seed] = r;
  assert.equal(bySeed["wiki/concepts/old.md"].active, "wiki/concepts/new.md");
  assert.equal(
    bySeed["wiki/concepts/current.md"].active,
    "wiki/concepts/current.md",
  );
});

test("seed missing from vault resolves to itself", () => {
  const recs = records({ "wiki/concepts/a.md": page("A", "") });
  const res = resolve(["wiki/concepts/gone.md"], recs);
  assert.deepEqual(res, [
    {
      seed: "wiki/concepts/gone.md",
      active: "wiki/concepts/gone.md",
      chain: [],
    },
  ]);
});

test("supersedes cycle does not infinite loop", () => {
  const recs = records({
    "wiki/concepts/a.md": page("A", "b.md"),
    "wiki/concepts/b.md": page("B", "a.md"),
  });
  const res = resolve(["wiki/concepts/a.md"], recs);
  assert.ok(
    res[0].active === "wiki/concepts/a.md" ||
      res[0].active === "wiki/concepts/b.md",
    `active = ${res[0].active}, want one of the cycle members`,
  );
});
