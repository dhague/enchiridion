package chainofevidence

import (
	"strings"
	"testing"

	"github.com/dhague/enchiridion/enchiridion-go/internal/wikipage"
)

func page(frontmatter string) wikipage.Page {
	return wikipage.Page{Text: "---\n" + frontmatter + "---\nbody\n"}
}

const rawRef = "raw/doc.md"

func TestChainHolds(t *testing.T) {
	staged := map[string]wikipage.Page{
		"wiki/sources/doc.md": page("raw_source: \"[doc.md](../../raw/doc.md)\"\n"),
		"wiki/concepts/a.md":  page("source:\n  - \"[doc.md](../sources/doc.md)\"\n"),
	}
	errs, err := Check(staged, rawRef)
	if err != nil {
		t.Fatalf("Check: %v", err)
	}
	if len(errs) != 0 {
		t.Errorf("Check = %v, want no errors", errs)
	}
}

func TestMissingStubIsReported(t *testing.T) {
	staged := map[string]wikipage.Page{
		"wiki/concepts/a.md": page("title: A\n"),
	}
	errs, err := Check(staged, rawRef)
	if err != nil {
		t.Fatalf("Check: %v", err)
	}
	if len(errs) != 1 || !strings.Contains(errs[0], "needs a sources/ page") {
		t.Errorf("Check = %v, want one missing-stub error", errs)
	}
}

// A `sources/` page whose raw_source points at a *different* raw file is not
// this ingestion's stub.
func TestStubPointingElsewhereDoesNotCount(t *testing.T) {
	staged := map[string]wikipage.Page{
		"wiki/sources/other.md": page("raw_source: \"[other.md](../../raw/other.md)\"\n"),
	}
	errs, err := Check(staged, rawRef)
	if err != nil {
		t.Fatalf("Check: %v", err)
	}
	if len(errs) != 1 || !strings.Contains(errs[0], "needs a sources/ page") {
		t.Errorf("Check = %v, want one missing-stub error", errs)
	}
}

func TestPageWithoutSourceEdgeIsReported(t *testing.T) {
	staged := map[string]wikipage.Page{
		"wiki/sources/doc.md": page("raw_source: \"[doc.md](../../raw/doc.md)\"\n"),
		"wiki/concepts/a.md":  page("title: A\n"),
		"wiki/concepts/b.md":  page("source:\n  - \"[doc.md](../sources/doc.md)\"\n"),
	}
	errs, err := Check(staged, rawRef)
	if err != nil {
		t.Fatalf("Check: %v", err)
	}
	if len(errs) != 1 || !strings.Contains(errs[0], "wiki/concepts/a.md needs a source edge") {
		t.Errorf("Check = %v, want exactly the a.md error", errs)
	}
}

// The stub is found by resolving the link, not by string-matching it, so an
// unnormalized raw ref and a percent-encoded destination both land on the
// same file.
func TestRawRefIsNormalizedAndDestinationDecoded(t *testing.T) {
	staged := map[string]wikipage.Page{
		"wiki/sources/doc.md": page("raw_source: \"[a doc.md](../../raw/notes/../a%20doc.md)\"\n"),
	}
	errs, err := Check(staged, "raw/./a doc.md")
	if err != nil {
		t.Fatalf("Check: %v", err)
	}
	if len(errs) != 0 {
		t.Errorf("Check = %v, want no errors", errs)
	}
}

func TestErrorsAreDeterministicallyOrdered(t *testing.T) {
	staged := map[string]wikipage.Page{
		"wiki/sources/doc.md": page("raw_source: \"[doc.md](../../raw/doc.md)\"\n"),
		"wiki/concepts/c.md":  page("title: C\n"),
		"wiki/concepts/a.md":  page("title: A\n"),
		"wiki/concepts/b.md":  page("title: B\n"),
	}
	for range 5 {
		errs, err := Check(staged, rawRef)
		if err != nil {
			t.Fatalf("Check: %v", err)
		}
		got := strings.Join(errs, "|")
		if !strings.HasPrefix(got, "wiki/concepts/a.md") ||
			!strings.Contains(got, "|wiki/concepts/b.md") ||
			!strings.Contains(got, "|wiki/concepts/c.md") {
			t.Fatalf("errors not in sorted page order: %v", errs)
		}
	}
}

func TestInvalidFrontmatterIsAnError(t *testing.T) {
	staged := map[string]wikipage.Page{
		"wiki/sources/doc.md": {Text: "---\nraw_source: [unclosed\n---\nbody\n"},
	}
	if _, err := Check(staged, rawRef); err == nil {
		t.Error("Check on unparseable frontmatter: want an error, got nil")
	}
}
