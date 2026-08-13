package place

import (
	"strings"
	"testing"

	"pgregory.net/rapid"
)

func TestSlugify(t *testing.T) {
	tests := []struct{ title, want string }{
		{"Connection Pooling", "connection-pooling"},
		{"What's in a name?", "whats-in-a-name"},
		{"What’s in a name?", "whats-in-a-name"}, // curly apostrophe too
		{"  Leading & trailing  ", "leading-trailing"},
		{"CamelCase/slash_underscore", "camelcase-slash-underscore"},
		{"Ünïcödé título", "n-c-d-t-tulo"},
		{"---", ""},
	}
	for _, tc := range tests {
		if got := Slugify(tc.title, 0); got != tc.want {
			t.Errorf("Slugify(%q) = %q, want %q", tc.title, got, tc.want)
		}
	}
}

func TestSlugifyTruncatesAtAHyphenBoundary(t *testing.T) {
	title := "the quick brown fox jumps over the lazy dog and keeps on running forever"
	got := Slugify(title, MaxSlugLength)
	if len(got) > MaxSlugLength {
		t.Fatalf("slug %q is %d chars, over the %d cap", got, len(got), MaxSlugLength)
	}
	if want := "the-quick-brown-fox-jumps-over-the-lazy-dog-and-keeps-on"; got != want {
		t.Errorf("Slugify = %q, want %q", got, want)
	}
}

func TestSlugifyHardCutsWhenNoUsableHyphenBoundary(t *testing.T) {
	// No hyphen early enough to leave minWordCut chars, so a hard cut.
	got := Slugify(strings.Repeat("a", 100), 20)
	if got != strings.Repeat("a", 20) {
		t.Fatalf("Slugify = %q, want 20 a's", got)
	}
}

func TestSlugifyPropertiesHold(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		title := rapid.String().Draw(t, "title")
		slug := Slugify(title, MaxSlugLength)
		if len(slug) > MaxSlugLength {
			t.Fatalf("slug %q exceeds the cap", slug)
		}
		if strings.HasPrefix(slug, "-") || strings.HasSuffix(slug, "-") {
			t.Fatalf("slug %q has a bare hyphen end", slug)
		}
		if strings.Contains(slug, "--") {
			t.Fatalf("slug %q has a doubled hyphen", slug)
		}
		for _, r := range slug {
			if !(r >= 'a' && r <= 'z' || r >= '0' && r <= '9' || r == '-') {
				t.Fatalf("slug %q contains %q, outside the kebab charset", slug, r)
			}
		}
	})
}

func TestFolderToKindSingularizesPerADR0008(t *testing.T) {
	tests := []struct{ folder, want string }{
		{"decisions", "decision"},
		{"people", "people"}, // no trailing s: used verbatim
		{"synthesis", "synthesi"},
	}
	for _, tc := range tests {
		if got := FolderToKind(tc.folder); got != tc.want {
			t.Errorf("FolderToKind(%q) = %q, want %q", tc.folder, got, tc.want)
		}
	}
}

func TestFolderKindsInvertsKindFolders(t *testing.T) {
	for kind, folder := range KindFolders {
		if got := FolderKinds[folder]; got != kind {
			t.Errorf("FolderKinds[%q] = %q, want %q", folder, got, kind)
		}
	}
	// `synthesis` is the ADR-0008 exception: no distinct plural, so the
	// canonical lookup must win over the singularization rule.
	if got := FolderKinds["synthesis"]; got != "synthesis" {
		t.Errorf("FolderKinds[synthesis] = %q, want synthesis", got)
	}
}

func TestPath(t *testing.T) {
	got, err := Path("concept", "Connection Pooling", nil)
	if err != nil {
		t.Fatalf("Path: %v", err)
	}
	if want := "wiki/concepts/connection-pooling.md"; got != want {
		t.Errorf("Path = %q, want %q", got, want)
	}
}

func TestPathAcceptsDiscoveredKinds(t *testing.T) {
	got, err := Path("decision", "Use FTS5", map[string]string{"decision": "decisions"})
	if err != nil {
		t.Fatalf("Path: %v", err)
	}
	if want := "wiki/decisions/use-fts5.md"; got != want {
		t.Errorf("Path = %q, want %q", got, want)
	}
}

func TestPathRejectsUnknownKind(t *testing.T) {
	if _, err := Path("nonsense", "X", nil); err == nil {
		t.Fatal("expected an error for an unknown kind")
	}
}
