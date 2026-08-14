package pagerecord

import (
	"reflect"
	"testing"
)

const conceptPage = `---
title: Connection pooling
summary: Reusing database connections across requests.
tags:
  - database
  - performance
source_date: 2026-07-20
volatility: evolving
raw_source: "[notes.md](../../raw/notes%20%281%29.md)"
supersedes:
  - "[Old pooling](old-pooling.md)"
refines:
  - "[Databases](../entities/databases.md)"
---

Body text.
`

func TestNewDecodesTheFrontmatterSchema(t *testing.T) {
	rec, err := New("wiki/concepts/pooling.md", conceptPage)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if rec.Kind != "concept" {
		t.Errorf("Kind = %q, want concept", rec.Kind)
	}
	if rec.Title != "Connection pooling" {
		t.Errorf("Title = %q", rec.Title)
	}
	if rec.SourceDate != "2026-07-20" {
		t.Errorf("SourceDate = %q, want 2026-07-20", rec.SourceDate)
	}
	if rec.Volatility != "evolving" {
		t.Errorf("Volatility = %q", rec.Volatility)
	}
	if want := []string{"database", "performance"}; !reflect.DeepEqual(rec.Tags, want) {
		t.Errorf("Tags = %v, want %v", rec.Tags, want)
	}
}

func TestNewResolvesEdgeTargetsToVaultRelative(t *testing.T) {
	rec, err := New("wiki/concepts/pooling.md", conceptPage)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	want := []Edge{
		{Key: "raw_source", Targets: []string{"raw/notes (1).md"}},
		{Key: "supersedes", Targets: []string{"wiki/concepts/old-pooling.md"}},
		{Key: "refines", Targets: []string{"wiki/entities/databases.md"}},
	}
	if !reflect.DeepEqual(rec.Edges, want) {
		t.Fatalf("Edges = %#v, want %#v", rec.Edges, want)
	}
}

func TestNewQuotesDatesBackAsISOStrings(t *testing.T) {
	// An unquoted `source_date:` is a YAML timestamp, not a string. Spelling
	// it back as an ISO date is what keeps `--since`/`--until` comparable.
	rec, err := New("wiki/concepts/a.md", "---\nsource_date: 2026-01-02\n---\n")
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if rec.SourceDate != "2026-01-02" {
		t.Fatalf("SourceDate = %q, want 2026-01-02", rec.SourceDate)
	}
}

// A clock on `source_date` has no domain meaning (it is valid time — a date,
// not an instant), so it is truncated to its date on the way in. This is the
// normalise-on-read half of #192: it alone fixes a timestamp escaping
// `--since`/`--until` bounds in an existing vault, with no migration.
func TestNewTruncatesATimestampToItsDate(t *testing.T) {
	for _, scalar := range []string{
		"2026-07-20T14:30:00Z",
		"2026-07-20T14:30:00+05:00",
		`"2026-07-20T14:30:00Z"`,
		`"2026-07-20 10:30:00"`,
	} {
		rec, err := New("wiki/concepts/timed.md", "---\nsource_date: "+scalar+"\n---\n")
		if err != nil {
			t.Fatalf("New(%s): %v", scalar, err)
		}
		if rec.SourceDate != "2026-07-20" {
			t.Errorf("SourceDate for %s = %q, want 2026-07-20", scalar, rec.SourceDate)
		}
	}
}

// A value that isn't a date at all is stored verbatim on read — the read
// path tolerates legacy and hand-written values; ingest rejects them on
// write (#192).
func TestNewKeepsANonDateVerbatim(t *testing.T) {
	rec, err := New("wiki/concepts/a.md", "---\nsource_date: \"summer 2026\"\n---\n")
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if rec.SourceDate != "summer 2026" {
		t.Errorf("SourceDate = %q, want the verbatim spelling", rec.SourceDate)
	}
}

func TestNewDerivesCustomKindsPerADR0008(t *testing.T) {
	rec, err := New("wiki/decisions/use-fts5.md", "---\ntitle: Use FTS5\n---\n")
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if rec.Kind != "decision" {
		t.Errorf("Kind = %q, want decision", rec.Kind)
	}
}

func TestNewRejectsPagesAtTheWrongDepth(t *testing.T) {
	for _, ref := range []string{
		"wiki/loose.md",
		"wiki/concepts/nested/deep.md",
		"raw/notes.md",
	} {
		if _, err := New(ref, "---\ntitle: X\n---\n"); err == nil {
			t.Errorf("New(%q) succeeded, want a structural error", ref)
		}
	}
}

func TestNewAcceptsAPageWithNoFrontmatter(t *testing.T) {
	rec, err := New("wiki/concepts/bare.md", "Just a body.\n")
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if rec.Title != "" || len(rec.Edges) != 0 {
		t.Fatalf("expected an empty record, got %#v", rec)
	}
}

func TestNewRejectsAnEdgeThatIsNotAMarkdownLink(t *testing.T) {
	_, err := New("wiki/concepts/a.md", "---\nrelated:\n  - wiki/concepts/b.md\n---\n")
	if err == nil {
		t.Fatal("expected an error for a bare-path edge")
	}
}

func TestLoadRecordsInvertsSupersedes(t *testing.T) {
	pages := map[string]string{
		"wiki/concepts/new.md": "---\ntitle: New\nsupersedes:\n  - \"[Old](old.md)\"\n---\n",
		"wiki/concepts/old.md": "---\ntitle: Old\n---\n",
	}
	records, err := LoadRecords(pages)
	if err != nil {
		t.Fatalf("LoadRecords: %v", err)
	}
	if want := []string{"wiki/concepts/new.md"}; !reflect.DeepEqual(
		records["wiki/concepts/old.md"].SupersededBy, want) {
		t.Errorf("old.SupersededBy = %v, want %v",
			records["wiki/concepts/old.md"].SupersededBy, want)
	}
	if got := records["wiki/concepts/new.md"].SupersededBy; len(got) != 0 {
		t.Errorf("new.SupersededBy = %v, want empty", got)
	}
}
