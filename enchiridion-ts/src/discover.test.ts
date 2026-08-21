/**
 * Tests for the discover module.
 *
 * The integration tests open a real [Index] over a committed fixture vault
 * (search is a view of committed history — ADR-0015), exactly as the command
 * owns the one index handle (ADR-0010). The OR-vs-AND behaviour is exercised
 * against a fake [Searcher] so the query the index actually receives can be
 * asserted directly.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  orQuery,
  classify,
  check,
  discover,
  tagsContaining,
  tagCounts,
  HintDuplicate,
  HintRefines,
  HintRelated,
  HintDistinct,
  type Searcher,
  type Candidate,
} from "./discover.js";
import { Index } from "./searchindex.js";
import type { Hit, Query } from "./searchindex.js";
import { OrderedMap, type PagePlan } from "./ingest.js";
import { VaultGit } from "./vaultgit.js";

// ---------------------------------------------------------------------------
// Fixture vault (committed — search is a view of committed history)
// ---------------------------------------------------------------------------

function writeFixturePage(root: string, rel: string, text: string): void {
  const abs = path.join(root, "wiki", rel);
  fs.mkdirSync(path.dirname(abs), { recursive: true });
  fs.writeFileSync(abs, text);
}

async function newFixtureVault(): Promise<string> {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-disc-"));
  writeFixturePage(
    root,
    "concepts/connection-pooling.md",
    "---\ntitle: Connection Pooling in Postgres\n" +
      "summary: Reuse connections instead of opening a new one per request.\n" +
      "tags:\n  - database\nvolatility: stable\n---\n\n" +
      "Connection pooling reduces per-request handshake overhead by " +
      "reusing a fixed set of open connections across callers.\n",
  );
  writeFixturePage(
    root,
    "concepts/sourdough-starter.md",
    "---\ntitle: Feeding a Sourdough Starter\n" +
      "summary: Daily flour-and-water feeding keeps a starter active.\n" +
      "tags:\n  - baking\nvolatility: stable\n---\n\n" +
      "A sourdough starter needs equal parts flour and water once a day, " +
      "kept warm, to stay active enough to leaven bread.\n",
  );

  const repo = new VaultGit(root);
  await repo.init();
  await repo.add(["."]);
  await repo.commit("fixtures");
  return root;
}

/** Open the one index handle a test gets, mirroring the command's ownership
 * of it (ADR-0010). */
async function newFixtureSearcher(): Promise<Index> {
  const index = await Index.open(await newFixtureVault());
  return index;
}

function refOf(candidates: Candidate[], ref: string): Candidate | undefined {
  return candidates.find((c) => c.page_ref === ref);
}

// ---------------------------------------------------------------------------
// classify: pure boundary logic
// ---------------------------------------------------------------------------

test("classify maps scores and title sharing to hints", () => {
  const cases: [string, number, boolean, string][] = [
    [
      "high score with shared title token is duplicate",
      20.0,
      true,
      HintDuplicate,
    ],
    [
      "high score without shared title token is refines",
      20.0,
      false,
      HintRefines,
    ],
    ["mid score is related regardless of title", 10.0, true, HintRelated],
    ["mid score related without title", 10.0, false, HintRelated],
    ["low score is distinct", 1.0, true, HintDistinct],
    ["duplicate threshold is inclusive", 15.0, true, HintDuplicate],
    ["related threshold is inclusive", 5.0, false, HintRelated],
  ];
  for (const [name, score, shares, want] of cases) {
    assert.equal(classify(score, shares, 15.0, 5.0), want, name);
  }
});

// ---------------------------------------------------------------------------
// orQuery construction
// ---------------------------------------------------------------------------

test("orQuery builds an OR expression of unique lowercased words", () => {
  const cases: [string[], string][] = [
    [["Connection Pooling"], `"connection" OR "pooling"`],
    [
      ["Connection Pooling", "Connection reuse"],
      `"connection" OR "pooling" OR "reuse"`,
    ],
    [["", ""], ""],
    [["  !!!  "], ""],
  ];
  for (const [texts, want] of cases) {
    assert.equal(orQuery(...texts), want);
  }
});

// ---------------------------------------------------------------------------
// Searcher fake
// ---------------------------------------------------------------------------

/** A fake Searcher that records the last query it saw and returns scripted
 * hits. Lets a test assert the exact query (raw, OR-joined) the index
 * receives. */
class FakeSearcher implements Searcher {
  lastQuery: Query | null = null;
  hits: Hit[] = [];

  async search(q: Query): Promise<Hit[]> {
    this.lastQuery = q;
    return this.hits;
  }
}

