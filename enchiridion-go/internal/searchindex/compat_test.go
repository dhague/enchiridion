package searchindex

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/dhague/enchiridion/enchiridion-go/internal/vaultgit"
)

// Migration to the Go binary is incremental per subcommand (ADR-0011), so
// during the coexistence window one vault's `.wiki-knowledge/index.db` is
// written by whichever implementation the calling skill has been migrated
// to. SQLite's on-disk format is stable across versions, but that guarantee
// says nothing about FTS5 *schema* or *tokenizer* agreement between two
// independently-built SQLite libraries — and the Go binary embeds its own
// build rather than using the system one Python's stdlib links against.
//
// These tests pin both directions: write with one implementation, read with
// the other. A tokenizer drift (`porter unicode61` resolving differently) or
// a schema drift (a column added on one side) fails here rather than
// silently returning nothing to `wiki-researcher`.
//
// The Python side deliberately uses only stdlib `sqlite3` and raw SQL — not
// the plugin's `search_index.py` — so the test needs no virtualenv and pins
// the *schema contract*, not one implementation's helper functions.

func pythonBin(t *testing.T) string {
	t.Helper()
	path, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 not on PATH; skipping cross-implementation compatibility test")
	}
	out, err := exec.Command(path, "-c",
		"import sqlite3;c=sqlite3.connect(':memory:');c.execute('CREATE VIRTUAL TABLE t USING fts5(x)')").
		CombinedOutput()
	if err != nil {
		t.Skipf("python3's sqlite3 has no FTS5 (%s); skipping compatibility test", strings.TrimSpace(string(out)))
	}
	return path
}

func runPython(t *testing.T, script string, args ...string) string {
	t.Helper()
	cmd := exec.Command(pythonBin(t), append([]string{"-c", script}, args...)...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("python3 failed: %v\n%s", err, out)
	}
	return strings.TrimSpace(string(out))
}

// pythonQuery searches an existing index.db the way `search_index.py` does —
// same FTS5 MATCH, same bm25 weights, same phrase-quoting.
const pythonQuery = `
import sqlite3, sys
db, term = sys.argv[1], sys.argv[2]
conn = sqlite3.connect(db)
rows = conn.execute(
    "SELECT p.page_ref FROM page_fts "
    "JOIN page p ON p.page_ref = page_fts.page_ref "
    "WHERE page_fts MATCH ? "
    "ORDER BY bm25(page_fts, 0.0, 10.0, 5.0, 1.0)",
    ('"' + term + '"',),
).fetchall()
print("\n".join(r[0] for r in rows))
`

// pythonWrite creates the shared schema from scratch and indexes one page,
// spelling out the DDL `search_index.py` uses.
const pythonWrite = `
import sqlite3, sys
db, page_ref, title, summary, body, tag = sys.argv[1:7]
conn = sqlite3.connect(db)
conn.executescript("""
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS page (
        page_ref TEXT PRIMARY KEY, title TEXT, summary TEXT, kind TEXT,
        source_date TEXT, git_date TEXT, volatility TEXT, supersedes TEXT,
        superseded_by TEXT, mtime_ns INTEGER, size INTEGER);
    CREATE TABLE IF NOT EXISTS page_tag (
        page_ref TEXT, tag TEXT, PRIMARY KEY (page_ref, tag));
    CREATE INDEX IF NOT EXISTS ix_page_tag_tag ON page_tag(tag);
    CREATE VIRTUAL TABLE IF NOT EXISTS page_fts USING fts5(
        page_ref UNINDEXED, title, summary, body,
        tokenize = 'porter unicode61');
""")
conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', '2')")
conn.execute(
    "INSERT INTO page(page_ref, title, summary, kind, source_date, git_date, "
    "volatility, supersedes, superseded_by, mtime_ns, size) "
    "VALUES (?, ?, ?, 'concept', '2026-07-20', NULL, 'stable', '[]', NULL, 0, 0)",
    (page_ref, title, summary))
conn.execute("INSERT INTO page_tag(page_ref, tag) VALUES (?, ?)", (page_ref, tag))
conn.execute(
    "INSERT INTO page_fts(page_ref, title, summary, body) VALUES (?, ?, ?, ?)",
    (page_ref, title, summary, body))
conn.commit()
`

