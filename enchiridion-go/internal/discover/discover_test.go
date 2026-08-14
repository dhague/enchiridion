package discover

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/dhague/enchiridion/enchiridion-go/internal/ingest"
	"github.com/dhague/enchiridion/enchiridion-go/internal/searchindex"
)

// writeFixturePage writes one page under wiki/, mirroring discover.py's test
// fixture builder.
func writeFixturePage(t *testing.T, root, rel, title, summary, body string) {
	t.Helper()
	path := filepath.Join(root, "wiki", filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	text := "---\ntitle: " + title + "\nsummary: " + summary + "\n---\n" + body + "\n"
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

func newFixtureVault(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	writeFixturePage(t, root, "concepts/connection-pooling.md",
		"Connection Pooling in Postgres",
		"Reuse connections instead of opening a new one per request.",
		"Connection pooling reduces per-request handshake overhead by "+
			"reusing a fixed set of open connections across callers.",
	)
	writeFixturePage(t, root, "concepts/sourdough-starter.md",
		"Feeding a Sourdough Starter",
		"Daily flour-and-water feeding keeps a starter active.",
		"A sourdough starter needs equal parts flour and water once a day, "+
			"kept warm, to stay active enough to leaven bread.",
	)
	return root
}

// newFixtureSearcher opens the one index handle a test gets, mirroring the
// command's ownership of it (ADR-0010) — nothing under this package opens its
// own.
func newFixtureSearcher(t *testing.T) Searcher {
	t.Helper()
	index, err := searchindex.Open(newFixtureVault(t), nil)
	if err != nil {
		t.Fatalf("searchindex.Open: %v", err)
	}
	t.Cleanup(func() { index.Close() })
	return index
}

func refOf(candidates []Candidate, ref string) *Candidate {
	for i := range candidates {
		if candidates[i].PageRef == ref {
			return &candidates[i]
		}
	}
	return nil
}

// --- classify: pure boundary logic -----------------------------------------

func TestClassify(t *testing.T) {
	tests := []struct {
		name     string
		score    float64
		shares   bool
		dup, rel float64
		want     Hint
	}{
		{"high score with shared title token is duplicate", 20.0, true, 15.0, 5.0, HintDuplicate},
		{"high score without shared title token is refines", 20.0, false, 15.0, 5.0, HintRefines},
		{"mid score is related regardless of title", 10.0, true, 15.0, 5.0, HintRelated},
		{"mid score related without title", 10.0, false, 15.0, 5.0, HintRelated},
		{"low score is distinct", 1.0, true, 15.0, 5.0, HintDistinct},
		{"duplicate threshold is inclusive", 15.0, true, 15.0, 5.0, HintDuplicate},
		{"related threshold is inclusive", 5.0, false, 15.0, 5.0, HintRelated},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := classify(tc.score, tc.shares, tc.dup, tc.rel); got != tc.want {
				t.Errorf("classify(%v, %v) = %q, want %q", tc.score, tc.shares, got, tc.want)
			}
		})
	}
}

// --- OrQuery construction ---------------------------------------------------

