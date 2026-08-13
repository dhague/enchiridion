package ingestscan

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/dhague/enchiridion/enchiridion-go/internal/vaultgit"
)

// fakeGit scripts the lenient git facts the sweep reads, mirroring
// wiki-plugin/tests/fake_vault_git.py's last_commit_dates/dirty state.
type fakeGit struct {
	lastCommitDates map[string]string
	dirty           map[string]bool
}

func (f *fakeGit) LastCommitDate(rel string) string  { return f.lastCommitDates[rel] }
func (f *fakeGit) PorcelainMentions(rel string) bool { return f.dirty[rel] }

func write(t *testing.T, root, rel, content string) {
	t.Helper()
	path := filepath.Join(root, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func seedVault(t *testing.T, root string) {
	t.Helper()
	for _, dir := range []string{"wiki/concepts", "wiki/entities", "wiki/sources", "wiki/synthesis", "raw"} {
		if err := os.MkdirAll(filepath.Join(root, filepath.FromSlash(dir)), 0o755); err != nil {
			t.Fatal(err)
		}
	}
}

// --- WalkRaw ---------------------------------------------------------------

func TestWalkRawYieldsEveryFileRecursively(t *testing.T) {
	root := t.TempDir()
	seedVault(t, root)
	write(t, root, "raw/a.md", "a")
	write(t, root, "raw/notes/b.md", "b")
	got, err := WalkRaw(root, "")
	if err != nil {
		t.Fatalf("WalkRaw: %v", err)
	}
	if want := []string{"raw/a.md", "raw/notes/b.md"}; !reflect.DeepEqual(got, want) {
		t.Errorf("WalkRaw = %v, want %v", got, want)
	}
}

func TestWalkRawSkipsInstructionsAndPolicy(t *testing.T) {
	root := t.TempDir()
	seedVault(t, root)
	write(t, root, "raw/INGESTION.md", "hints")
	write(t, root, "raw/.ingestignore", "*.tmp\n")
	write(t, root, "raw/real.md", "r")
	got, err := WalkRaw(root, "")
	if err != nil {
		t.Fatalf("WalkRaw: %v", err)
	}
	if want := []string{"raw/real.md"}; !reflect.DeepEqual(got, want) {
		t.Errorf("WalkRaw = %v, want %v", got, want)
	}
}

func TestWalkRawScopedToOneFolder(t *testing.T) {
	root := t.TempDir()
	seedVault(t, root)
	write(t, root, "raw/a.md", "a")
	write(t, root, "raw/notes/b.md", "b")
	got, err := WalkRaw(root, "notes")
	if err != nil {
		t.Fatalf("WalkRaw: %v", err)
	}
	if want := []string{"raw/notes/b.md"}; !reflect.DeepEqual(got, want) {
		t.Errorf("WalkRaw = %v, want %v", got, want)
	}
}

func TestWalkRawMissingFolderYieldsNothing(t *testing.T) {
	root := t.TempDir()
	seedVault(t, root)
	got, err := WalkRaw(root, "nope")
	if err != nil {
		t.Fatalf("WalkRaw: %v", err)
	}
	if len(got) != 0 {
		t.Errorf("WalkRaw = %v, want empty", got)
	}
}

// --- Scan: ignored ----------------------------------------------------------

func TestScanFileMatchingItsOwnFoldersIngestignoreIsIgnored(t *testing.T) {
	root := t.TempDir()
	seedVault(t, root)
	write(t, root, "raw/.ingestignore", "*.tmp\n")
	write(t, root, "raw/foo.tmp", "junk")
	write(t, root, "raw/real.md", "real")

	result, err := Scan(root, "", nil)
	if err != nil {
		t.Fatalf("Scan: %v", err)
	}
	if want := []string{"raw/real.md"}; !reflect.DeepEqual(rawRels(result.Eligible), want) {
		t.Errorf("eligible = %v, want %v", rawRels(result.Eligible), want)
	}
	if want := []string{"raw/foo.tmp"}; !reflect.DeepEqual(result.Ignored, want) {
		t.Errorf("ignored = %v, want %v", result.Ignored, want)
	}
}

func TestScanParentsIngestignoreDoesNotApply(t *testing.T) {
	root := t.TempDir()
	seedVault(t, root)
	write(t, root, "raw/.ingestignore", "*.tmp\n")
	write(t, root, "raw/notes/foo.tmp", "junk")

	result, err := Scan(root, "", nil)
	if err != nil {
		t.Fatalf("Scan: %v", err)
	}
	if want := []string{"raw/notes/foo.tmp"}; !reflect.DeepEqual(rawRels(result.Eligible), want) {
		t.Errorf("eligible = %v, want %v", rawRels(result.Eligible), want)
	}
	if len(result.Ignored) != 0 {
		t.Errorf("ignored = %v, want empty", result.Ignored)
	}
}

func TestScanOwnFoldersIngestignoreOverridesBackPointers(t *testing.T) {
	root := t.TempDir()
	seedVault(t, root)
	write(t, root, "wiki/sources/foo.md", "---\ntitle: Foo\nraw_source: \"[foo.md](../../raw/foo.md)\"\n---\n# Foo\n")
	write(t, root, "raw/.ingestignore", "foo.md\n")
	write(t, root, "raw/foo.md", "raw")

	result, err := Scan(root, "", nil)
	if err != nil {
		t.Fatalf("Scan: %v", err)
	}
	if len(result.Eligible) != 0 {
		t.Errorf("eligible = %v, want empty", result.Eligible)
	}
	if want := []string{"raw/foo.md"}; !reflect.DeepEqual(result.Ignored, want) {
		t.Errorf("ignored = %v, want %v", result.Ignored, want)
	}
}

// --- Scan: eligibility -------------------------------------------------------

func TestScanFileWithNoBackPointerIsNeverIngested(t *testing.T) {
	root := t.TempDir()
	seedVault(t, root)
	write(t, root, "raw/foo.md", "raw")

	result, err := Scan(root, "", nil)
	if err != nil {
		t.Fatalf("Scan: %v", err)
	}
	if len(result.Eligible) != 1 {
		t.Fatalf("eligible = %v, want 1 candidate", result.Eligible)
	}
	cand := result.Eligible[0]
	if cand.RawRel != "raw/foo.md" || cand.Reason != ReasonNeverIngested || len(cand.BackPointers) != 0 {
		t.Errorf("candidate = %+v", cand)
	}
}

func TestScanFileWithBackPointerButNoGitIsStillOffered(t *testing.T) {
	root := t.TempDir()
	seedVault(t, root)
	write(t, root, "wiki/sources/foo.md", "---\ntitle: Foo\nraw_source: \"[foo.md](../../raw/foo.md)\"\n---\n# Foo\n")
	write(t, root, "raw/foo.md", "raw")

	// No git here: the real repo's lenient surface returns ""/false, and the
	// absent page date fails toward offering.
	result, err := Scan(root, "", nil)
	if err != nil {
		t.Fatalf("Scan: %v", err)
	}
	if len(result.Eligible) != 1 {
		t.Fatalf("eligible = %v, want 1 candidate", result.Eligible)
	}
	cand := result.Eligible[0]
	if cand.Reason != ReasonChangedSinceIngestion {
		t.Errorf("reason = %q, want %q", cand.Reason, ReasonChangedSinceIngestion)
	}
	if want := []string{"wiki/sources/foo.md"}; !reflect.DeepEqual(cand.BackPointers, want) {
		t.Errorf("back_pointers = %v, want %v", cand.BackPointers, want)
	}
}

func TestScanPercentEncodedRawSourceStillMatches(t *testing.T) {
	root := t.TempDir()
	seedVault(t, root)
	write(t, root, "wiki/sources/my-notes.md",
		"---\ntitle: My Notes\n"+
			"raw_source: \"[My Notes (draft).md](../../raw/My%20Notes%20%28draft%29.md)\"\n"+
			"---\n# My Notes\n")
	write(t, root, "raw/My Notes (draft).md", "raw")

	result, err := Scan(root, "", nil)
	if err != nil {
		t.Fatalf("Scan: %v", err)
	}
	if len(result.Eligible) != 1 {
		t.Fatalf("eligible = %v, want 1 candidate", result.Eligible)
	}
	cand := result.Eligible[0]
	if cand.RawRel != "raw/My Notes (draft).md" {
		t.Errorf("raw_rel = %q", cand.RawRel)
	}
	if cand.Reason != ReasonChangedSinceIngestion {
		t.Errorf("reason = %q", cand.Reason)
	}
	if want := []string{"wiki/sources/my-notes.md"}; !reflect.DeepEqual(cand.BackPointers, want) {
		t.Errorf("back_pointers = %v, want %v", cand.BackPointers, want)
	}
}

// --- Scan: git-backed eligibility -------------------------------------------

func TestScanStrictlyNewerFakeGitFactsOfferTheFile(t *testing.T) {
	root := t.TempDir()
	seedVault(t, root)
	write(t, root, "wiki/sources/foo.md", "---\ntitle: Foo\nraw_source: \"[foo.md](../../raw/foo.md)\"\n---\n# Foo\n")
	write(t, root, "raw/foo.md", "raw")
	git := &fakeGit{
		lastCommitDates: map[string]string{"raw/foo.md": "2026-02-01", "wiki/sources/foo.md": "2026-01-01"},
	}

	result, err := Scan(root, "", git)
	if err != nil {
		t.Fatalf("Scan: %v", err)
	}
	if len(result.Eligible) != 1 {
		t.Fatalf("eligible = %v", result.Eligible)
	}
	cand := result.Eligible[0]
	if cand.Reason != ReasonChangedSinceIngestion || cand.RawRel != "raw/foo.md" {
		t.Errorf("candidate = %+v", cand)
	}
}

func TestScanDirtyFakeOverridesEqualDates(t *testing.T) {
	root := t.TempDir()
	seedVault(t, root)
	write(t, root, "wiki/sources/foo.md", "---\ntitle: Foo\nraw_source: \"[foo.md](../../raw/foo.md)\"\n---\n# Foo\n")
	write(t, root, "raw/foo.md", "raw")
	git := &fakeGit{
		dirty:           map[string]bool{"raw/foo.md": true},
		lastCommitDates: map[string]string{"raw/foo.md": "2026-01-01", "wiki/sources/foo.md": "2026-01-01"},
	}

	result, err := Scan(root, "", git)
	if err != nil {
		t.Fatalf("Scan: %v", err)
	}
	if len(result.Eligible) != 1 || result.Eligible[0].Reason != ReasonChangedSinceIngestion {
		t.Errorf("eligible = %v", result.Eligible)
	}
}

func TestScanSameCommitMeansNotOffered(t *testing.T) {
	root := t.TempDir()
	seedVault(t, root)
	repo := vaultgit.New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	write(t, root, "raw/notes.md", "raw notes")
	write(t, root, "wiki/sources/notes.md", "---\ntitle: Notes\nraw_source: \"[notes.md](../../raw/notes.md)\"\n---\n# Notes\n")
	if err := repo.Add("."); err != nil {
		t.Fatalf("Add: %v", err)
	}
	if _, err := repo.Commit("ingest notes"); err != nil {
		t.Fatalf("Commit: %v", err)
	}

	result, err := Scan(root, "", nil)
	if err != nil {
		t.Fatalf("Scan: %v", err)
	}
	if len(result.Eligible) != 0 || len(result.Ignored) != 0 {
		t.Errorf("same-commit file was offered: eligible=%v ignored=%v", result.Eligible, result.Ignored)
	}
}

func TestScanDirtyWorkingTreeOverridesDateEquality(t *testing.T) {
	root := t.TempDir()
	seedVault(t, root)
	repo := vaultgit.New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	write(t, root, "raw/notes.md", "raw notes")
	write(t, root, "wiki/sources/notes.md", "---\ntitle: Notes\nraw_source: \"[notes.md](../../raw/notes.md)\"\n---\n# Notes\n")
	if err := repo.Add("."); err != nil {
		t.Fatalf("Add: %v", err)
	}
	if _, err := repo.Commit("ingest notes"); err != nil {
		t.Fatalf("Commit: %v", err)
	}
	// Edit the raw file but DON'T commit; dirty status flips the offer.
	write(t, root, "raw/notes.md", "raw notes v2 (uncommitted)")

	result, err := Scan(root, "", nil)
	if err != nil {
		t.Fatalf("Scan: %v", err)
	}
	if len(result.Eligible) != 1 || result.Eligible[0].Reason != ReasonChangedSinceIngestion {
		t.Errorf("eligible = %v", result.Eligible)
	}
}

// --- Scan: shape -------------------------------------------------------------

func TestScanMalformedIngestignoreIsAnError(t *testing.T) {
	root := t.TempDir()
	seedVault(t, root)
	write(t, root, "raw/.ingestignore", "sub/*.tmp\n") // a '/' is rejected
	write(t, root, "raw/foo.md", "raw")

	if _, err := Scan(root, "", nil); err == nil {
		t.Fatal("malformed .ingestignore: want an error, got nil")
	}
}

func TestScanEmptyVaultYieldsEmptyResults(t *testing.T) {
	root := t.TempDir()
	seedVault(t, root)
	result, err := Scan(root, "", nil)
	if err != nil {
		t.Fatalf("Scan: %v", err)
	}
	if len(result.Eligible) != 0 || len(result.Ignored) != 0 {
		t.Errorf("result = %+v, want empty", result)
	}
}

func TestScanScopedToOneFolder(t *testing.T) {
	root := t.TempDir()
	seedVault(t, root)
	write(t, root, "raw/a.md", "a")
	write(t, root, "raw/notes/b.md", "b")

	result, err := Scan(root, "notes", nil)
	if err != nil {
		t.Fatalf("Scan: %v", err)
	}
	if want := []string{"raw/notes/b.md"}; !reflect.DeepEqual(rawRels(result.Eligible), want) {
		t.Errorf("eligible = %v, want %v", rawRels(result.Eligible), want)
	}
}

func rawRels(cands []Candidate) []string {
	rels := make([]string, len(cands))
	for i, c := range cands {
		rels[i] = c.RawRel
	}
	return rels
}
