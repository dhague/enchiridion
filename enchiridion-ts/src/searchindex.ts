/**
 * SQLite FTS5 lexical index for a vault.
 *
 * Mirrors Go's internal/searchindex package. The index is a materialised view
 * of HEAD's wiki/ tree (ADR-0015): content is read from git blobs, never from
 * files on disk. One connection at a time (no WAL — Resilio Sync + sidecar
 * corruption, ADR-0006).
 */

import nodeSqlite3Wasm from "node-sqlite3-wasm";
import type { Database as DatabaseType } from "node-sqlite3-wasm";
// node-sqlite3-wasm is a CJS package (module.exports = { Database,
// SQLite3Error }). Importing it as a static default lets esbuild INLINE it
// into the bundle (D3 #288 / #290): a packaged install ships cli.cjs +
// node-sqlite3-wasm.wasm with no node_modules, so a runtime createRequire
// would fail to resolve the package. esbuild does the CJS interop at build
// time, so this is safe on both Node and Bun. The .wasm is located relative
// to the bundle by the package itself.
const { Database } = nodeSqlite3Wasm as unknown as {
  Database: typeof import("node-sqlite3-wasm").Database;
};
import { parse as parseYaml } from "yaml";
import fs from "node:fs";
import path from "node:path";

// The Git surface and its types are owned by the vaultgit module (§256):
// this index consumes them, and re-exports the type names so existing callers
// (and the test fake) keep compiling unchanged.
export type { Git, Snapshot, PageChange, VaultGit } from "./vaultgit.js";
import type { Git, Snapshot, PageChange } from "./vaultgit.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export const SCHEMA_VERSION = "4";

/** One search result. Score is higher-is-better (bm25() negated). */
export interface Hit {
  pageRef: string;
  score: number;
  title: string;
  summary: string;
  tags: string[];
  kind: string;
  sourceDate: string;
  gitDate: string | null;
  volatility: string;
  supersededBy: string | null;
  snippet: string | null;
}

export interface Stats {
  pages: number;
  inserted: number;
  updated: number;
  removed: number;
  durationMs: number;
}

export interface Status {
  pages: number;
  dbSizeBytes: number;
  backend: string;
  schemaVersion: string;
  gitHead: string;
  uncommittedPages: number;
}

export interface Query {
  text?: string;
  raw?: boolean;
  tagsAll?: string[];
  tagsAny?: string[];
  kinds?: string[];
  since?: string;
  until?: string;
  dateField?: string;
  volatility?: string[];
  includeSuperseded?: boolean;
  limit?: number;
}

export interface TagCount {
  tag: string;
  count: number;
}

// ---------------------------------------------------------------------------
// Schema
// ---------------------------------------------------------------------------

const BM25_WEIGHTS = "0.0,10.0,5.0,1.0";
const BACKEND_NAME = "fts5";

