package vault

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/dhague/enchiridion/enchiridion-go/internal/wikipage"
)

// writeVault lays down a {pageRef: text} map under a fresh temp root and
// returns a Vault over it.
func writeVault(t *testing.T, pages map[string]string) *Vault {
	t.Helper()
	root := t.TempDir()
	for ref, text := range pages {
		path := filepath.Join(root, filepath.FromSlash(ref))
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return New(root)
}

func TestLoadAndWriteRoundTrip(t *testing.T) {
	v := writeVault(t, nil)
	page := wikipage.Page{Text: "---\ntitle: A\n---\nbody\n"}
	if err := v.Write("wiki/concepts/a.md", page); err != nil {
		t.Fatalf("Write: %v", err)
	}
	got, err := v.Load("wiki/concepts/a.md")
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if got.Text != page.Text {
		t.Errorf("round trip = %q, want %q", got.Text, page.Text)
	}
	if !v.Exists("wiki/concepts/a.md") {
		t.Error("Exists = false for a page just written")
	}
	if v.Exists("wiki/concepts") {
		t.Error("Exists = true for a directory; it should name a file")
	}
}

// Exists answers "is there a page here"; Occupied answers "may a create
// claim this path". A directory splits them.
func TestExistsVersusOccupiedOnADirectory(t *testing.T) {
	v := writeVault(t, map[string]string{"wiki/concepts/a.md/nested.md": "x\n"})
	if v.Exists("wiki/concepts/a.md") {
		t.Error("Exists = true for a directory")
	}
	if !v.Occupied("wiki/concepts/a.md") {
		t.Error("Occupied = false for a directory; a create must not claim it")
	}
	if v.Occupied("wiki/concepts/absent.md") {
		t.Error("Occupied = true for a path with nothing at it")
	}
}

func TestLegacyKindFolders(t *testing.T) {
	v := writeVault(t, map[string]string{
		"wiki/concept/old.md":  "x\n", // pre-ADR-0008 singular
		"wiki/source/old.md":   "x\n", // pre-ADR-0008 singular
		"wiki/concepts/new.md": "x\n", // canonical
		"wiki/synthesis/s.md":  "x\n", // its own plural — never legacy
		"wiki/decisions/d.md":  "x\n", // a custom kind-folder — not legacy
	})
	legacy, err := v.LegacyKindFolders()
	if err != nil {
		t.Fatalf("LegacyKindFolders: %v", err)
	}
	if strings.Join(legacy, ",") != "concept,source" {
		t.Errorf("LegacyKindFolders = %v, want [concept source]", legacy)
	}
}

func TestLegacyKindFoldersOnAMigratedVault(t *testing.T) {
	v := writeVault(t, map[string]string{"wiki/concepts/a.md": "x\n"})
	legacy, err := v.LegacyKindFolders()
	if err != nil || len(legacy) != 0 {
		t.Errorf("LegacyKindFolders = %v, %v; want none, nil", legacy, err)
	}
}

func TestLoadWikiPagesNeverWalksRaw(t *testing.T) {
	v := writeVault(t, map[string]string{
		"wiki/concepts/a.md": "a\n",
		"wiki/sources/s.md":  "s\n",
		"raw/doc.md":         "raw\n",
	})
	pages, err := v.LoadWikiPages()
	if err != nil {
		t.Fatalf("LoadWikiPages: %v", err)
	}
	if len(pages) != 2 {
		t.Errorf("LoadWikiPages returned %d pages, want 2: %v", len(pages), pages)
	}
	if _, ok := pages["raw/doc.md"]; ok {
		t.Error("LoadWikiPages walked raw/")
	}
}

func TestDiscoveredKindsSkipsCanonicalFolders(t *testing.T) {
	v := writeVault(t, map[string]string{
		"wiki/concepts/a.md":  "a\n",
		"wiki/decisions/d.md": "d\n",
		"wiki/people/p.md":    "p\n",
	})
	kinds, err := v.DiscoveredKinds()
	if err != nil {
		t.Fatalf("DiscoveredKinds: %v", err)
	}
	want := map[string]string{"decision": "decisions", "people": "people"}
	if len(kinds) != len(want) {
		t.Fatalf("DiscoveredKinds = %v, want %v", kinds, want)
	}
	for kind, folder := range want {
		if kinds[kind] != folder {
			t.Errorf("DiscoveredKinds[%q] = %q, want %q", kind, kinds[kind], folder)
		}
	}
}

func TestDiscoveredKindsOnVaultWithoutWikiDir(t *testing.T) {
	kinds, err := New(t.TempDir()).DiscoveredKinds()
	if err != nil || len(kinds) != 0 {
		t.Errorf("DiscoveredKinds on a bare directory = %v, %v; want empty, nil", kinds, err)
	}
}

func TestSetAndMergeWriteBack(t *testing.T) {
	v := writeVault(t, map[string]string{
		"wiki/concepts/a.md": "---\ntitle: A\ntags:\n  - x\n---\nbody\n",
	})
	if _, err := v.Set("wiki/concepts/a.md", "volatility", "stable"); err != nil {
		t.Fatalf("Set: %v", err)
	}
	if _, err := v.Merge("wiki/concepts/a.md", "tags", []any{"x", "y"}); err != nil {
		t.Fatalf("Merge: %v", err)
	}
	page, err := v.Load("wiki/concepts/a.md")
	if err != nil {
		t.Fatal(err)
	}
	volatility, err := page.GetString("volatility")
	if err != nil || volatility != "stable" {
		t.Errorf("volatility = %q, %v; want \"stable\", nil", volatility, err)
	}
	tags, err := page.GetStringList("tags")
	if err != nil || strings.Join(tags, ",") != "x,y" {
		t.Errorf("tags = %v, %v; want [x y], nil", tags, err)
	}
}

func TestMovePageFixesLinksAndRemovesOriginal(t *testing.T) {
	v := writeVault(t, map[string]string{
		"wiki/concepts/a.md": "---\nrelated:\n  - \"[B](b.md)\"\n---\nSee [B](b.md).\n",
		"wiki/concepts/b.md": "Back to [A](a.md).\n",
		"wiki/sources/s.md":  "Nothing to fix here.\n",
	})

	changed, err := v.MovePage("wiki/concepts/b.md", "wiki/entities/b.md")
	if err != nil {
		t.Fatalf("MovePage: %v", err)
	}
	want := []string{"wiki/concepts/a.md", "wiki/entities/b.md"}
	if strings.Join(changed, ",") != strings.Join(want, ",") {
		t.Errorf("MovePage = %v, want %v (sorted, untouched pages excluded)", changed, want)
	}
	if v.Exists("wiki/concepts/b.md") {
		t.Error("the original page was not removed")
	}
	moved, err := v.Load("wiki/entities/b.md")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(moved.Text, "(../concepts/a.md)") {
		t.Errorf("moved page's outbound link not rebased: %q", moved.Text)
	}
	inbound, err := v.Load("wiki/concepts/a.md")
	if err != nil {
		t.Fatal(err)
	}
	if strings.Count(inbound.Text, "(../entities/b.md)") != 2 {
		t.Errorf("both the frontmatter edge and the body link should be retargeted: %q", inbound.Text)
	}
}

func TestMovePageMissingSource(t *testing.T) {
	v := writeVault(t, map[string]string{"wiki/concepts/a.md": "a\n"})
	if _, err := v.MovePage("wiki/concepts/missing.md", "wiki/entities/missing.md"); err == nil {
		t.Error("MovePage of a page that doesn't exist: want an error, got nil")
	}
}

func TestMovePageOntoItselfChangesNothing(t *testing.T) {
	v := writeVault(t, map[string]string{"wiki/concepts/a.md": "See [self](a.md).\n"})
	changed, err := v.MovePage("wiki/concepts/a.md", "wiki/concepts/a.md")
	if err != nil {
		t.Fatalf("MovePage: %v", err)
	}
	if len(changed) != 0 {
		t.Errorf("MovePage onto itself = %v, want no changes", changed)
	}
	if !v.Exists("wiki/concepts/a.md") {
		t.Error("MovePage onto itself deleted the page")
	}
}

// A raw/ artifact renamed outside the plugin is never read or written — only
// the wiki pages pointing at it are fixed.
func TestRewriteInboundLinksForNonPageTarget(t *testing.T) {
	v := writeVault(t, map[string]string{
		"wiki/sources/s.md":  "---\nraw_source: \"[old.md](../../raw/old.md)\"\n---\nstub\n",
		"wiki/concepts/a.md": "Unrelated.\n",
	})
	changed, err := v.RewriteInboundLinks("raw/old.md", "raw/new.md")
	if err != nil {
		t.Fatalf("RewriteInboundLinks: %v", err)
	}
	if strings.Join(changed, ",") != "wiki/sources/s.md" {
		t.Errorf("RewriteInboundLinks = %v, want [wiki/sources/s.md]", changed)
	}
	stub, err := v.Load("wiki/sources/s.md")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(stub.Text, "(../../raw/new.md)") {
		t.Errorf("raw_source not retargeted: %q", stub.Text)
	}
	if v.Exists("raw/new.md") || v.Exists("raw/old.md") {
		t.Error("RewriteInboundLinks touched the raw artifact itself")
	}
}

func TestPagesDecodesRecords(t *testing.T) {
	v := writeVault(t, map[string]string{
		"wiki/concepts/a.md": "---\ntitle: A\ntags:\n  - x\n---\nbody\n",
		"wiki/sources/s.md":  "---\ntitle: S\nsupersedes:\n  - \"[A](../concepts/a.md)\"\n---\nstub\n",
	})
	pages, err := v.Pages()
	if err != nil {
		t.Fatalf("Pages: %v", err)
	}
	if got := pages["wiki/concepts/a.md"]; got.Title != "A" || got.Kind != "concept" {
		t.Errorf("record = %+v, want title A kind concept", got)
	}
	if got := pages["wiki/concepts/a.md"].SupersededBy; len(got) != 1 || got[0] != "wiki/sources/s.md" {
		t.Errorf("SupersededBy = %v, want [wiki/sources/s.md]", got)
	}
}
