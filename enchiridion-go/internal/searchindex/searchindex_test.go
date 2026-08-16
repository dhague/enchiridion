package searchindex

import (
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"testing"
	"time"

	"github.com/dhague/enchiridion/enchiridion-go/internal/vaultgit"
)

// newVault returns an empty vault root plus an open index over it.
func newVault(t *testing.T) (string, *Index) {
	t.Helper()
	root := t.TempDir()
	index, err := Open(root)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	t.Cleanup(func() { index.Close() })
	return root, index
}

// fakeGit scripts the commit dates the index reads, mirroring
// internal/ingestscan's fakeGit. Backdating is the point: a real work tree can
// only commit "now", so date *filtering* can't be exercised against one.
type fakeGit struct{ dates map[string]string }

func (f fakeGit) CommitDates() map[string]string { return f.dates }

// newVaultWithGit is newVault over scripted commit dates.
func newVaultWithGit(t *testing.T, dates map[string]string) (string, *Index) {
	t.Helper()
	root := t.TempDir()
	index, err := openWithGit(root, fakeGit{dates: dates})
	if err != nil {
		t.Fatalf("openWithGit: %v", err)
	}
	t.Cleanup(func() { index.Close() })
	return root, index
}

func writePage(t *testing.T, root, pageRef, text string) {
	t.Helper()
	path := filepath.Join(root, filepath.FromSlash(pageRef))
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

func page(title, summary, body string, tags []string, extra string) string {
	text := "---\ntitle: " + title + "\nsummary: " + summary + "\n"
	if len(tags) > 0 {
		text += "tags:\n"
		for _, tag := range tags {
			text += "  - " + tag + "\n"
		}
	}
	text += extra + "---\n\n" + body + "\n"
	return text
}

func refsOf(hits []Hit) []string {
	refs := make([]string, len(hits))
	for i, hit := range hits {
		refs[i] = hit.PageRef
	}
	return refs
}

func TestTokenizeQuery(t *testing.T) {
	tests := []struct{ in, want string }{
		// A bare hyphenated tag is a syntax error to raw FTS5 MATCH, which
		// is the whole reason phrase-quoting is the default.
		{"wiki-knowledge", `"wiki-knowledge"`},
		{"connection pooling", `"connection" "pooling"`},
		{"  spaced   out  ", `"spaced" "out"`},
		{"", ""},
		{`say "hi"`, `"say" "\"hi\""`},
	}
	for _, tc := range tests {
		if got := TokenizeQuery(tc.in); got != tc.want {
			t.Errorf("TokenizeQuery(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestSearchFindsAPageByBodyText(t *testing.T) {
	root, index := newVault(t)
	writePage(t, root, "wiki/concepts/pooling.md",
		page("Connection pooling", "Reusing connections.",
			"Pooling keeps open database handles around.", []string{"database"}, ""))

	hits, err := index.Search(Query{Text: "database handles"})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if want := []string{"wiki/concepts/pooling.md"}; !reflect.DeepEqual(refsOf(hits), want) {
		t.Fatalf("hits = %v, want %v", refsOf(hits), want)
	}
	hit := hits[0]
	if hit.Kind != "concept" {
		t.Errorf("Kind = %q, want concept", hit.Kind)
	}
	if !reflect.DeepEqual(hit.Tags, []string{"database"}) {
		t.Errorf("Tags = %v", hit.Tags)
	}
	if hit.Score <= 0 {
		t.Errorf("Score = %v, want higher-is-better (positive)", hit.Score)
	}
	if hit.Snippet == nil || *hit.Snippet == "" {
		t.Error("expected a snippet on a text hit")
	}
}

func TestSearchRanksTitleAboveBody(t *testing.T) {
	// The bm25 column weights encode the retrieval skill's frontmatter-first
	// instruction; a title match must outrank a body-only mention.
	root, index := newVault(t)
	writePage(t, root, "wiki/concepts/titled.md",
		page("Pooling", "About it.", "Nothing else here.", nil, ""))
	writePage(t, root, "wiki/concepts/bodied.md",
		page("Something else", "Unrelated.", "A passing mention of pooling.", nil, ""))

	hits, err := index.Search(Query{Text: "pooling"})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if want := []string{"wiki/concepts/titled.md", "wiki/concepts/bodied.md"}; !reflect.DeepEqual(
		refsOf(hits), want) {
		t.Fatalf("hits = %v, want %v", refsOf(hits), want)
	}
}

func TestSearchFiltersOnMetadata(t *testing.T) {
	root, index := newVault(t)
	writePage(t, root, "wiki/concepts/a.md",
		page("A", "First.", "shared word", []string{"alpha", "shared"},
			"source_date: 2026-07-01\nvolatility: stable\n"))
	writePage(t, root, "wiki/entities/b.md",
		page("B", "Second.", "shared word", []string{"beta", "shared"},
			"source_date: 2026-08-01\nvolatility: volatile\n"))

	tests := []struct {
		name string
		q    Query
		want []string
	}{
		{"tags_all", Query{Text: "shared", TagsAll: []string{"alpha"}}, []string{"wiki/concepts/a.md"}},
		{"tags_all conjunctive", Query{Text: "shared", TagsAll: []string{"alpha", "beta"}}, nil},
		{"tags_any", Query{Text: "shared", TagsAny: []string{"alpha", "beta"}},
			[]string{"wiki/concepts/a.md", "wiki/entities/b.md"}},
		{"kind", Query{Text: "shared", Kinds: []string{"entity"}}, []string{"wiki/entities/b.md"}},
		{"since", Query{Text: "shared", Since: "2026-07-15"}, []string{"wiki/entities/b.md"}},
		{"until", Query{Text: "shared", Until: "2026-07-15"}, []string{"wiki/concepts/a.md"}},
		{"volatility", Query{Text: "shared", Volatility: []string{"stable"}}, []string{"wiki/concepts/a.md"}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			hits, err := index.Search(tc.q)
			if err != nil {
				t.Fatalf("Search: %v", err)
			}
			got := refsOf(hits)
			if len(got) != len(tc.want) {
				t.Fatalf("hits = %v, want %v", got, tc.want)
			}
			for _, ref := range tc.want {
				if !slices.Contains(got, ref) {
					t.Fatalf("hits = %v, want %v", got, tc.want)
				}
			}
		})
	}
}

func TestSearchRejectsAnUnknownDateField(t *testing.T) {
	_, index := newVault(t)
	if _, err := index.Search(Query{DateField: "mtime"}); err == nil {
		t.Fatal("expected an error for an unknown date_field")
	}
}

// The #192 reproduction, pinned at the seam that was buggy: `source_date` is
// compared to `--since`/`--until` as raw SQL strings, so a timestamp carrying
// a clock used to be lexicographically greater than the bare date of the same
// day — and an upper bound on that day silently dropped it. Normalising to
// YYYY-MM-DD on read means both pages belong in the range.
func TestSearchUntilIncludesASameDayTimestamp(t *testing.T) {
	root, index := newVault(t)
	writePage(t, root, "wiki/concepts/dated.md",
		page("Dated", "Bare date.", "shared word", nil, "source_date: 2026-07-20\n"))
	writePage(t, root, "wiki/concepts/timed.md",
		page("Timed", "A clock.", "shared word", nil, "source_date: 2026-07-20T14:30:00Z\n"))

	hits, err := index.Search(Query{Text: "shared", Until: "2026-07-20"})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if want := []string{"wiki/concepts/dated.md", "wiki/concepts/timed.md"}; !reflect.DeepEqual(
		refsOf(hits), want) {
		t.Fatalf("hits = %v, want both pages under --until 2026-07-20", refsOf(hits))
	}
	for _, hit := range hits {
		if hit.SourceDate != "2026-07-20" {
			t.Errorf("%s: SourceDate = %q, want the canonical date", hit.PageRef, hit.SourceDate)
		}
	}
}

func TestSearchExcludesSupersededPagesByDefault(t *testing.T) {
	root, index := newVault(t)
	writePage(t, root, "wiki/concepts/old.md",
		page("Old", "The old take.", "shared word", nil, ""))
	writePage(t, root, "wiki/concepts/new.md",
		page("New", "The new take.", "shared word", nil,
			"supersedes:\n  - \"[Old](old.md)\"\n"))

	hits, err := index.Search(Query{Text: "shared"})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if want := []string{"wiki/concepts/new.md"}; !reflect.DeepEqual(refsOf(hits), want) {
		t.Fatalf("hits = %v, want %v", refsOf(hits), want)
	}

	hits, err = index.Search(Query{Text: "shared", IncludeSuperseded: true})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(hits) != 2 {
		t.Fatalf("hits = %v, want both pages", refsOf(hits))
	}
	for _, hit := range hits {
		if hit.PageRef != "wiki/concepts/old.md" {
			continue
		}
		if hit.SupersededBy == nil || *hit.SupersededBy != "wiki/concepts/new.md" {
			t.Errorf("old.SupersededBy = %v, want wiki/concepts/new.md", hit.SupersededBy)
		}
	}
}

func TestSearchWithNoTextIsAPureMetadataQuery(t *testing.T) {
	root, index := newVault(t)
	writePage(t, root, "wiki/concepts/a.md", page("A", "First.", "body", []string{"x"}, ""))
	writePage(t, root, "wiki/entities/b.md", page("B", "Second.", "body", []string{"x"}, ""))

	hits, err := index.Search(Query{TagsAll: []string{"x"}})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if want := []string{"wiki/concepts/a.md", "wiki/entities/b.md"}; !reflect.DeepEqual(
		refsOf(hits), want) {
		t.Fatalf("hits = %v, want %v ordered by page_ref", refsOf(hits), want)
	}
}

func TestSearchHonoursLimit(t *testing.T) {
	root, index := newVault(t)
	for _, name := range []string{"a", "b", "c"} {
		writePage(t, root, "wiki/concepts/"+name+".md",
			page(name, "s", "shared word", nil, ""))
	}
	hits, err := index.Search(Query{Text: "shared", Limit: 2})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(hits) != 2 {
		t.Fatalf("got %d hits, want 2", len(hits))
	}
}

func TestSearchRawIsTheFTS5EscapeHatch(t *testing.T) {
	root, index := newVault(t)
	writePage(t, root, "wiki/concepts/a.md", page("A", "s", "alpha only", nil, ""))
	writePage(t, root, "wiki/concepts/b.md", page("B", "s", "beta only", nil, ""))

	hits, err := index.Search(Query{Text: "alpha OR beta", Raw: true})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(hits) != 2 {
		t.Fatalf("hits = %v, want both pages via the OR operator", refsOf(hits))
	}
}

func TestSearchStalenessScanIsTheCorrectnessPath(t *testing.T) {
	// The design decision the rest of the package assumes: an edit made
	// outside the plugin entirely (git pull, Obsidian) is picked up by the
	// unconditional (mtime_ns, size) scan every search runs, with no
	// reindex call from the caller.
	root, index := newVault(t)
	writePage(t, root, "wiki/concepts/a.md", page("A", "s", "original wording", nil, ""))
	if _, err := index.Search(Query{Text: "original"}); err != nil {
		t.Fatalf("Search: %v", err)
	}

	writePage(t, root, "wiki/concepts/a.md", page("A", "s", "replacement wording", nil, ""))
	hits, err := index.Search(Query{Text: "replacement"})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(hits) != 1 {
		t.Fatalf("hits = %v, want the edited page", refsOf(hits))
	}

	if err := os.Remove(filepath.Join(root, "wiki/concepts/a.md")); err != nil {
		t.Fatal(err)
	}
	hits, err = index.Search(Query{Text: "replacement"})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(hits) != 0 {
		t.Fatalf("hits = %v, want none after the page was deleted", refsOf(hits))
	}
}

func TestReindexStats(t *testing.T) {
	root, index := newVault(t)
	writePage(t, root, "wiki/concepts/a.md", page("A", "s", "body", nil, ""))
	writePage(t, root, "wiki/concepts/b.md", page("B", "s", "body", nil, ""))

	stats, err := index.Reindex(false)
	if err != nil {
		t.Fatalf("Reindex: %v", err)
	}
	if stats.Inserted != 2 || stats.Pages != 2 {
		t.Fatalf("stats = %+v, want 2 inserted / 2 pages", stats)
	}

	writePage(t, root, "wiki/concepts/a.md", page("A", "s", "edited body", nil, ""))
	if err := os.Remove(filepath.Join(root, "wiki/concepts/b.md")); err != nil {
		t.Fatal(err)
	}
	stats, err = index.Reindex(false)
	if err != nil {
		t.Fatalf("Reindex: %v", err)
	}
	if stats.Inserted != 0 || stats.Updated != 1 || stats.Removed != 1 || stats.Pages != 1 {
		t.Fatalf("stats = %+v, want ~1 -1 / 1 page", stats)
	}

	stats, err = index.Reindex(true)
	if err != nil {
		t.Fatalf("Reindex(full): %v", err)
	}
	if stats.Inserted != 1 || stats.Pages != 1 {
		t.Fatalf("stats = %+v, want a full rebuild of 1 page", stats)
	}
}

// #192's data-vs-schema change, pinned: an index built by schema version "2"
// may hold a verbatim timestamp in source_date (the old read path stored it
// unnormalised). The staleness scan only re-upserts pages whose (mtime_ns,
// size) changed, so the open-time version-mismatch rebuild is the one
// mechanism that heals an existing vault with no user action — the lazy
// "no migration script" the ticket asked for.
func TestVersionBumpRebuildsStaleSourceDates(t *testing.T) {
	root, index := newVault(t)
	writePage(t, root, "wiki/concepts/timed.md",
		page("Timed", "A clock.", "shared word", nil, "source_date: 2026-07-20T14:30:00Z\n"))
	if _, err := index.Search(Query{Text: "shared"}); err != nil {
		t.Fatalf("Search: %v", err)
	}
	// Rewind to the pre-#192 world: the row holding the verbatim timestamp
	// old code wrote, under the old schema version.
	if _, err := index.db.Exec(
		"UPDATE page SET source_date = '2026-07-20T14:30:00Z' WHERE page_ref = 'wiki/concepts/timed.md'"); err != nil {
		t.Fatal(err)
	}
	if _, err := index.db.Exec(
		"INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', '2')"); err != nil {
		t.Fatal(err)
	}
	index.Close()

	reopened, err := Open(root)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	defer reopened.Close()

	hits, err := reopened.Search(Query{Text: "shared", Until: "2026-07-20"})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(hits) != 1 || hits[0].SourceDate != "2026-07-20" {
		t.Fatalf("hits = %v, want the rebuilt row canonicalised to 2026-07-20", hits)
	}
}

func TestStatus(t *testing.T) {
	root, index := newVault(t)
	writePage(t, root, "wiki/concepts/a.md", page("A", "s", "body", nil, ""))
	if _, err := index.Reindex(false); err != nil {
		t.Fatalf("Reindex: %v", err)
	}

	status, err := index.Status()
	if err != nil {
		t.Fatalf("Status: %v", err)
	}
	if status.Pages != 1 {
		t.Errorf("Pages = %d, want 1", status.Pages)
	}
	if status.SchemaVersion != SchemaVersion {
		t.Errorf("SchemaVersion = %q, want %q", status.SchemaVersion, SchemaVersion)
	}
	if status.Backend != "fts5" {
		t.Errorf("Backend = %q, want fts5", status.Backend)
	}
	if status.DBSizeBytes <= 0 {
		t.Errorf("DBSizeBytes = %d, want a real size", status.DBSizeBytes)
	}
}

func TestTagCounts(t *testing.T) {
	root, index := newVault(t)
	writePage(t, root, "wiki/concepts/a.md", page("A", "s", "body", []string{"shared", "alpha"}, ""))
	writePage(t, root, "wiki/concepts/b.md", page("B", "s", "body", []string{"shared", "beta"}, ""))

	counts, err := index.TagCounts()
	if err != nil {
		t.Fatalf("TagCounts: %v", err)
	}
	want := []TagCount{
		{Tag: "shared", Count: 2},
		{Tag: "alpha", Count: 1},
		{Tag: "beta", Count: 1},
	}
	if !reflect.DeepEqual(counts, want) {
		t.Fatalf("TagCounts = %v, want %v (most-used first, ties alphabetical)", counts, want)
	}
}

func TestSchemaMismatchTriggersAFullRebuild(t *testing.T) {
	// Delete-and-rebuild *is* the migration strategy — an index written by a
	// future schema version must be wiped, not migrated.
	root, index := newVault(t)
	writePage(t, root, "wiki/concepts/a.md", page("A", "s", "body", nil, ""))
	if _, err := index.Reindex(false); err != nil {
		t.Fatalf("Reindex: %v", err)
	}
	if _, err := index.db.Exec(
		"INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', '999')"); err != nil {
		t.Fatal(err)
	}
	index.Close()

	reopened, err := Open(root)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	defer reopened.Close()

	status, err := reopened.Status()
	if err != nil {
		t.Fatalf("Status: %v", err)
	}
	if status.SchemaVersion != SchemaVersion {
		t.Errorf("SchemaVersion = %q, want the rebuild to reset it to %q",
			status.SchemaVersion, SchemaVersion)
	}
	if status.Pages != 1 {
		t.Errorf("Pages = %d, want the vault re-walked after the rebuild", status.Pages)
	}
}

func TestGitDateFilterBoundsOnBackdatedCommits(t *testing.T) {
	root, index := newVaultWithGit(t, map[string]string{
		"wiki/concepts/old.md": "2020-01-01",
		"wiki/concepts/new.md": "2026-06-01",
	})
	// Same body text on all three, so the bound is the only thing separating
	// them. The third is absent from the map: never committed.
	for _, ref := range []string{"old", "new", "uncommitted"} {
		writePage(t, root, "wiki/concepts/"+ref+".md", page(ref, "s", "shared body", nil, ""))
	}

	hits, err := index.Search(Query{Text: "shared", DateField: "git_date", Since: "2026-01-01"})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if got := refsOf(hits); !reflect.DeepEqual(got, []string{"wiki/concepts/new.md"}) {
		t.Errorf("hits = %v, want only the page committed after the bound", got)
	}

	// Unbounded, every page is a hit — the bound excluded them, not the query.
	hits, err = index.Search(Query{Text: "shared"})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(hits) != 3 {
		t.Errorf("hits = %v, want all three unbounded", refsOf(hits))
	}

	byRef := map[string]*string{}
	for _, hit := range hits {
		byRef[hit.PageRef] = hit.GitDate
	}
	if got := byRef["wiki/concepts/old.md"]; got == nil || *got != "2020-01-01" {
		t.Errorf("old.md GitDate = %v, want the scripted 2020-01-01", got)
	}
	if got := byRef["wiki/concepts/uncommitted.md"]; got != nil {
		t.Errorf("uncommitted.md GitDate = %q, want null for a page absent from the map", *got)
	}
}

// The integration counterpart to the fake-driven test above: this one proves
// Open wires the *real* vaultgit.Repo through. What CommitDates itself reports
// over a branchy history is vaultgit's own tests' business, not this package's.
func TestGitDateFilterUsesCommitHistory(t *testing.T) {
	root := t.TempDir()
	repo := vaultgit.New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	writePage(t, root, "wiki/concepts/a.md", page("A", "s", "committed body", nil,
		"source_date: 2020-01-01\n"))
	if err := repo.Add("."); err != nil {
		t.Fatalf("Add: %v", err)
	}
	if _, err := repo.Commit("first"); err != nil {
		t.Fatalf("Commit: %v", err)
	}

	index, err := Open(root)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	defer index.Close()

	today := time.Now().Format(time.DateOnly)
	hits, err := index.Search(Query{Text: "committed", DateField: "git_date", Since: today})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(hits) != 1 {
		t.Fatalf("hits = %v, want the just-committed page", refsOf(hits))
	}
	if hits[0].GitDate == nil || *hits[0].GitDate != today {
		t.Errorf("GitDate = %v, want %q", hits[0].GitDate, today)
	}
	// source_date is the default field, and it is deliberately not the same
	// signal — the same query bounded on it must find nothing.
	hits, err = index.Search(Query{Text: "committed", Since: today})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(hits) != 0 {
		t.Errorf("hits = %v, want none: source_date is 2020-01-01", refsOf(hits))
	}
}

func TestUncommittedPageHasANullGitDate(t *testing.T) {
	root, index := newVault(t)
	writePage(t, root, "wiki/concepts/a.md", page("A", "s", "body", nil, ""))

	hits, err := index.Search(Query{Text: "body"})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(hits) != 1 {
		t.Fatalf("hits = %v, want one", refsOf(hits))
	}
	if hits[0].GitDate != nil {
		t.Errorf("GitDate = %q, want null outside a git repo", *hits[0].GitDate)
	}
}
