// Package searchindex is the SQLite FTS5 lexical index for a vault.
//
// Per [ADR-0006]: a single gitignored `.wiki-knowledge/index.db` at the
// vault root holds a `page` metadata table (kind, tags, source_date,
// git_date, volatility, supersedes, superseded_by, mtime_ns, size) plus an
// FTS5 virtual table over title/summary/body. The composite query shape —
// *"pages updated in the last week, tagged `foo`, containing `bar`"* — is one
// SQL statement with text as MATCH and metadata as WHERE predicates.
//
// **Where correctness lives** (the design decision the rest of the code
// assumes): an unconditional (mtime_ns, size) staleness scan runs on every
// search call, so the index cannot go wrong because a caller forgot to
// update it — including for edits made outside the plugin entirely (git
// pull, Obsidian).
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
// The jump to "3" is a *data* not a *schema* change: #192 made `source_date`
// canonical (YYYY-MM-DD, clock truncated) on read, but rows written by "2"
// still hold a verbatim timestamp. The staleness scan only re-upserts pages
// whose (mtime_ns, size) changed, so those rows would otherwise sit stale
// until each page was touched — the one way to heal an existing index with no
// user action is the version bump's full rebuild on next open.
const SchemaVersion = "3"

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
type Index struct {
	root   string
	git    *vaultgit.Repo
	dbPath string
	db     *sql.DB
}