func TestOrQuery(t *testing.T) {
	tests := []struct {
		in   []string
		want string
	}{
		{[]string{"Connection Pooling"}, `"connection" OR "pooling"`},
		{[]string{"Connection Pooling", "Connection reuse"}, `"connection" OR "pooling" OR "reuse"`},
		{[]string{"", ""}, ""},
		{[]string{"  !!!  "}, ""},
	}
	for _, tc := range tests {
		if got := OrQuery(tc.in...); got != tc.want {
			t.Errorf("OrQuery(%v) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

// --- Check: integration against a real vault --------------------------------

func TestCheckFindsOwnTitle(t *testing.T) {
	idx := newFixtureSearcher(t)
	candidates, err := Check(idx, "Connection Pooling in Postgres", "", "", Options{})
	if err != nil {
		t.Fatalf("Check: %v", err)
	}
	if refOf(candidates, "wiki/concepts/connection-pooling.md") == nil {
		t.Errorf("own title did not surface the page: %v", candidates)
	}
}

func TestCheckSurvivesNoisyNewText(t *testing.T) {
	idx := newFixtureSearcher(t)
	candidates, err := Check(idx,
		"Connection Pooling in Postgres",
		"A totally unrelated sentence about zebras and volcanoes.",
		"", Options{})
	if err != nil {
		t.Fatalf("Check: %v", err)
	}
	if refOf(candidates, "wiki/concepts/connection-pooling.md") == nil {
		t.Errorf("unrelated summary suppressed a real title match: %v", candidates)
	}
}

func TestCheckBodyIsQueryScoresHighest(t *testing.T) {
	idx := newFixtureSearcher(t)
	candidates, err := Check(idx, "", "",
		"Connection pooling reduces per-request handshake overhead by "+
			"reusing a fixed set of open connections across callers.",
		Options{})
	if err != nil {
		t.Fatalf("Check: %v", err)
	}
	if len(candidates) == 0 {
		t.Fatal("no candidates for a verbatim body match")
	}
	if candidates[0].PageRef != "wiki/concepts/connection-pooling.md" {
		t.Errorf("first candidate = %q, want connection-pooling.md", candidates[0].PageRef)
	}
}

func TestCheckHintsOnRealHits(t *testing.T) {
	idx := newFixtureSearcher(t)
	candidates, err := Check(idx,
		"Connection Pooling in Postgres",
		"Reuse connections instead of opening a new one per request.",
		"",
		Options{DuplicateThreshold: 1e-06, RelatedThreshold: 1e-08})
	if err != nil {
		t.Fatalf("Check: %v", err)
	}
	top := refOf(candidates, "wiki/concepts/connection-pooling.md")
	if top == nil {
		t.Fatal("connection-pooling.md not among candidates")
	}
	if top.Hint != HintDuplicate {
		t.Errorf("hint = %q, want duplicate", top.Hint)
	}
}

func TestCheckLimitIsRespected(t *testing.T) {
	idx := newFixtureSearcher(t)
	candidates, err := Check(idx, "Connection Pooling", "", "", Options{Limit: 1})
	if err != nil {
		t.Fatalf("Check: %v", err)
	}
	if len(candidates) > 1 {
		t.Errorf("limit=1 returned %d candidates", len(candidates))
	}
}

func TestCheckReturnsFullPayload(t *testing.T) {
	idx := newFixtureSearcher(t)
	candidates, err := Check(idx, "Connection Pooling in Postgres", "", "", Options{})
	if err != nil {
		t.Fatalf("Check: %v", err)
	}
	top := refOf(candidates, "wiki/concepts/connection-pooling.md")
	if top == nil {
		t.Fatal("connection-pooling.md not among candidates")
	}
	if top.Summary != "Reuse connections instead of opening a new one per request." {
		t.Errorf("summary = %q", top.Summary)
	}
	if top.Tags == nil {
		t.Error("tags should be a non-nil list")
	}
	if top.SupersededBy != nil {
		t.Errorf("superseded_by = %v, want nil", *top.SupersededBy)
	}
}

// --- Discover: one call per draft plan --------------------------------------

func TestDiscoverRunsCheckForEveryPage(t *testing.T) {
	idx := newFixtureSearcher(t)
	summary := "Daily flour-and-water feeding keeps a starter active."
	pages := []ingest.PagePlan{
		{Op: "create", Title: "Connection Pooling in Postgres", Frontmatter: ingest.OrderedMap[any]{Keys: []string{"summary"}, Values: map[string]any{"summary": ""}}},
		{Op: "create", Title: "Feeding a Sourdough Starter", Frontmatter: ingest.OrderedMap[any]{Keys: []string{"summary"}, Values: map[string]any{"summary": summary}}},
	}
	results, err := Discover(idx, pages, Options{})
	if err != nil {
		t.Fatalf("Discover: %v", err)
	}
	got := []string{results[0].Title, results[1].Title}
	if want := []string{"Connection Pooling in Postgres", "Feeding a Sourdough Starter"}; !reflect.DeepEqual(got, want) {
		t.Errorf("titles = %v, want %v", got, want)
	}
	if refOf(results[0].Candidates, "wiki/concepts/connection-pooling.md") == nil {
		t.Errorf("first page's candidates missing connection-pooling.md: %v", results[0].Candidates)
	}
}

func TestDiscoverUpdatePageWithNoBodyDoesNotCrash(t *testing.T) {
	idx := newFixtureSearcher(t)
	pages := []ingest.PagePlan{
		{Op: "update", Title: "Connection Pooling in Postgres", PageRef: "wiki/concepts/connection-pooling.md"},
	}
	results, err := Discover(idx, pages, Options{})
	if err != nil {
		t.Fatalf("Discover: %v", err)
	}
	if len(results) != 1 {
		t.Errorf("len(results) = %d, want 1", len(results))
	}
}

// --- tag helpers ------------------------------------------------------------

func TestTagsContaining(t *testing.T) {
	vocab := []searchindex.TagCount{
		{Tag: "access-management", Count: 7},
		{Tag: "node-access", Count: 3},
		{Tag: "csm-ticket", Count: 2},
		{Tag: "sourdough", Count: 1},
	}
	tests := []struct {
		substrings []string
		want       []string
	}{
		{[]string{"access"}, []string{"access-management", "node-access"}},
		{[]string{"ACCESS"}, []string{"access-management", "node-access"}},
		{[]string{"access", "csm"}, []string{"access-management", "node-access", "csm-ticket"}},
		{[]string{"zzz"}, nil},
	}
	for _, tc := range tests {
		if got := TagsContaining(vocab, tc.substrings); !reflect.DeepEqual(got, tc.want) {
			t.Errorf("TagsContaining(%v) = %v, want %v", tc.substrings, got, tc.want)
		}
	}
}

func TestTagCounts(t *testing.T) {
	vocab := []searchindex.TagCount{{Tag: "access-management", Count: 7}}
	if got := TagCounts(vocab, []string{"access-management"}); !reflect.DeepEqual(got, []searchindex.TagCount{{Tag: "access-management", Count: 7}}) {
		t.Errorf("existing tag = %v", got)
	}
	if got := TagCounts(vocab, []string{"user-provisioning"}); !reflect.DeepEqual(got, []searchindex.TagCount{{Tag: "user-provisioning", Count: 0}}) {
		t.Errorf("missing tag = %v", got)
	}
	vocab2 := []searchindex.TagCount{{Tag: "a", Count: 1}, {Tag: "b", Count: 2}}
	if got := TagCounts(vocab2, []string{"b", "a"}); !reflect.DeepEqual(got, []searchindex.TagCount{{Tag: "b", Count: 2}, {Tag: "a", Count: 1}}) {
		t.Errorf("order = %v", got)
	}
}