function hit(
  pageRef: string,
  title: string,
  score: number,
  partial: Partial<Hit> = {},
): Hit {
  return {
    pageRef,
    score,
    title,
    summary: partial.summary ?? "",
    tags: partial.tags ?? [],
    kind: partial.kind ?? "concept",
    sourceDate: partial.sourceDate ?? "",
    gitDate: partial.gitDate ?? null,
    volatility: partial.volatility ?? "",
    supersededBy: partial.supersededBy ?? null,
    snippet: partial.snippet ?? null,
  };
}

// ---------------------------------------------------------------------------
// check: query construction, classification, empty-input guard
// ---------------------------------------------------------------------------

test("check sends an OR query with raw:true and a limit", async () => {
  const fake = new FakeSearcher();
  await check(fake, "Connection Pooling", "Reuse", "connections", {
    limit: 0,
    duplicateThreshold: 0,
    relatedThreshold: 0,
  });
  assert.ok(fake.lastQuery);
  assert.equal(fake.lastQuery!.raw, true);
  assert.equal(fake.lastQuery!.limit, 200); // DefaultLimit
  assert.equal(
    fake.lastQuery!.text,
    `"connection" OR "pooling" OR "reuse" OR "connections"`,
  );
});

test("check returns no candidates for an empty query", async () => {
  const fake = new FakeSearcher();
  const candidates = await check(fake, "", "", "", {
    limit: 0,
    duplicateThreshold: 0,
    relatedThreshold: 0,
  });
  assert.deepEqual(candidates, []);
  assert.equal(fake.lastQuery, null);
});

test("check classifies each hit and carries the full payload", async () => {
  const fake = new FakeSearcher();
  fake.hits = [
    hit(
      "wiki/concepts/connection-pooling.md",
      "Connection Pooling in Postgres",
      20,
      {
        summary: "Reuse connections.",
        tags: ["database"],
        volatility: "stable",
      },
    ),
  ];
  const candidates = await check(fake, "Connection Pooling", "", "", {
    limit: 0,
    duplicateThreshold: 0,
    relatedThreshold: 0,
  });
  assert.equal(candidates.length, 1);
  const c = candidates[0];
  assert.equal(c.page_ref, "wiki/concepts/connection-pooling.md");
  assert.equal(c.hint, HintDuplicate);
  assert.equal(c.summary, "Reuse connections.");
  assert.deepEqual(c.tags, ["database"]);
  assert.equal(c.volatility, "stable");
  assert.equal(c.superseded_by, null);
});

test("check honours custom thresholds", async () => {
  const fake = new FakeSearcher();
  fake.hits = [
    hit("a.md", "Connection Pooling", 10),
    hit("b.md", "Zebra Care", 3),
  ];
  const candidates = await check(fake, "Connection Pooling", "", "", {
    limit: 0,
    duplicateThreshold: 0,
    relatedThreshold: 0,
  });
  assert.equal(candidates[0].hint, HintRelated);
  assert.equal(candidates[1].hint, HintDistinct);
});

// ---------------------------------------------------------------------------
// Integration against a real committed vault
// ---------------------------------------------------------------------------

test("check finds its own title in a real vault", async () => {
  const idx = await newFixtureSearcher();
  try {
    const candidates = await check(
      idx,
      "Connection Pooling in Postgres",
      "",
      "",
      { limit: 0, duplicateThreshold: 0, relatedThreshold: 0 },
    );
    assert.ok(refOf(candidates, "wiki/concepts/connection-pooling.md"));
  } finally {
    idx.close();
  }
});

test("check survives noisy new text that an AND query would zero out", async () => {
  // The OR query keeps the real title match even though the summary shares
  // almost nothing — the case an AND-across-terms query would silently lose.
  const idx = await newFixtureSearcher();
  try {
    const candidates = await check(
      idx,
      "Connection Pooling in Postgres",
      "A totally unrelated sentence about zebras and volcanoes.",
      "",
      { limit: 0, duplicateThreshold: 0, relatedThreshold: 0 },
    );
    assert.ok(refOf(candidates, "wiki/concepts/connection-pooling.md"));
  } finally {
    idx.close();
  }
});

test("check with a verbatim body ranks that page highest", async () => {
  const idx = await newFixtureSearcher();
  try {
    const candidates = await check(
      idx,
      "",
      "",
      "Connection pooling reduces per-request handshake overhead by " +
        "reusing a fixed set of open connections across callers.",
      { limit: 0, duplicateThreshold: 0, relatedThreshold: 0 },
    );
    assert.ok(candidates.length > 0);
    assert.equal(candidates[0].page_ref, "wiki/concepts/connection-pooling.md");
  } finally {
    idx.close();
  }
});

