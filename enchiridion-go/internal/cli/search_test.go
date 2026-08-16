package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/dhague/enchiridion/enchiridion-go/internal/vaultgit"
)

// run executes the root command with args, with $WIKI_ROOT pinned to root,
// and returns everything it wrote to stdout.
func run(t *testing.T, root string, args ...string) string {
	t.Helper()
	t.Setenv("WIKI_ROOT", root)

	cmd := NewRootCommand()
	out := &bytes.Buffer{}
	cmd.SetOut(out)
	cmd.SetErr(out)
	cmd.SetArgs(args)
	if err := cmd.Execute(); err != nil {
		t.Fatalf("execute %v: %v\n%s", args, err, out.String())
	}
	return out.String()
}

func newVault(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	writePage(t, root, "wiki/concepts/pooling.md", `---
title: Connection pooling
summary: Reusing database connections across requests.
tags:
  - database
  - performance
source_date: 2026-07-20
volatility: stable
---

Pooling keeps open database handles around.
`)
	writePage(t, root, "wiki/entities/postgres.md", `---
title: PostgreSQL
summary: The database itself.
tags:
  - database
source_date: 2026-06-01
volatility: evolving
---

An open-source relational database.
`)
	return root
}

// writePage writes and commits a page. Search is a view of committed history
// (ADR-0015), so every fixture used through the CLI must be committed to be
// visible — an uncommitted write is exactly what TestSearchStatus exercises
// separately.
func writePage(t *testing.T, root, pageRef, text string) {
	t.Helper()
	path := filepath.Join(root, filepath.FromSlash(pageRef))
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	commitPage(t, root, pageRef)
}

func commitPage(t *testing.T, root, pageRef string) {
	t.Helper()
	repo := vaultgit.New(root)
	if !repo.IsWorkTree() {
		if err := repo.Init(); err != nil {
			t.Fatalf("git init: %v", err)
		}
	}
	if err := repo.Add(pageRef); err != nil {
		t.Fatalf("git add: %v", err)
	}
	if _, err := repo.Commit("test: " + pageRef); err != nil {
		t.Fatalf("git commit: %v", err)
	}
}

func TestSearchTableOutputIsOneAlignedLinePerHit(t *testing.T) {
	root := newVault(t)
	out := run(t, root, "search", "database")

	lines := strings.Split(strings.TrimRight(out, "\n"), "\n")
	if len(lines) != 2 {
		t.Fatalf("got %d lines, want 2:\n%s", len(lines), out)
	}
	for _, line := range lines {
		// Both dates are shown: with age, they are the trust signal the
		// agent has to carry into any answer.
		if !strings.Contains(line, "src=") || !strings.Contains(line, "git=") {
			t.Errorf("line %q is missing a date column", line)
		}
	}
	// The page_ref column is right-padded to the widest ref, so the score
	// column starts at the same offset on every line.
	if scoreColumn(lines[0]) != scoreColumn(lines[1]) {
		t.Errorf("page_ref column is not padded to a common width:\n%s", out)
	}
}

// scoreColumn returns the offset of the second column — the first non-space
// character after the leading page_ref run.
func scoreColumn(line string) int {
	rest := strings.IndexByte(line, ' ')
	if rest < 0 {
		return -1
	}
	return rest + len(line[rest:]) - len(strings.TrimLeft(line[rest:], " "))
}

func TestSearchJSONEmitsOneObjectPerLine(t *testing.T) {
	root := newVault(t)
	out := run(t, root, "search", "pooling", "--json")

	lines := strings.Split(strings.TrimRight(out, "\n"), "\n")
	if len(lines) != 1 {
		t.Fatalf("got %d lines, want 1:\n%s", len(lines), out)
	}
	var hit map[string]any
	if err := json.Unmarshal([]byte(lines[0]), &hit); err != nil {
		t.Fatalf("not JSON: %v\n%s", err, lines[0])
	}
	// The record shape wiki-retrieval's SKILL.md reads.
	for _, key := range []string{
		"page_ref", "score", "title", "summary", "tags", "kind",
		"source_date", "git_date", "volatility", "superseded_by", "snippet",
	} {
		if _, ok := hit[key]; !ok {
			t.Errorf("hit is missing %q: %v", key, hit)
		}
	}
	if hit["page_ref"] != "wiki/concepts/pooling.md" {
		t.Errorf("page_ref = %v", hit["page_ref"])
	}
}

// The #192 reproduction exactly as reported: a `source_date` carrying a clock
// used to escape a same-day `--until` bound because the bound is a raw string
// comparison. Normalising on read means both pages come back.
func TestSearchUntilIncludesASameDayTimestamp(t *testing.T) {
	root := newVault(t)
	writePage(t, root, "wiki/concepts/dated.md", `---
title: Dated
summary: Bare date.
source_date: 2026-07-20
---
shared word
`)
	writePage(t, root, "wiki/concepts/timed.md", `---
title: Timed
summary: A clock.
source_date: 2026-07-20T14:30:00Z
---
shared word
`)

	out := run(t, root, "search", "shared", "--until", "2026-07-20")
	for _, want := range []string{"wiki/concepts/dated.md", "wiki/concepts/timed.md"} {
		if !strings.Contains(out, want) {
			t.Errorf("--until 2026-07-20 output missing %q:\n%s", want, out)
		}
	}
	if strings.Contains(out, "src=2026-07-20T14:30:00Z") {
		t.Errorf("timed.md should surface its canonical date, got:\n%s", out)
	}
}

