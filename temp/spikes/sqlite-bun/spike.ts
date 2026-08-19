import * as sqliteModule from "node-sqlite3-wasm";

const schemaDDL = `
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS page (
    page_ref      TEXT PRIMARY KEY,
    title         TEXT,
    summary       TEXT,
    kind          TEXT,
    source_date   TEXT,
    git_date      TEXT,
    volatility    TEXT,
    supersedes    TEXT,    -- JSON array of vault-relative page_refs
    superseded_by TEXT     -- single vault-relative page_ref, or NULL
);
CREATE TABLE IF NOT EXISTS page_tag (
    page_ref TEXT,
    tag TEXT,
    PRIMARY KEY (page_ref, tag)
);
CREATE INDEX IF NOT EXISTS ix_page_tag_tag ON page_tag(tag);
CREATE VIRTUAL TABLE IF NOT EXISTS page_fts USING fts5(
    page_ref UNINDEXED, title, summary, body,
    tokenize = 'porter unicode61'
);
`;

function pass(n: number, label: string) {
  console.log(`PASS ${n}: ${label}`);
}

function fail(n: number, label: string, detail: string) {
  console.error(`FAIL ${n}: ${label} — ${detail}`);
  process.exit(1);
}

function main() {
  console.log(`Node.js: ${process.version}`);

  const { Database } = sqliteModule as any;
  if (!Database || typeof Database !== "function") {
    fail(1, "WASM module loads", `Database not found, got: ${Object.keys(sqliteModule).join(", ")}`);
    return;
  }
  pass(1, "WASM module loads without error");

  let db: any;
  try {
    db = new Database();
    db.exec(schemaDDL);
  } catch (e) {
    fail(2, "FTS5 virtual table creates", String(e));
    return;
  }
  pass(2, "FTS5 virtual table creates without error (schema matches production: `page_ref UNINDEXED, title, summary, body, tokenize = 'porter unicode61'`)");

  pass(3, "porter unicode61 tokenizer registers without error");

  try {
    db.run(
      "INSERT INTO page_fts(page_ref, title, summary, body) VALUES (?, ?, ?, ?)",
      [
        "concepts/retrieval.md",
        "Retrieval strategies",
        "Overview of retrieval approaches",
        "Full-text search using FTS5 and bm25 ranking enables fast retrieval.",
      ]
    );
    db.run(
      "INSERT INTO page_fts(page_ref, title, summary, body) VALUES (?, ?, ?, ?)",
      [
        "concepts/ingestion.md",
        "Ingestion pipeline",
        "How raw files become wiki pages",
        "The ingestion pipeline converts raw markdown files into structured wiki pages.",
      ]
    );
    // title carries "FTS5" (column weight 10.0) so bm25 score differs from retrieval.md (body only, weight 1.0)
    db.run(
      "INSERT INTO page_fts(page_ref, title, summary, body) VALUES (?, ?, ?, ?)",
      [
        "synthesis/notes.md",
        "FTS5 search implementation notes",
        "Summary of a working session on search",
        "Worked on implementing FTS5 search and testing the porter stemmer.",
      ]
    );
  } catch (e) {
    fail(4, "Three test pages insert", String(e));
    return;
  }
  pass(4, "Three test pages insert successfully");

  const matchRows: Array<{ page_ref: string }> = db.all(
    "SELECT page_ref FROM page_fts WHERE page_fts MATCH 'ingestion'"
  );
  if (matchRows.length !== 1) {
    fail(5, "Keyword MATCH returns expected page", `got ${matchRows.length} rows, want 1`);
    return;
  }
  if (matchRows[0].page_ref !== "concepts/ingestion.md") {
    fail(5, "Keyword MATCH returns expected page", `got ${matchRows[0].page_ref}`);
    return;
  }
  pass(5, "Keyword MATCH returns expected page and no others");

  const bm25Rows: Array<{ page_ref: string; score: number }> = db.all(
    "SELECT page_ref, bm25(page_fts, 0.0, 10.0, 5.0, 1.0) AS score FROM page_fts WHERE page_fts MATCH 'fts5'"
  );
  if (bm25Rows.length < 2) {
    fail(6, "bm25 returns distinct non-zero values", `got ${bm25Rows.length} rows, want ≥2`);
    return;
  }
  const scores = bm25Rows.map((r) => r.score);
  if (scores.some((s) => s === 0)) {
    fail(6, "bm25 returns distinct non-zero values", `zero score in: ${scores}`);
    return;
  }
  const distinctScores = new Set(scores);
  if (distinctScores.size < 2) {
    fail(6, "bm25 returns distinct non-zero values", `all scores identical: ${scores}`);
    return;
  }
  pass(6, `bm25(page_fts, 0.0, 10.0, 5.0, 1.0) returns distinct, non-zero values: ${scores.map((s) => s.toPrecision(6)).join(", ")}`);

  const phraseRows: Array<{ page_ref: string }> = db.all(
    `SELECT page_ref FROM page_fts WHERE page_fts MATCH '"fast retrieval"'`
  );
  if (phraseRows.length !== 1) {
    fail(7, "Phrase query matches exact phrase only", `got ${phraseRows.length} rows, want 1`);
    return;
  }
  if (phraseRows[0].page_ref !== "concepts/retrieval.md") {
    fail(7, "Phrase query matches exact phrase only", `got ${phraseRows[0].page_ref}`);
    return;
  }
  pass(7, 'Phrase-quoted query "fast retrieval" matches only the page containing that exact phrase');

  db.close();
  console.log("\nAll assertions PASS");
}

main();
