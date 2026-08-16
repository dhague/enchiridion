// Package searchindex is the SQLite FTS5 lexical index for a vault.
//
// Per [ADR-0006] (superseded on the point below by [ADR-0015]): a single
// gitignored `.wiki-knowledge/index.db` at the vault root holds a `page`
// metadata table (kind, tags, source_date, git_date, volatility, supersedes,
// superseded_by) plus an FTS5 virtual table over title/summary/body. The
// composite query shape — *"pages updated in the last week, tagged `foo`,
// containing `bar`"* — is one SQL statement with text as MATCH and metadata
// as WHERE predicates.
//
// **Where correctness lives** (the design decision the rest of the code
// assumes): the index is a materialised view of `HEAD`'s `wiki/` tree, not
// of the working tree. `meta.git_head` holds the last `HEAD` the index has
// accounted for; every search compares it to the repository's current
// `HEAD` and, when they differ, reads the delta (or falls back to a full
// tree read) via [vaultgit.Repo.CommittedPages] — content comes from git
// blobs, never from files on disk. A page sitting uncommitted in the
// working tree is invisible to search by construction, not by convention;
// see [ADR-0015] for the reasoning and the rejected mtime-scan alternative.
//
// The schema is free to change on its own terms; bump [SchemaVersion] when
// it does.
//
// There is no `re` fallback backend: the Go binary embeds its own SQLite
// build, so FTS5 is always present. The `backend` field survives in
// [Status] only because it is part of the `search --status` output
// contract.
//
// [ADR-0006]: ../../../docs/adr/0006-stdlib-fts5-not-embeddings.md
// [ADR-0015]: ../../../docs/adr/0015-search-index-view-of-committed-history.md
package searchindex

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/ncruces/go-sqlite3"
	"github.com/ncruces/go-sqlite3/driver"
	"github.com/ncruces/go-sqlite3/ext/fts5"

	"github.com/dhague/enchiridion/enchiridion-go/internal/pagerecord"
	"github.com/dhague/enchiridion/enchiridion-go/internal/vault"
	"github.com/dhague/enchiridion/enchiridion-go/internal/vaultgit"
	"github.com/dhague/enchiridion/enchiridion-go/internal/wikipage"
)

// SchemaVersion is bumped when the on-disk schema changes. A mismatch on
// open triggers a full rebuild — delete-and-rebuild is the migration
// strategy, never an in-place ALTER.
//
// The jump to "4" is [ADR-0015]'s semantics change, load-bearing beyond the
// `mtime_ns`/`size` column drop: every existing `index.db` holds
// working-tree-semantics data (possibly including pages that were never
// committed), and the bump forces one clean rebuild under the new
// committed-history rules with no user action — exactly as the "2" → "3"
// bump did for #192's semantics change with an unchanged shape.
const SchemaVersion = "4"

// bm25Weights are the column weights for page_ref (UNINDEXED), title,
// summary, body — encoding the retrieval skill's "frontmatter-first"
// instruction into the ranking rather than leaving it as prose the agent
// must remember.
const bm25Weights = "0.0,10.0,5.0,1.0"

// backendName is reported by [Index.Status]. Always "fts5" — see the package
// doc on why there is no fallback.
const backendName = "fts5"

// Hit is one search result. Score is higher-is-better — bm25() returns
// negative values, so it is negated on the way out. PageRef is
// vault-relative, so it is directly usable as a plan edge/update target
// (ADR-0009).
type Hit struct {
	PageRef      string   `json:"page_ref"`
	Score        float64  `json:"score"`
	Title        string   `json:"title"`
	Summary      string   `json:"summary"`
	Tags         []string `json:"tags"`
	Kind         string   `json:"kind"`
	SourceDate   string   `json:"source_date"`
	GitDate      *string  `json:"git_date"`
	Volatility   string   `json:"volatility"`
	SupersededBy *string  `json:"superseded_by"`
	Snippet      *string  `json:"snippet"`
}

// Stats reports what one reindex pass did.
type Stats struct {
	Pages      int     `json:"pages"`
	Inserted   int     `json:"inserted"`
	Updated    int     `json:"updated"`
	Removed    int     `json:"removed"`
	DurationMS float64 `json:"duration_ms"`
}