func TestSearchFlagsReachTheQuery(t *testing.T) {
	root := newVault(t)
	tests := []struct {
		name string
		args []string
		want string
	}{
		{"kind", []string{"search", "database", "--kind", "entity", "--json"}, "wiki/entities/postgres.md"},
		{"tag", []string{"search", "database", "--tag", "performance", "--json"}, "wiki/concepts/pooling.md"},
		{"tag-any", []string{"search", "database", "--tag-any", "performance", "--json"}, "wiki/concepts/pooling.md"},
		{"since", []string{"search", "database", "--since", "2026-07-01", "--json"}, "wiki/concepts/pooling.md"},
		{"until", []string{"search", "database", "--until", "2026-07-01", "--json"}, "wiki/entities/postgres.md"},
		{"volatility", []string{"search", "database", "--volatility", "stable", "--json"}, "wiki/concepts/pooling.md"},
		{"limit", []string{"search", "database", "--limit", "1", "--json"}, ""},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			out := strings.TrimRight(run(t, root, tc.args...), "\n")
			lines := strings.Split(out, "\n")
			if len(lines) != 1 {
				t.Fatalf("got %d hits, want 1:\n%s", len(lines), out)
			}
			if tc.want != "" && !strings.Contains(out, tc.want) {
				t.Errorf("hit = %s, want %s", out, tc.want)
			}
		})
	}
}

func TestSearchCommaSeparatedFiltersAcceptMultipleValues(t *testing.T) {
	root := newVault(t)
	out := run(t, root, "search", "database", "--kind", "concept,entity", "--json")
	if lines := strings.Split(strings.TrimRight(out, "\n"), "\n"); len(lines) != 2 {
		t.Fatalf("got %d hits, want both kinds:\n%s", len(lines), out)
	}
}

func TestSearchWithNoHitsPrintsNothing(t *testing.T) {
	root := newVault(t)
	if out := run(t, root, "search", "kangaroo"); out != "" {
		t.Fatalf("expected no output, got %q", out)
	}
}

func TestSearchStatus(t *testing.T) {
	root := newVault(t)
	if _, err := os.Stat(root); err != nil {
		t.Fatal(err)
	}
	run(t, root, "search", "--reindex")

	out := run(t, root, "search", "--status")
	for _, key := range []string{
		"pages:", "db_size_bytes:", "backend:", "schema_version:",
		"git_head:", "uncommitted_pages:",
	} {
		if !strings.Contains(out, key) {
			t.Errorf("status output is missing %q:\n%s", key, out)
		}
	}

	out = run(t, root, "search", "--status", "--json")
	var status map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(out)), &status); err != nil {
		t.Fatalf("--status --json is not JSON: %v\n%s", err, out)
	}
	if status["pages"].(float64) != 2 {
		t.Errorf("pages = %v, want 2", status["pages"])
	}
	if head, _ := status["git_head"].(string); head == "" {
		t.Errorf("git_head = %v, want the committed HEAD SHA", status["git_head"])
	}
	if status["uncommitted_pages"].(float64) != 0 {
		t.Errorf("uncommitted_pages = %v, want 0", status["uncommitted_pages"])
	}
}

// TestSearchStatusReportsUncommittedPages pins the diagnostic ADR-0015
// exists to make legible: a page on disk but never committed isn't
// searchable, and --status is where that becomes observable.
func TestSearchStatusReportsUncommittedPages(t *testing.T) {
	root := newVault(t)
	run(t, root, "search", "--reindex")

	path := filepath.Join(root, "wiki", "concepts", "draft.md")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("---\ntitle: Draft\nsummary: s\n---\n\nbody\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	out := run(t, root, "search", "--status", "--json")
	var status map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(out)), &status); err != nil {
		t.Fatalf("--status --json is not JSON: %v\n%s", err, out)
	}
	if status["uncommitted_pages"].(float64) != 1 {
		t.Errorf("uncommitted_pages = %v, want 1 for the uncommitted draft", status["uncommitted_pages"])
	}
}

func TestSearchReindex(t *testing.T) {
	root := newVault(t)
	out := run(t, root, "search", "--reindex")
	if !strings.Contains(out, "reindex: 2 pages") {
		t.Errorf("reindex output = %q", out)
	}

	out = run(t, root, "search", "--reindex", "--full", "--json")
	var stats map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(out)), &stats); err != nil {
		t.Fatalf("--reindex --json is not JSON: %v\n%s", err, out)
	}
	if stats["inserted"].(float64) != 2 {
		t.Errorf("inserted = %v, want a full rebuild of 2 pages", stats["inserted"])
	}
}

func TestSearchRejectsAnUnknownDateField(t *testing.T) {
	root := newVault(t)
	t.Setenv("WIKI_ROOT", root)

	cmd := NewRootCommand()
	out := &bytes.Buffer{}
	cmd.SetOut(out)
	cmd.SetErr(out)
	cmd.SetArgs([]string{"search", "database", "--date-field", "mtime"})
	if err := cmd.Execute(); err == nil {
		t.Fatal("expected an error for an unknown --date-field")
	}
}