const SCHEMA_DDL = `
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
    supersedes    TEXT,
    superseded_by TEXT
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

// ---------------------------------------------------------------------------
// Frontmatter / page record helpers
// ---------------------------------------------------------------------------

/** Canonical folder→kind mapping (mirrors place.FolderKinds in Go). */
const FOLDER_KINDS: Record<string, string> = {
  concepts: "concept",
  entities: "entity",
  sources: "source",
  synthesis: "synthesis",
};

/**
 * Derive kind from a vault-relative page ref.
 * `wiki/concepts/foo.md` → `"concept"`.
 * Custom folders fall back to ADR-0008 singularization (strip trailing `s`).
 */
function kindFromRef(pageRef: string): string {
  const parts = pageRef.split("/");
  if (parts.length < 3 || parts[0] !== "wiki") return "";
  const folder = parts[1];
  return FOLDER_KINDS[folder] ?? folder.replace(/s$/, "");
}

/**
 * Split `---\nYAML\n---` frontmatter from the rest of a markdown file.
 * Returns [frontmatterYaml, body].
 */
function splitFrontmatter(src: string): [string, string] {
  if (!src.startsWith("---\n") && !src.startsWith("---\r\n")) return ["", src];
  const rest = src.slice(4);
  const end = rest.search(/\n---(\r?\n|$)/);
  if (end === -1) return ["", src];
  const fm = rest.slice(0, end);
  const body = rest.slice(
    end + 4 /* \n--- */ + (rest[end + 4] === "\r" ? 2 : 1),
  );
  return [fm, body];
}

/** Resolve a markdown link destination relative to pageDir to vault-relative. */
function resolveLink(dest: string, pageDir: string): string {
  if (dest.startsWith("wiki/")) return dest;
  const resolved = path.posix.normalize(path.posix.join(pageDir, dest));
  return resolved;
}

/** Extract the href from `[label](href)`. Returns null if not a markdown link. */
function parseLinkDest(link: string): string | null {
  const m = link.match(/^\[.*?\]\(([^)]+)\)$/);
  return m ? m[1] : null;
}

interface PageRecord {
  title: string;
  summary: string;
  kind: string;
  sourceDate: string;
  volatility: string;
  tags: string[];
  supersedes: string[];
}

function normaliseDate(raw: unknown): string {
  if (!raw) return "";
  const s = String(raw);
  const m = s.match(/^(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : s;
}

function parsePageRecord(pageRef: string, content: string): PageRecord {
  const [fmText] = splitFrontmatter(content);
  const fm: Record<string, unknown> = fmText
    ? ((parseYaml(fmText) as Record<string, unknown>) ?? {})
    : {};

  const pageDir = path.posix.dirname(pageRef);
  const kind = kindFromRef(pageRef);

  const supersedes: string[] = [];
  const rawSupersedes = fm["supersedes"];
  if (Array.isArray(rawSupersedes)) {
    for (const item of rawSupersedes) {
      const dest = parseLinkDest(String(item));
      if (dest) supersedes.push(resolveLink(dest, pageDir));
    }
  }

  const tags: string[] = [];
  const rawTags = fm["tags"];
  if (Array.isArray(rawTags)) {
    for (const t of rawTags) tags.push(String(t));
  }

  return {
    title: fm["title"] ? String(fm["title"]) : "",
    summary: fm["summary"] ? String(fm["summary"]) : "",
    kind,
    sourceDate: normaliseDate(fm["source_date"]),
    volatility: fm["volatility"] ? String(fm["volatility"]) : "",
    tags,
    supersedes,
  };
}

// ---------------------------------------------------------------------------
// Index
// ---------------------------------------------------------------------------

export class Index {
  private constructor(
    private readonly root: string,
    private readonly dbPath: string,
    private readonly db: DatabaseType,
    private readonly git: Git,
  ) {}

  /**
   * Open (creating if needed) the index at root, using the real git repo
   * there via isomorphic-git.
   */
  static async open(root: string): Promise<Index> {
    const { VaultGit } = await import("./vaultgit.js");
    return Index.openWithGit(root, new VaultGit(root));
  }

  /**
   * Open with a substituted Git surface — the test seam. The real
   * entrypoint is `open()`.
   */
  static async openWithGit(root: string, git: Git): Promise<Index> {
    const indexDir = path.join(root, ".wiki-knowledge");
    fs.mkdirSync(indexDir, { recursive: true });
    const dbPath = path.join(indexDir, "index.db");
    const db = new Database(dbPath);
    const index = new Index(root, dbPath, db, git);
    index.createSchema();
    if (!index.schemaOk()) {
      await index.rebuildFull();
    }
    return index;
  }

  /** Open an in-memory index (for tests). */
  static async openInMemory(git: Git): Promise<Index> {
    const db = new Database();
    const index = new Index(":memory:", ":memory:", db, git);
    index.createSchema();
    if (!index.schemaOk()) {
      await index.rebuildFull();
    }
    return index;
  }

  close(): void {
    this.db.close();
  }

  // -------------------------------------------------------------------------
  // Schema lifecycle
  // -------------------------------------------------------------------------

  private createSchema(): void {
    this.db.exec(SCHEMA_DDL);
    this.db.run(
      "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
      [SCHEMA_VERSION],
    );
  }

  private schemaOk(): boolean {
    const row = this.db.get(
      "SELECT value FROM meta WHERE key = 'schema_version'",
    ) as { value: string } | null;
    return row?.value === SCHEMA_VERSION;
  }

  // -------------------------------------------------------------------------
  // Core index walk
  // -------------------------------------------------------------------------

  async reindex(full: boolean): Promise<Stats> {
    const start = Date.now();
    let stats: Stats;
    if (full) {
      stats = await this.rebuildFull();
    } else {
      // Forced range walk — does NOT short-circuit on HEAD == watermark,
      // unlike the search-time sync() path.
      const watermark = this.watermark();
      const snap = await this.git.committedPages(watermark);
      stats = await this.apply(snap);
    }
    stats.durationMs = Date.now() - start;
    return stats;
  }

  private async sync(): Promise<Stats> {
    const watermark = this.watermark();
    const snap = await this.git.committedPages(watermark);
    if (snap.head === watermark && !snap.fullRebuild) {
      return {
        pages: this.countPages(),
        inserted: 0,
        updated: 0,
        removed: 0,
        durationMs: 0,
      };
    }
    return this.apply(snap);
  }

  private async rebuildFull(): Promise<Stats> {
    const snap = await this.git.committedPages("");
    return this.apply(snap);
  }

  private async apply(snap: Snapshot): Promise<Stats> {
    let stats: Stats;
    if (snap.fullRebuild) {
      stats = this.applyFullRebuild(snap);
    } else {
      stats = this.applyDelta(snap);
    }
    this.setWatermark(snap.head);
    return stats;
  }

  private applyFullRebuild(snap: Snapshot): Stats {
    // Drop all tables including meta — delete-and-rebuild is the migration
    // strategy; never in-place ALTER or patching (mirrors Go's applyFullRebuild).
    this.db.exec(`
      DROP TABLE IF EXISTS meta;
      DROP TABLE IF EXISTS page_tag;
      DROP TABLE IF EXISTS page;
      DROP TABLE IF EXISTS page_fts;
    `);
    this.createSchema();
    let inserted = 0;
    for (const page of snap.pages) {
      this.upsertPage(page);
      inserted++;
    }
    this.recomputeSupersededBy();
    const pages = this.countPages();
    return { pages, inserted, updated: 0, removed: 0, durationMs: 0 };
  }

  private applyDelta(snap: Snapshot): Stats {
    let inserted = 0;
    let updated = 0;
    let removed = 0;
    for (const page of snap.pages) {
      if (page.deleted) {
        this.removePage(page.pageRef);
        removed++;
        continue;
      }
      const existed = this.pageIndexed(page.pageRef);
      this.upsertPage(page);
      if (existed) updated++;
      else inserted++;
    }
    this.recomputeSupersededBy();
    const pages = this.countPages();
    return { pages, inserted, updated, removed, durationMs: 0 };
  }

  private countPages(): number {
    const row = this.db.get("SELECT COUNT(*) AS n FROM page") as { n: number };
    return row?.n ?? 0;
  }

  private pageIndexed(pageRef: string): boolean {
    const row = this.db.get(
      "SELECT COUNT(*) AS n FROM page WHERE page_ref = ?",
      [pageRef],
    ) as { n: number };
    return (row?.n ?? 0) > 0;
  }

  // -------------------------------------------------------------------------
  // Watermark
  // -------------------------------------------------------------------------

  private watermark(): string {
    const row = this.db.get(
      "SELECT value FROM meta WHERE key = 'git_head'",
    ) as { value: string } | null;
    return row?.value ?? "";
  }

  private setWatermark(head: string): void {
    this.db.run(
      "INSERT OR REPLACE INTO meta(key, value) VALUES ('git_head', ?)",
      [head],
    );
  }

  // -------------------------------------------------------------------------
  // Per-page upsert / remove
  // -------------------------------------------------------------------------

  private upsertPage(page: PageChange): void {
    const rec = parsePageRecord(page.pageRef, page.content);
    const [, body] = splitFrontmatter(page.content);

    // Remove existing rows before re-inserting (upsert via delete+insert).
    for (const stmt of [
      "DELETE FROM page WHERE page_ref = ?",
      "DELETE FROM page_tag WHERE page_ref = ?",
      "DELETE FROM page_fts WHERE page_ref = ?",
    ]) {
      this.db.run(stmt, [page.pageRef]);
    }

    this.db.run(
      `INSERT INTO page(page_ref, title, summary, kind, source_date,
          git_date, volatility, supersedes, superseded_by)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)`,
      [
        page.pageRef,
        rec.title,
        rec.summary,
        rec.kind,
        rec.sourceDate,
        page.date || null,
        rec.volatility,
        JSON.stringify(rec.supersedes),
      ],
    );
    for (const tag of rec.tags) {
      this.db.run(
        "INSERT OR IGNORE INTO page_tag(page_ref, tag) VALUES (?, ?)",
        [page.pageRef, tag],
      );
    }
    this.db.run(
      "INSERT INTO page_fts(page_ref, title, summary, body) VALUES (?, ?, ?, ?)",
      [page.pageRef, rec.title, rec.summary, body],
    );
  }

  private removePage(pageRef: string): void {
    for (const stmt of [
      "DELETE FROM page WHERE page_ref = ?",
      "DELETE FROM page_tag WHERE page_ref = ?",
      "DELETE FROM page_fts WHERE page_ref = ?",
    ]) {
      this.db.run(stmt, [pageRef]);
    }
  }

  private recomputeSupersededBy(): void {
    this.db.run("UPDATE page SET superseded_by = NULL");
    const rows = this.db.all(
      "SELECT page_ref, supersedes FROM page WHERE supersedes IS NOT NULL",
    ) as { page_ref: string; supersedes: string }[];
    for (const row of rows) {
      let targets: string[];
      try {
        targets = JSON.parse(row.supersedes);
      } catch {
        continue;
      }
      for (const target of targets) {
        this.db.run(
          "UPDATE page SET superseded_by = ? WHERE page_ref = ? AND superseded_by IS NULL",
          [row.page_ref, target],
        );
      }
    }
  }

  // -------------------------------------------------------------------------
  // Public read API
  // -------------------------------------------------------------------------

  async status(): Promise<Status> {
    const pages = this.countPages();
    let dbSizeBytes = 0;
    if (this.dbPath !== ":memory:") {
      try {
        const stat = fs.statSync(this.dbPath);
        dbSizeBytes = stat.size;
      } catch {
        // file might not exist if no writes yet
      }
    }
    const schemaRow = this.db.get(
      "SELECT value FROM meta WHERE key = 'schema_version'",
    ) as { value: string } | null;
    const gitHead = this.watermark();

    let uncommittedPages = 0;
    if (this.root !== ":memory:") {
      const onDisk = countWikiMarkdownFiles(this.root);
      uncommittedPages = Math.max(onDisk - pages, 0);
    }

    return {
      pages,
      dbSizeBytes,
      backend: BACKEND_NAME,
      schemaVersion: schemaRow?.value ?? "",
      gitHead,
      uncommittedPages,
    };
  }

  async tagCounts(): Promise<TagCount[]> {
    await this.sync();
    const rows = this.db.all(
      "SELECT tag, COUNT(*) AS n FROM page_tag GROUP BY tag ORDER BY n DESC, tag ASC",
    ) as { tag: string; n: number }[];
    return rows.map((r) => ({ tag: r.tag, count: r.n }));
  }

  async search(q: Query): Promise<Hit[]> {
    await this.sync();

    const { where, params, err } = buildMetadataWhere(q);
    if (err) throw new Error(err);

    const limit = q.limit && q.limit > 0 ? q.limit : 20;

    let matchExpr = q.text ?? "";
    if (!q.raw) matchExpr = tokenizeQuery(matchExpr);

    if (!matchExpr) {
      return this.queryMetadataOnly(where, params, limit);
    }
    return this.queryFts(matchExpr, where, params, limit);
  }

  private queryMetadataOnly(
    where: string,
    params: unknown[],
    limit: number,
  ): Hit[] {
    const rows = this.db.all(
      `SELECT p.page_ref, 0.0 AS score, p.title, p.summary, p.kind,
              p.source_date, p.git_date, p.volatility, p.superseded_by
       FROM page p WHERE ${where} ORDER BY p.page_ref LIMIT ?`,
      [...params, limit] as import("node-sqlite3-wasm").JSValue[],
    ) as unknown as RawPageRow[];

    return rows.map((r) => ({
      pageRef: r.page_ref,
      score: 0,
      title: r.title ?? "",
      summary: r.summary ?? "",
      tags: [],
      kind: r.kind ?? "",
      sourceDate: r.source_date ?? "",
      gitDate: r.git_date ?? null,
      volatility: r.volatility ?? "",
      supersededBy: r.superseded_by ?? null,
      snippet: null,
    }));
  }

  private queryFts(
    matchExpr: string,
    where: string,
    params: unknown[],
    limit: number,
  ): Hit[] {
    const args = [
      matchExpr,
      ...params,
      limit,
    ] as import("node-sqlite3-wasm").JSValue[];
    const rows = this.db.all(
      `SELECT p.page_ref, bm25(page_fts, ${BM25_WEIGHTS}) AS raw_score,
              p.title, p.summary, p.kind,
              p.source_date, p.git_date, p.volatility, p.superseded_by,
              snippet(page_fts, 3, '', '', '…', 12) AS snip
       FROM page_fts
       JOIN page p ON p.page_ref = page_fts.page_ref
       WHERE page_fts MATCH ? AND ${where}
       ORDER BY raw_score LIMIT ?`,
      args,
    ) as unknown as RawFtsRow[];

    const hits: Hit[] = rows.map((r) => ({
      pageRef: r.page_ref,
      score: -(r.raw_score as number),
      title: r.title ?? "",
      summary: r.summary ?? "",
      tags: [],
      kind: r.kind ?? "",
      sourceDate: r.source_date ?? "",
      gitDate: r.git_date ?? null,
      volatility: r.volatility ?? "",
      supersededBy: r.superseded_by ?? null,
      snippet: r.snip ?? null,
    }));

    for (const hit of hits) {
      hit.tags = this.tagsFor(hit.pageRef);
    }
    return hits;
  }

  private tagsFor(pageRef: string): string[] {
    const rows = this.db.all(
      "SELECT tag FROM page_tag WHERE page_ref = ? ORDER BY tag",
      [pageRef],
    ) as { tag: string }[];
    return rows.map((r) => r.tag);
  }
}

// ---------------------------------------------------------------------------
// Query builder
// ---------------------------------------------------------------------------

interface WhereResult {
  where: string;
  params: unknown[];
  err: string | null;
}

function buildMetadataWhere(q: Query): WhereResult {
  const clauses: string[] = [];
  const params: unknown[] = [];

  if (!q.includeSuperseded) {
    clauses.push("p.superseded_by IS NULL");
  }
  for (const tag of q.tagsAll ?? []) {
    clauses.push(
      "EXISTS (SELECT 1 FROM page_tag t WHERE t.page_ref = p.page_ref AND t.tag = ?)",
    );
    params.push(tag);
  }
  if ((q.tagsAny ?? []).length > 0) {
    clauses.push(
      `EXISTS (SELECT 1 FROM page_tag t WHERE t.page_ref = p.page_ref AND t.tag IN (${placeholders(q.tagsAny!.length)}))`,
    );
    params.push(...q.tagsAny!);
  }
  if ((q.kinds ?? []).length > 0) {
    clauses.push(`p.kind IN (${placeholders(q.kinds!.length)})`);
    params.push(...q.kinds!);
  }

  const dateField = q.dateField || "source_date";
  if (dateField !== "source_date" && dateField !== "git_date") {
    return {
      where: "1=1",
      params: [],
      err: `date_field must be 'source_date' or 'git_date', got '${dateField}'`,
    };
  }
  if (q.since) {
    clauses.push(`p.${dateField} >= ?`);
    params.push(q.since);
  }
  if (q.until) {
    clauses.push(`p.${dateField} <= ?`);
    params.push(q.until);
  }
  if ((q.volatility ?? []).length > 0) {
    clauses.push(`p.volatility IN (${placeholders(q.volatility!.length)})`);
    params.push(...q.volatility!);
  }

  return {
    where: clauses.length > 0 ? clauses.join(" AND ") : "1=1",
    params,
    err: null,
  };
}

/** Phrase-quote each whitespace-separated token for FTS5 MATCH. */
export function tokenizeQuery(text: string): string {
  if (!text.trim()) return "";
  return text
    .trim()
    .split(/\s+/)
    .map((t) => `"${t.replace(/"/g, '\\"')}"`)
    .join(" ");
}

// ---------------------------------------------------------------------------
// Filesystem helpers
// ---------------------------------------------------------------------------

function countWikiMarkdownFiles(root: string): number {
  const wikiDir = path.join(root, "wiki");
  try {
    let count = 0;
    for (const kindEntry of fs.readdirSync(wikiDir, { withFileTypes: true })) {
      if (!kindEntry.isDirectory()) continue;
      const kindDir = path.join(wikiDir, kindEntry.name);
      for (const fileEntry of fs.readdirSync(kindDir, {
        withFileTypes: true,
      })) {
        if (fileEntry.isFile() && fileEntry.name.endsWith(".md")) count++;
      }
    }
    return count;
  } catch {
    return 0;
  }
}

function placeholders(n: number): string {
  return Array(n).fill("?").join(", ");
}

// ---------------------------------------------------------------------------
// Row types (internal)
// ---------------------------------------------------------------------------

interface RawPageRow {
  page_ref: string;
  title: string | null;
  summary: string | null;
  kind: string | null;
  source_date: string | null;
  git_date: string | null;
  volatility: string | null;
  superseded_by: string | null;
}

interface RawFtsRow extends RawPageRow {
  raw_score: number;
  snip: string | null;
}