// Status reports the index's shape, for `search --status`.
type Status struct {
	Pages         int    `json:"pages"`
	DBSizeBytes   int64  `json:"db_size_bytes"`
	Backend       string `json:"backend"`
	SchemaVersion string `json:"schema_version"`
	// GitHead is the `HEAD` the index has accounted for ("" if never synced).
	GitHead string `json:"git_head"`
	// UncommittedPages is how many `wiki/**.md` files on disk exceed the
	// indexed page count — pages written but not yet committed, and so not
	// searchable.
	UncommittedPages int `json:"uncommitted_pages"`
}

// Query is the full filter set [Index.Search] accepts. The zero value is a
// pure-metadata query returning every non-superseded page.
type Query struct {
	// Text is tokenized and phrase-quoted by default; see [TokenizeQuery].
	Text string
	// Raw passes Text through as a literal FTS5 expression.
	Raw bool

	TagsAll []string
	TagsAny []string
	Kinds   []string
	// Since and Until are inclusive ISO-date bounds on DateField.
	Since      string
	Until      string
	DateField  string
	Volatility []string

	IncludeSuperseded bool
	Limit             int
}

// TokenizeQuery splits text on whitespace and phrase-quotes each term.
//
// FTS5's MATCH is a query language, not a string, and ordinary vault
// vocabulary is a syntax error in it — a hyphenated tag like
// `wiki-knowledge` raises "no such column: knowledge". Hence quoting by
// default, with [Query].Raw the escape hatch for callers who actually want
// NEAR(), OR, and prefix operators.
func TokenizeQuery(text string) string {
	terms := strings.Fields(text)
	quoted := make([]string, 0, len(terms))
	for _, term := range terms {
		quoted = append(quoted, `"`+strings.ReplaceAll(term, `"`, `\"`)+`"`)
	}
	return strings.Join(quoted, " ")
}

// Index is the lexical index for one vault. **At most one live Index per
// vault at a time**: writes commit immediately (no WAL, ADR-0006), so a
// second connection racing the first surfaces as `database is locked`.
//
// [Open] pairs with [Index.Close], so the lifetime is explicit and one
// owner holds it (ADR-0010). The owner is the command — the single
// entrypoint of a one-shot process — which opens once and passes the handle
// down (see [github.com/dhague/enchiridion/enchiridion-go/internal/discover]).
// Packages below it take a searcher, never a root, so they cannot open a
// competing connection; that is why no `ForRoot` exists here.
//
// The unit of address is a vault-relative page reference
// (`wiki/concepts/foo.md`) throughout — schema, upsert API, and search
// results alike.
// Git is the slice of [vaultgit.Repo] the index needs, named as an interface
// so tests can script commit snapshots rather than standing up a work tree.
// The real-git behaviour behind it — reachability, range enumeration, merge
// handling, blob reads — is covered by vaultgit's own tests; this package's
// tests only need "apply a snapshot to SQL correctly."
//
// The surface is *lenient*: a missing repository or one with no commits
// yields an empty [vaultgit.Snapshot] rather than an error — "nothing
// committed, nothing indexed."
type Git interface {
	// CommittedPages returns the vault's wiki pages changed since commit
	// since ("" for the full tree). See [vaultgit.Repo.CommittedPages].
	CommittedPages(since string) (vaultgit.Snapshot, error)
}

var _ Git = (*vaultgit.Repo)(nil)

type Index struct {
	root   string
	git    Git
	dbPath string
	db     *sql.DB
}

// Open opens (creating if needed) the index for the vault at root, reading
// git dates from the real repository there.
func Open(root string) (*Index, error) {
	return openWithGit(root, vaultgit.New(root))
}