// Open opens (creating if needed) the index for the vault at root.
func Open(root string) (*Index, error) {
	git := vaultgit.New(root)
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
		if _, err := index.fullRebuild(); err != nil {
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
    superseded_by TEXT,    -- single vault-relative page_ref, or NULL
    mtime_ns      INTEGER,
    size          INTEGER
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

// fullRebuild wipes and re-indexes. Delete-and-rebuild *is* the migration
// strategy — no incremental migrations, ever. Tables are dropped, not just
// emptied, so a schema-spelling change (ADR-0009's `rel` → `page_ref`) is
// absorbed by the same path as a data wipe.
func (i *Index) fullRebuild() (Stats, error) {
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
	return i.reindexWalk()
}

// -- core index walk -------------------------------------------------------

// Reindex re-indexes the vault. full wipes first; otherwise the delta scan
// runs. Both call the same per-page path. DurationMS is computed once here,
// covering whichever path ran in full — for the full-rebuild path that
// includes the drop/recreate, not just the walk.
func (i *Index) Reindex(full bool) (Stats, error) {
	start := time.Now()
	var (
		stats Stats
		err   error
	)
	if full {
		stats, err = i.fullRebuild()
	} else {
		stats, err = i.scan()
	}
	stats.DurationMS = float64(time.Since(start).Microseconds()) / 1000
	return stats, err
}

// scan is the staleness scan: walk `wiki/**`, upsert diffs. The correctness
// path — every search runs it before querying.
//
// Each upsert commits immediately, so a mid-scan crash leaves the index
// partially updated. That's acceptable: the filesystem is the source of
// truth and the next scan reconciles, so the index needs no all-or-nothing
// semantic to protect.
func (i *Index) scan() (Stats, error) {
	var stats Stats

	indexed, err := i.indexedFingerprints()
	if err != nil {
		return stats, err
	}
	refs, err := vault.PageRefs(i.root)
	if err != nil {
		return stats, err
	}
	gitDates := i.git.CommitDates()

	seen := make(map[string]bool, len(refs))
	for _, pageRef := range refs {
		seen[pageRef] = true
		info, err := os.Stat(filepath.Join(i.root, pageRef))
		if err != nil {
			return stats, err
		}
		fresh := fingerprint{mtimeNS: info.ModTime().UnixNano(), size: info.Size()}
		prev, known := indexed[pageRef]
		if known && prev == fresh {
			continue
		}
		text, err := os.ReadFile(filepath.Join(i.root, pageRef))
		if err != nil {
			return stats, err
		}
		if err := i.UpsertPage(pageRef, string(text), gitDates); err != nil {
			return stats, err
		}
		if known {
			stats.Updated++
		} else {
			stats.Inserted++
		}
	}

	for pageRef := range indexed {
		if seen[pageRef] {
			continue
		}
		if err := i.RemovePage(pageRef); err != nil {
			return stats, err
		}
		stats.Removed++
	}

	return i.finishReindex(stats)
}

// reindexWalk is the full walk, no diff — every file upserted. The
// schema-mismatch rebuild path.
func (i *Index) reindexWalk() (Stats, error) {
	var stats Stats

	refs, err := vault.PageRefs(i.root)
	if err != nil {
		return stats, err
	}
	gitDates := i.git.CommitDates()
	for _, pageRef := range refs {
		text, err := os.ReadFile(filepath.Join(i.root, pageRef))
		if err != nil {
			return stats, err
		}
		if err := i.UpsertPage(pageRef, string(text), gitDates); err != nil {
			return stats, err
		}
		stats.Inserted++
	}
	return i.finishReindex(stats)
}

// finishReindex is the tail shared by scan and reindexWalk: invert
// `supersedes` edges, then report the resulting page count.
func (i *Index) finishReindex(stats Stats) (Stats, error) {
	if err := i.recomputeSupersededBy(); err != nil {
		return stats, err
	}
	var err error
	stats.Pages, err = i.countPages()
	return stats, err
}

type fingerprint struct {
	mtimeNS int64
	size    int64
}

func (i *Index) indexedFingerprints() (map[string]fingerprint, error) {
	rows, err := i.db.Query("SELECT page_ref, mtime_ns, size FROM page")
	if err != nil {
		return nil, fmt.Errorf("reading index fingerprints: %w", err)
	}
	defer rows.Close()
	out := map[string]fingerprint{}
	for rows.Next() {
		var pageRef string
		var fp fingerprint
		if err := rows.Scan(&pageRef, &fp.mtimeNS, &fp.size); err != nil {
			return nil, err
		}
		out[pageRef] = fp
	}
	return out, rows.Err()
}

func (i *Index) countPages() (int, error) {
	var n int
	err := i.db.QueryRow("SELECT COUNT(*) FROM page").Scan(&n)
	return n, err
}

// -- per-page upsert/remove ------------------------------------------------

// UpsertPage indexes or replaces one page. pageRef is vault-relative
// (ADR-0009). The file at root/pageRef must already exist — its (mtime_ns,
// size) is stored verbatim, and is what the next staleness scan compares
// against to call this row fresh.
//
// gitDates is the {pageRef: date} map from one [vaultgit.Repo.CommitDates]
// pass; scan callers derive it once per walk and hand it down. When it is nil
// it is computed here — one full-history walk per write.
func (i *Index) UpsertPage(pageRef, text string, gitDates map[string]string) error {
	rec, err := pagerecord.New(pageRef, text)
	if err != nil {
		return err
	}
	info, err := os.Stat(filepath.Join(i.root, pageRef))
	if err != nil {
		return err
	}
	if gitDates == nil {
		gitDates = i.git.CommitDates()
	}
	_, body, _, _ := wikipage.SplitFrontmatter(text)

	supersedes, err := json.Marshal(orEmpty(rec.Supersedes()))
	if err != nil {
		return err
	}

	tx, err := i.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback() //nolint:errcheck // no-op once Commit succeeds

	if err := deletePageRows(tx, pageRef); err != nil {
		return fmt.Errorf("indexing %s: %w", pageRef, err)
	}

	_, err = tx.Exec(
		"INSERT INTO page(page_ref, title, summary, kind, source_date, "+
			"git_date, volatility, supersedes, superseded_by, mtime_ns, size) "+
			"VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
		pageRef, rec.Title, rec.Summary, rec.Kind, rec.SourceDate,
		nullable(gitDates[pageRef]), rec.Volatility, string(supersedes),
		info.ModTime().UnixNano(), info.Size())
	if err != nil {
		return fmt.Errorf("indexing %s: %w", pageRef, err)
	}
	for _, tag := range rec.Tags {
		if _, err := tx.Exec(
			"INSERT OR IGNORE INTO page_tag(page_ref, tag) VALUES (?, ?)",
			pageRef, tag); err != nil {
			return fmt.Errorf("indexing %s: %w", pageRef, err)
		}
	}
	_, err = tx.Exec(
		"INSERT INTO page_fts(page_ref, title, summary, body) VALUES (?, ?, ?, ?)",
		pageRef, rec.Title, rec.Summary, body)
	if err != nil {
		return fmt.Errorf("indexing %s: %w", pageRef, err)
	}
	return tx.Commit()
}

// RemovePage drops one page from every index table.
func (i *Index) RemovePage(pageRef string) error {
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

// Status returns the page count, db size, backend, and schema version.
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
	return Status{
		Pages:         pages,
		DBSizeBytes:   dbSize,
		Backend:       backendName,
		SchemaVersion: schemaVersion,
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
	if _, err := i.scan(); err != nil {
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
	if _, err := i.scan(); err != nil { // correctness path; see package doc
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