func TestPythonCanReadAGoWrittenIndex(t *testing.T) {
	pythonBin(t)

	root, index := newVault(t)
	writePage(t, root, "wiki/concepts/pooling.md",
		page("Connection pooling", "Reusing connections.",
			"Pooling keeps open database connections around, so requests need not reconnect.",
			[]string{"database"}, ""))
	if _, err := index.Reindex(false); err != nil {
		t.Fatalf("Reindex: %v", err)
	}
	if err := index.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	dbPath := filepath.Join(root, ".wiki-knowledge", "index.db")
	got := runPython(t, pythonQuery, dbPath, "reconnect")
	if got != "wiki/concepts/pooling.md" {
		t.Fatalf("python read %q from the Go-written index, want wiki/concepts/pooling.md", got)
	}

	// A stemmed match exercises the tokenizer specifically: `porter` must
	// resolve to the same stemmer on both sides, or "connect" would miss a
	// page that only says "connections".
	got = runPython(t, pythonQuery, dbPath, "connect")
	if got != "wiki/concepts/pooling.md" {
		t.Fatalf("python porter-stemmed query returned %q, want the page — tokenizer drift", got)
	}
}

func TestGoCanReadAPythonWrittenIndex(t *testing.T) {
	pythonBin(t)

	root := t.TempDir()
	indexDir := filepath.Join(root, ".wiki-knowledge")
	if err := os.MkdirAll(indexDir, 0o755); err != nil {
		t.Fatal(err)
	}
	dbPath := filepath.Join(indexDir, "index.db")

	// The page exists on disk too, with matching (mtime_ns, size) of 0/0 in
	// the Python-written row — so the Go staleness scan *will* re-upsert it.
	// That is itself the thing under test: a Go scan over a Python index must
	// reconcile rather than fail on an unexpected schema.
	writePage(t, root, "wiki/concepts/pooling.md",
		page("Connection pooling", "Reusing connections.",
			"Pooling keeps open database connections around.", []string{"database"}, ""))
	runPython(t, pythonWrite, dbPath, "wiki/concepts/pooling.md",
		"Connection pooling", "Reusing connections.",
		"Pooling keeps open database connections around.", "database")

	index, err := Open(root, vaultgit.New(root))
	if err != nil {
		t.Fatalf("Open over a Python-written index: %v", err)
	}
	defer index.Close()

	// No reindex first: reading straight out of the Python-written FTS5
	// tables is the case a half-migrated vault actually hits.
	hits, err := index.Search(Query{Text: "connect"})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(hits) != 1 || hits[0].PageRef != "wiki/concepts/pooling.md" {
		t.Fatalf("hits = %v, want the Python-indexed page", refsOf(hits))
	}
	if hits[0].Title != "Connection pooling" {
		t.Errorf("Title = %q, want the Python-written metadata", hits[0].Title)
	}
}

func TestSchemaVersionMatchesThePythonImplementation(t *testing.T) {
	// A drift here silently wipes the other implementation's index on every
	// open, so it is pinned against the Python source rather than left as a
	// comment.
	source, err := os.ReadFile(filepath.Join(
		"..", "..", "..", "wiki-plugin", "scripts", "search_index.py"))
	if err != nil {
		t.Skipf("Python source not available (%v); skipping the version pin", err)
	}
	want := `SCHEMA_VERSION = "` + SchemaVersion + `"`
	if !strings.Contains(string(source), want) {
		t.Fatalf("search_index.py does not contain %s — the two implementations "+
			"share one index.db and must agree on the schema version", want)
	}
}