// openWithGit is Open with the git surface substituted — the in-package test
// seam. Open is the only exported constructor, so callers still cannot open a
// competing connection or vary the root independently of the repository.
func openWithGit(root string, git Git) (*Index, error) {
	indexDir := filepath.Join(root, ".wiki-knowledge")
	if err := os.MkdirAll(indexDir, 0o755); err != nil {
		return nil, fmt.Errorf("creating %s: %w", indexDir, err)
	}
	dbPath := filepath.Join(indexDir, "index.db")
	// No WAL: Resilio Sync plus SQLite sidecar files corrupts (ADR-0006), so
	// the default rollback journal is left alone.
	//
	// FTS5 ships as a loadable extension in this driver's SQLite build, so it
	// is registered per connection rather than assumed present. There is no
	// runtime FTS5 capability probe: registration failing here is a broken
	// binary, not a platform whose SQLite was compiled without the module.
	db, err := driver.Open("file:"+dbPath, func(conn *sqlite3.Conn) error {
		return fts5.Register(conn)
	})
	if err != nil {
		return nil, fmt.Errorf("opening %s: %w", dbPath, err)
	}
	// ncruces/go-sqlite3 permits one connection at a time; every caller here
	// is a one-shot CLI, so serialising is free.
	db.SetMaxOpenConns(1)

	index := &Index{root: root, git: git, dbPath: dbPath, db: db}
	if err := index.createSchema(); err != nil {
		db.Close()
		return nil, err
	}
	ok, err := index.schemaOK()
	if err != nil {
		db.Close()
		return nil, err
	}
	if !ok {
		if _, err := index.rebuildFull(); err != nil {
			db.Close()
			return nil, err
		}
	}
	return index, nil
}

// Close releases the underlying database handle.
func (i *Index) Close() error { return i.db.Close() }

