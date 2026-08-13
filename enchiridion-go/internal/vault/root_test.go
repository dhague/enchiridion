package vault

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

// env builds a lookupEnv function over a fixed map, so root resolution is
// testable without touching the real process environment.
func env(pairs map[string]string) func(string) (string, bool) {
	return func(key string) (string, bool) {
		value, ok := pairs[key]
		return value, ok
	}
}

func TestResolveRootPrefersWikiRootEnv(t *testing.T) {
	// $WIKI_ROOT wins even when `start` is itself a marked vault — the
	// query-from-anywhere mode depends on it (ADR-0004).
	elsewhere := t.TempDir()
	start := t.TempDir()
	mustMkdir(t, filepath.Join(start, "wiki"))

	got, err := ResolveRoot(start, env(map[string]string{"WIKI_ROOT": elsewhere}))
	if err != nil {
		t.Fatalf("ResolveRoot: %v", err)
	}
	if got != mustResolve(t, elsewhere) {
		t.Fatalf("ResolveRoot = %q, want %q", got, elsewhere)
	}
}

func TestResolveRootIgnoresAnEmptyWikiRoot(t *testing.T) {
	start := t.TempDir()
	mustMkdir(t, filepath.Join(start, "wiki"))

	got, err := ResolveRoot(start, env(map[string]string{"WIKI_ROOT": ""}))
	if err != nil {
		t.Fatalf("ResolveRoot: %v", err)
	}
	if got != mustResolve(t, start) {
		t.Fatalf("ResolveRoot = %q, want %q", got, start)
	}
}

func TestResolveRootWalksUpToTheNearestMarker(t *testing.T) {
	for _, marker := range Markers {
		t.Run(marker, func(t *testing.T) {
			root := t.TempDir()
			if marker == "wiki" {
				mustMkdir(t, filepath.Join(root, marker))
			} else {
				mustWrite(t, filepath.Join(root, marker), "")
			}
			deep := filepath.Join(root, "a", "b", "c")
			mustMkdir(t, deep)

			got, err := ResolveRoot(deep, env(nil))
			if err != nil {
				t.Fatalf("ResolveRoot: %v", err)
			}
			if got != mustResolve(t, root) {
				t.Fatalf("ResolveRoot = %q, want %q", got, root)
			}
		})
	}
}

func TestResolveRootFallsBackToStart(t *testing.T) {
	// The dedicated-mode default: no marker anywhere above, so `start` is
	// the vault root. This is also why every script invocation against the
	// dogfooding vault sets WIKI_ROOT explicitly.
	start := t.TempDir()
	got, err := ResolveRoot(start, env(nil))
	if err != nil {
		t.Fatalf("ResolveRoot: %v", err)
	}
	if got != mustResolve(t, start) {
		t.Fatalf("ResolveRoot = %q, want %q", got, start)
	}
}

func TestPageRefsWalksOnlyWikiMarkdown(t *testing.T) {
	root := t.TempDir()
	mustMkdir(t, filepath.Join(root, "wiki", "concepts"))
	mustMkdir(t, filepath.Join(root, "raw"))
	mustWrite(t, filepath.Join(root, "wiki", "concepts", "a.md"), "")
	mustWrite(t, filepath.Join(root, "wiki", "concepts", ".gitkeep"), "")
	mustWrite(t, filepath.Join(root, "wiki", "concepts", "notes.txt"), "")
	mustWrite(t, filepath.Join(root, "raw", "should-not-appear.md"), "")

	refs, err := PageRefs(root)
	if err != nil {
		t.Fatalf("PageRefs: %v", err)
	}
	if want := []string{"wiki/concepts/a.md"}; !reflect.DeepEqual(refs, want) {
		t.Fatalf("PageRefs = %v, want %v", refs, want)
	}
}

func TestPageRefsOnAVaultWithNoWikiDir(t *testing.T) {
	refs, err := PageRefs(t.TempDir())
	if err != nil {
		t.Fatalf("PageRefs: %v", err)
	}
	if len(refs) != 0 {
		t.Fatalf("PageRefs = %v, want empty", refs)
	}
}

func mustMkdir(t *testing.T, path string) {
	t.Helper()
	if err := os.MkdirAll(path, 0o755); err != nil {
		t.Fatal(err)
	}
}

func mustWrite(t *testing.T, path, content string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func mustResolve(t *testing.T, path string) string {
	t.Helper()
	resolved, err := resolve(path)
	if err != nil {
		t.Fatal(err)
	}
	return resolved
}
