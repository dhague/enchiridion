package vaultgit

import (
	"os"
	"path/filepath"
	"testing"
)

func TestIsWorkTree(t *testing.T) {
	root := t.TempDir()
	repo := New(root)
	if repo.IsWorkTree() {
		t.Fatal("a bare temp dir reported as a work tree")
	}
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	if !repo.IsWorkTree() {
		t.Fatal("an initialised repo did not report as a work tree")
	}
}

func TestAddAndCommit(t *testing.T) {
	root := t.TempDir()
	repo := New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	writePage(t, root, "wiki/concepts/a.md", "one\n")

	if err := repo.Add("wiki"); err != nil {
		t.Fatalf("Add: %v", err)
	}
	sha, err := repo.Commit("first")
	if err != nil {
		t.Fatalf("Commit: %v", err)
	}
	if len(sha) != 40 {
		t.Fatalf("Commit returned %q, want a 40-char SHA", sha)
	}
}

func TestCommitDatesCoversTheRootCommit(t *testing.T) {
	// `git log --name-only` shows the root commit's whole tree, so a vault's
	// scaffold commit must still date the pages it introduced.
	root := t.TempDir()
	repo := New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	writePage(t, root, "wiki/concepts/a.md", "one\n")
	writePage(t, root, "raw/notes.md", "raw\n")
	commitAll(t, repo, "first")

	dates := repo.CommitDates()
	if _, ok := dates["wiki/concepts/a.md"]; !ok {
		t.Fatalf("CommitDates = %v, want an entry for wiki/concepts/a.md", dates)
	}
	if _, ok := dates["raw/notes.md"]; ok {
		t.Errorf("CommitDates included a raw/ file: %v", dates)
	}
	if got := dates["wiki/concepts/a.md"]; len(got) != len("2026-01-02") {
		t.Errorf("date %q is not YYYY-MM-DD", got)
	}
}

func TestCommitDatesTracksLaterEdits(t *testing.T) {
	root := t.TempDir()
	repo := New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	writePage(t, root, "wiki/concepts/a.md", "one\n")
	writePage(t, root, "wiki/concepts/b.md", "b\n")
	commitAll(t, repo, "first")
	writePage(t, root, "wiki/concepts/a.md", "one, edited\n")
	commitAll(t, repo, "second")

	dates := repo.CommitDates()
	for _, ref := range []string{"wiki/concepts/a.md", "wiki/concepts/b.md"} {
		if _, ok := dates[ref]; !ok {
			t.Errorf("CommitDates = %v, missing %s", dates, ref)
		}
	}
}

func TestCommitDatesIsLenientOnANonRepo(t *testing.T) {
	// The lenient surface: "a missing date means git_date is null, never a
	// failure" — the search index reads that policy off this method.
	if dates := New(t.TempDir()).CommitDates(); len(dates) != 0 {
		t.Fatalf("CommitDates = %v, want an empty map", dates)
	}
}

func TestCommitDatesIsLenientOnAnEmptyRepo(t *testing.T) {
	root := t.TempDir()
	repo := New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	if dates := repo.CommitDates(); len(dates) != 0 {
		t.Fatalf("CommitDates = %v, want an empty map for a repo with no HEAD", dates)
	}
}

func writePage(t *testing.T, root, rel, content string) {
	t.Helper()
	path := filepath.Join(root, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func commitAll(t *testing.T, repo *Repo, message string) {
	t.Helper()
	if err := repo.Add("."); err != nil {
		t.Fatalf("Add: %v", err)
	}
	if _, err := repo.Commit(message); err != nil {
		t.Fatalf("Commit: %v", err)
	}
}