// -- schema lifecycle ------------------------------------------------------

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
`

func (i *Index) createSchema() error {
	if _, err := i.db.Exec(schemaDDL); err != nil {
		return fmt.Errorf("creating index schema: %w", err)
	}
	// Seed the schema version the first time; check it on every open.
	_, err := i.db.Exec(
		"INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
		SchemaVersion)
	if err != nil {
		return fmt.Errorf("seeding schema version: %w", err)
	}
	return nil
}

func (i *Index) schemaOK() (bool, error) {
	var value string
	err := i.db.QueryRow("SELECT value FROM meta WHERE key = 'schema_version'").Scan(&value)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("reading schema version: %w", err)
	}
	return value == SchemaVersion, nil
}

// -- core index walk -------------------------------------------------------

// Reindex re-indexes the vault. full forces a full tree read from HEAD and
// wipes first; otherwise the range walk to HEAD runs — the same sync every
// search performs, triggered explicitly. DurationMS is computed once here,
// covering whichever path ran — for the full-rebuild path that includes the
// drop/recreate, not just the walk.
func (i *Index) Reindex(full bool) (Stats, error) {
	start := time.Now()
	var (
		stats Stats
		err   error
	)
	if full {
		stats, err = i.rebuildFull()
	} else {
		stats, err = i.sync()
	}
	stats.DurationMS = float64(time.Since(start).Microseconds()) / 1000
	return stats, err
}

// sync is the correctness path: bring the index up to date with `HEAD` by
// reading committed history, then report what changed. Every search and a
// bare `--reindex` run this.
//
// `HEAD == watermark` is the common case and is free: [Git.CommittedPages]
// answers it in one commit lookup, and nothing further is written — no
// table touched, no new watermark to persist over the one already correct.
func (i *Index) sync() (Stats, error) {
	watermark, err := i.watermark()
	if err != nil {
		return Stats{}, err
	}
	snap, err := i.git.CommittedPages(watermark)
	if err != nil {
		return Stats{}, err
	}
	if snap.Head == watermark && !snap.FullRebuild {
		pages, err := i.countPages()
		return Stats{Pages: pages}, err
	}
	return i.apply(snap)
}

// rebuildFull forces a full tree read from HEAD regardless of the current
// watermark — `--reindex --full`.
func (i *Index) rebuildFull() (Stats, error) {
	snap, err := i.git.CommittedPages("")
	if err != nil {
		return Stats{}, err
	}
	return i.apply(snap)
}

// apply writes snap into the index — a full rebuild when snap.FullRebuild,
// otherwise an upsert/delete per changed page — and advances the watermark.
// The watermark advances whenever this runs, including when the range
// touched no pages: it means "the HEAD already accounted for," not "the HEAD
// that last changed something," so a vault whose commits mostly touch raw/
// doesn't re-walk a growing range on every search.
func (i *Index) apply(snap vaultgit.Snapshot) (Stats, error) {
	var (
		stats Stats
		err   error
	)
	if snap.FullRebuild {
		stats, err = i.applyFullRebuild(snap)
	} else {
		stats, err = i.applyDelta(snap)
	}
	if err != nil {
		return stats, err
	}
	if err := i.setWatermark(snap.Head); err != nil {
		return stats, err
	}
	return stats, nil
}

// applyFullRebuild wipes and re-indexes every page in snap. Delete-and-rebuild
// *is* the migration strategy — no incremental migrations, ever. Tables are
// dropped, not just emptied, so a schema-spelling change (ADR-0009's `rel` →
// `page_ref`) is absorbed by the same path as a data wipe.
func (i *Index) applyFullRebuild(snap vaultgit.Snapshot) (Stats, error) {
	_, err := i.db.Exec(`
		DROP TABLE IF EXISTS page_tag;
		DROP TABLE IF EXISTS page;
		DROP TABLE IF EXISTS page_fts;
	`)
	if err != nil {
		return Stats{}, fmt.Errorf("dropping index tables: %w", err)
	}
	if err := i.createSchema(); err != nil {
		return Stats{}, err
	}
	if _, err := i.db.Exec(
		"INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
		SchemaVersion); err != nil {
		return Stats{}, fmt.Errorf("writing schema version: %w", err)
	}

	var stats Stats
	for _, page := range snap.Pages {
		if err := i.upsertPage(page); err != nil {
			return stats, err
		}
		stats.Inserted++
	}
	return i.finishReindex(stats)
}

// applyDelta upserts or removes exactly the pages snap enumerated.
func (i *Index) applyDelta(snap vaultgit.Snapshot) (Stats, error) {
	var stats Stats
	for _, page := range snap.Pages {
		if page.Deleted {
			if err := i.removePage(page.PageRef); err != nil {
				return stats, err
			}
			stats.Removed++
			continue
		}
		existed, err := i.pageIndexed(page.PageRef)
		if err != nil {
			return stats, err
		}
		if err := i.upsertPage(page); err != nil {
			return stats, err
		}
		if existed {
			stats.Updated++
		} else {
			stats.Inserted++
		}
	}
	return i.finishReindex(stats)
}

// finishReindex is the tail shared by both apply paths: invert `supersedes`
// edges, then report the resulting page count.
func (i *Index) finishReindex(stats Stats) (Stats, error) {
	if err := i.recomputeSupersededBy(); err != nil {
		return stats, err
	}
	var err error
	stats.Pages, err = i.countPages()
	return stats, err
}

func (i *Index) countPages() (int, error) {
	var n int
	err := i.db.QueryRow("SELECT COUNT(*) FROM page").Scan(&n)
	return n, err
}

func (i *Index) pageIndexed(pageRef string) (bool, error) {
	var n int
	err := i.db.QueryRow("SELECT COUNT(*) FROM page WHERE page_ref = ?", pageRef).Scan(&n)
	return n > 0, err
}

// -- watermark ---------------------------------------------------------

// watermark returns the `HEAD` the index has already accounted for, or ""
// if the index has never been synced.
func (i *Index) watermark() (string, error) {
	var value string
	err := i.db.QueryRow("SELECT value FROM meta WHERE key = 'git_head'").Scan(&value)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	if err != nil {
		return "", fmt.Errorf("reading watermark: %w", err)
	}
	return value, nil
}

func (i *Index) setWatermark(head string) error {
	if _, err := i.db.Exec(
		"INSERT OR REPLACE INTO meta(key, value) VALUES ('git_head', ?)", head); err != nil {
		return fmt.Errorf("writing watermark: %w", err)
	}
	return nil
}

// -- per-page upsert/remove ------------------------------------------------

// upsertPage indexes or replaces one page from its committed content and
// date. pageRef is vault-relative (ADR-0009).
func (i *Index) upsertPage(page vaultgit.PageChange) error {
	rec, err := pagerecord.New(page.PageRef, page.Content)
	if err != nil {
		return err
	}
	_, body, _, _ := wikipage.SplitFrontmatter(page.Content)

	supersedes, err := json.Marshal(orEmpty(rec.Supersedes()))
	if err != nil {
		return err
	}

	tx, err := i.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback() //nolint:errcheck // no-op once Commit succeeds

	if err := deletePageRows(tx, page.PageRef); err != nil {
		return fmt.Errorf("indexing %s: %w", page.PageRef, err)
	}

	_, err = tx.Exec(
		"INSERT INTO page(page_ref, title, summary, kind, source_date, "+
			"git_date, volatility, supersedes, superseded_by) "+
			"VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
		page.PageRef, rec.Title, rec.Summary, rec.Kind, rec.SourceDate,
		nullable(page.Date), rec.Volatility, string(supersedes))
	if err != nil {
		return fmt.Errorf("indexing %s: %w", page.PageRef, err)
	}
	for _, tag := range rec.Tags {
		if _, err := tx.Exec(
			"INSERT OR IGNORE INTO page_tag(page_ref, tag) VALUES (?, ?)",
			page.PageRef, tag); err != nil {
			return fmt.Errorf("indexing %s: %w", page.PageRef, err)
		}
	}
	_, err = tx.Exec(
		"INSERT INTO page_fts(page_ref, title, summary, body) VALUES (?, ?, ?, ?)",
		page.PageRef, rec.Title, rec.Summary, body)
	if err != nil {
		return fmt.Errorf("indexing %s: %w", page.PageRef, err)
	}
	return tx.Commit()
}

// removePage drops one page from every index table.
func (i *Index) removePage(pageRef string) error {
	tx, err := i.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback() //nolint:errcheck // no-op once Commit succeeds
	if err := deletePageRows(tx, pageRef); err != nil {
		return fmt.Errorf("removing %s from index: %w", pageRef, err)
	}
	return tx.Commit()
}

// deletePageRows removes pageRef's rows from every index table, the common
// prefix of both a replace (UpsertPage) and a drop (RemovePage).
func deletePageRows(tx *sql.Tx, pageRef string) error {
	for _, stmt := range []string{
		"DELETE FROM page WHERE page_ref = ?",
		"DELETE FROM page_tag WHERE page_ref = ?",
		"DELETE FROM page_fts WHERE page_ref = ?",
	} {
		if _, err := tx.Exec(stmt, pageRef); err != nil {
			return err
		}
	}
	return nil
}

// recomputeSupersededBy inverts every `supersedes` into the targets'
// superseded_by (one per target — the immediate superseder).
func (i *Index) recomputeSupersededBy() error {
	if _, err := i.db.Exec("UPDATE page SET superseded_by = NULL"); err != nil {
		return err
	}
	rows, err := i.db.Query("SELECT page_ref, supersedes FROM page WHERE supersedes IS NOT NULL")
	if err != nil {
		return err
	}
	type edge struct {
		from    string
		targets []string
	}
	var edges []edge
	for rows.Next() {
		var pageRef, raw string
		if err := rows.Scan(&pageRef, &raw); err != nil {
			rows.Close()
			return err
		}
		var targets []string
		if err := json.Unmarshal([]byte(raw), &targets); err != nil {
			continue // a row this implementation didn't write; skip it
		}
		edges = append(edges, edge{from: pageRef, targets: targets})
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return err
	}
	for _, e := range edges {
		for _, target := range e.targets {
			if _, err := i.db.Exec(
				"UPDATE page SET superseded_by = ? WHERE page_ref = ? AND superseded_by IS NULL",
				e.from, target); err != nil {
				return err
			}
		}
	}
	return nil
}

// -- public read API -------------------------------------------------------

// Status returns the page count, db size, backend, schema version, indexed
// `HEAD`, and how many `wiki/**.md` files on disk exceed the indexed page
// count — pages written but not yet committed, and so not searchable (see
// the package doc). That comparison is the sole surviving use of the vault
// import, and the one directory walk in this package that isn't on the
// search hot path.
func (i *Index) Status() (Status, error) {
	pages, err := i.countPages()
	if err != nil {
		return Status{}, err
	}
	var dbSize int64
	if info, err := os.Stat(i.dbPath); err == nil {
		dbSize = info.Size()
	}
	var schemaVersion string
	if err := i.db.QueryRow(
		"SELECT value FROM meta WHERE key = 'schema_version'").Scan(&schemaVersion); err != nil &&
		!errors.Is(err, sql.ErrNoRows) {
		return Status{}, err
	}
	gitHead, err := i.watermark()
	if err != nil {
		return Status{}, err
	}
	onDisk, err := vault.PageRefs(i.root)
	if err != nil {
		return Status{}, err
	}
	uncommitted := max(len(onDisk)-pages, 0)
	return Status{
		Pages:            pages,
		DBSizeBytes:      dbSize,
		Backend:          backendName,
		SchemaVersion:    schemaVersion,
		GitHead:          gitHead,
		UncommittedPages: uncommitted,
	}, nil
}

// TagCount is one tag with the number of pages carrying it.
type TagCount struct {
	Tag   string `json:"tag"`
	Count int    `json:"count"`
}

// TagCounts returns every tag with its usage count, most-used first, ties
// alphabetical. Staleness-scans first, like [Index.Search], so a tag minted
// by an external edit is visible immediately.
func (i *Index) TagCounts() ([]TagCount, error) {
	if _, err := i.sync(); err != nil {
		return nil, err
	}
	rows, err := i.db.Query(
		"SELECT tag, COUNT(*) AS n FROM page_tag GROUP BY tag ORDER BY n DESC, tag ASC")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []TagCount
	for rows.Next() {
		var tc TagCount
		if err := rows.Scan(&tc.Tag, &tc.Count); err != nil {
			return nil, err
		}
		out = append(out, tc)
	}
	return out, rows.Err()
}

// Search is the headline API: staleness-scan, then query.
func (i *Index) Search(q Query) ([]Hit, error) {
	if _, err := i.sync(); err != nil { // correctness path; see package doc
		return nil, err
	}

	where, params, err := metadataWhere(q)
	if err != nil {
		return nil, err
	}
	limit := q.Limit
	if limit <= 0 {
		limit = 20
	}

	matchExpr := q.Text
	if !q.Raw {
		matchExpr = TokenizeQuery(q.Text)
	}

	if matchExpr == "" {
		// Pure-metadata query: no MATCH. The WHERE-only path.
		return i.queryMetadataOnly(where, params, limit)
	}
	return i.queryFTS(matchExpr, where, params, limit)
}

func (i *Index) queryMetadataOnly(where string, params []any, limit int) ([]Hit, error) {
	rows, err := i.db.Query(
		"SELECT p.page_ref, 0.0, p.title, p.summary, p.kind, "+
			"       p.source_date, p.git_date, p.volatility, p.superseded_by "+
			"FROM page p WHERE "+where+" ORDER BY p.page_ref LIMIT ?",
		append(params, limit)...)
	if err != nil {
		return nil, fmt.Errorf("metadata query: %w", err)
	}
	defer rows.Close()

	hits := []Hit{}
	for rows.Next() {
		var h Hit
		if err := rows.Scan(&h.PageRef, &h.Score, &h.Title, &h.Summary, &h.Kind,
			&h.SourceDate, &h.GitDate, &h.Volatility, &h.SupersededBy); err != nil {
			return nil, err
		}
		// Tags are left empty on this path — it's the pure-metadata row
		// mapper.
		h.Tags = []string{}
		hits = append(hits, h)
	}
	return hits, rows.Err()
}

func (i *Index) queryFTS(matchExpr, where string, params []any, limit int) ([]Hit, error) {
	args := append([]any{matchExpr}, params...)
	args = append(args, limit)
	rows, err := i.db.Query(
		"SELECT p.page_ref, bm25(page_fts, "+bm25Weights+") AS raw_score, "+
			"       p.title, p.summary, p.kind, "+
			"       p.source_date, p.git_date, p.volatility, p.superseded_by, "+
			"       snippet(page_fts, 3, '', '', '…', 12) AS snip "+
			"FROM page_fts "+
			"JOIN page p ON p.page_ref = page_fts.page_ref "+
			"WHERE page_fts MATCH ? AND "+where+" "+
			"ORDER BY raw_score LIMIT ?",
		args...)
	if err != nil {
		return nil, fmt.Errorf("search query: %w", err)
	}
	defer rows.Close()

	hits := []Hit{}
	for rows.Next() {
		var h Hit
		var rawScore float64
		if err := rows.Scan(&h.PageRef, &rawScore, &h.Title, &h.Summary, &h.Kind,
			&h.SourceDate, &h.GitDate, &h.Volatility, &h.SupersededBy, &h.Snippet); err != nil {
			return nil, err
		}
		h.Score = -rawScore // bm25() is negative; negate for higher-is-better
		hits = append(hits, h)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	for idx := range hits {
		tags, err := i.tagsFor(hits[idx].PageRef)
		if err != nil {
			return nil, err
		}
		hits[idx].Tags = tags
	}
	return hits, nil
}

// metadataWhere builds the WHERE fragment shared by both query paths.
func metadataWhere(q Query) (string, []any, error) {
	var clauses []string
	var params []any

	if !q.IncludeSuperseded {
		clauses = append(clauses, "p.superseded_by IS NULL")
	}
	for _, tag := range q.TagsAll {
		clauses = append(clauses,
			"EXISTS (SELECT 1 FROM page_tag t WHERE t.page_ref = p.page_ref AND t.tag = ?)")
		params = append(params, tag)
	}
	if len(q.TagsAny) > 0 {
		clauses = append(clauses,
			"EXISTS (SELECT 1 FROM page_tag t WHERE t.page_ref = p.page_ref AND t.tag IN ("+
				placeholders(len(q.TagsAny))+"))")
		params = append(params, toAny(q.TagsAny)...)
	}
	if len(q.Kinds) > 0 {
		clauses = append(clauses, "p.kind IN ("+placeholders(len(q.Kinds))+")")
		params = append(params, toAny(q.Kinds)...)
	}

	dateField := q.DateField
	if dateField == "" {
		dateField = "source_date"
	}
	if dateField != "source_date" && dateField != "git_date" {
		return "", nil, fmt.Errorf(
			"date_field must be 'source_date' or 'git_date', got %q", dateField)
	}
	if q.Since != "" {
		clauses = append(clauses, "p."+dateField+" >= ?")
		params = append(params, q.Since)
	}
	if q.Until != "" {
		clauses = append(clauses, "p."+dateField+" <= ?")
		params = append(params, q.Until)
	}
	if len(q.Volatility) > 0 {
		clauses = append(clauses, "p.volatility IN ("+placeholders(len(q.Volatility))+")")
		params = append(params, toAny(q.Volatility)...)
	}

	if len(clauses) == 0 {
		return "1=1", params, nil
	}
	return strings.Join(clauses, " AND "), params, nil
}

func (i *Index) tagsFor(pageRef string) ([]string, error) {
	rows, err := i.db.Query(
		"SELECT tag FROM page_tag WHERE page_ref = ? ORDER BY tag", pageRef)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	tags := []string{}
	for rows.Next() {
		var tag string
		if err := rows.Scan(&tag); err != nil {
			return nil, err
		}
		tags = append(tags, tag)
	}
	return tags, rows.Err()
}

// -- small helpers ---------------------------------------------------------

func placeholders(n int) string {
	return strings.TrimSuffix(strings.Repeat("?,", n), ",")
}

func toAny(values []string) []any {
	out := make([]any, len(values))
	for i, v := range values {
		out[i] = v
	}
	return out
}

// nullable maps the empty string to SQL NULL, so an unknown git_date reads
// back as null rather than "".
func nullable(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func orEmpty(values []string) []string {
	if values == nil {
		return []string{}
	}
	return values
}