test("check hints a real hit as duplicate with permissive thresholds", async () => {
  const idx = await newFixtureSearcher();
  try {
    const candidates = await check(
      idx,
      "Connection Pooling in Postgres",
      "Reuse connections instead of opening a new one per request.",
      "",
      { limit: 0, duplicateThreshold: 1e-6, relatedThreshold: 1e-8 },
    );
    const top = refOf(candidates, "wiki/concepts/connection-pooling.md");
    assert.ok(top);
    assert.equal(top.hint, HintDuplicate);
  } finally {
    idx.close();
  }
});

test("check respects the limit", async () => {
  const idx = await newFixtureSearcher();
  try {
    const candidates = await check(idx, "Connection Pooling", "", "", {
      limit: 1,
      duplicateThreshold: 0,
      relatedThreshold: 0,
    });
    assert.ok(candidates.length <= 1);
  } finally {
    idx.close();
  }
});

test("check returns the full payload from a real vault", async () => {
  const idx = await newFixtureSearcher();
  try {
    const candidates = await check(
      idx,
      "Connection Pooling in Postgres",
      "",
      "",
      { limit: 0, duplicateThreshold: 0, relatedThreshold: 0 },
    );
    const top = refOf(candidates, "wiki/concepts/connection-pooling.md");
    assert.ok(top);
    assert.equal(
      top.summary,
      "Reuse connections instead of opening a new one per request.",
    );
    assert.ok(Array.isArray(top.tags));
    assert.equal(top.superseded_by, null);
  } finally {
    idx.close();
  }
});

// ---------------------------------------------------------------------------
// discover: one call per draft plan page
// ---------------------------------------------------------------------------

test("discover runs check for every plan page", async () => {
  const idx = await newFixtureSearcher();
  try {
    const summary = "Daily flour-and-water feeding keeps a starter active.";
    const mk = (title: string, sum: string): PagePlan => ({
      op: "create",
      title,
      kind: "concept",
      page_ref: "",
      body: "",
      frontmatter: OrderedMap.decode({ summary: sum }),
      edges: OrderedMap.decode({}),
    });
    const pages = [
      mk("Connection Pooling in Postgres", ""),
      mk("Feeding a Sourdough Starter", summary),
    ];
    const results = await discover(idx, pages, {
      limit: 0,
      duplicateThreshold: 0,
      relatedThreshold: 0,
    });
    assert.deepEqual(
      results.map((r) => r.title),
      ["Connection Pooling in Postgres", "Feeding a Sourdough Starter"],
    );
    assert.ok(
      refOf(results[0].candidates, "wiki/concepts/connection-pooling.md"),
    );
  } finally {
    idx.close();
  }
});

test("discover survives an update page with no body", async () => {
  const idx = await newFixtureSearcher();
  try {
    const pages: PagePlan[] = [
      {
        op: "update",
        title: "Connection Pooling in Postgres",
        kind: "",
        page_ref: "wiki/concepts/connection-pooling.md",
        body: null,
        frontmatter: OrderedMap.decode({}),
        edges: OrderedMap.decode({}),
      },
    ];
    const results = await discover(idx, pages, {
      limit: 0,
      duplicateThreshold: 0,
      relatedThreshold: 0,
    });
    assert.equal(results.length, 1);
  } finally {
    idx.close();
  }
});

// ---------------------------------------------------------------------------
// tag helpers
// ---------------------------------------------------------------------------

test("tagsContaining matches case-insensitively, preserving vocabulary order", () => {
  const vocab = [
    { tag: "access-management", count: 7 },
    { tag: "node-access", count: 3 },
    { tag: "csm-ticket", count: 2 },
    { tag: "sourdough", count: 1 },
  ];
  const cases: [string[], string[]][] = [
    [["access"], ["access-management", "node-access"]],
    [["ACCESS"], ["access-management", "node-access"]],
    [
      ["access", "csm"],
      ["access-management", "node-access", "csm-ticket"],
    ],
    [["zzz"], []],
  ];
  for (const [substrings, want] of cases) {
    assert.deepEqual(tagsContaining(vocab, substrings), want);
  }
});

test("tagCounts counts per requested tag in request order, 0 when absent", () => {
  const vocab = [{ tag: "access-management", count: 7 }];
  assert.deepEqual(tagCounts(vocab, ["access-management"]), [
    { tag: "access-management", count: 7 },
  ]);
  assert.deepEqual(tagCounts(vocab, ["user-provisioning"]), [
    { tag: "user-provisioning", count: 0 },
  ]);
  const vocab2 = [
    { tag: "a", count: 1 },
    { tag: "b", count: 2 },
  ];
  assert.deepEqual(tagCounts(vocab2, ["b", "a"]), [
    { tag: "b", count: 2 },
    { tag: "a", count: 1 },
  ]);
});
