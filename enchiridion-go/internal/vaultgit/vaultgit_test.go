package vaultgit

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/plumbing"
	"github.com/go-git/go-git/v5/plumbing/object"
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

func TestCommittedPagesFullReadCoversTheRootCommit(t *testing.T) {
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

	snap, err := repo.CommittedPages("")
	if err != nil {
		t.Fatalf("CommittedPages: %v", err)
	}
	if !snap.FullRebuild {
		t.Error("since == \"\" should read the full tree")
	}
	byRef := pagesByRef(snap)
	page, ok := byRef["wiki/concepts/a.md"]
	if !ok {
		t.Fatalf("Pages = %v, want an entry for wiki/concepts/a.md", snap.Pages)
	}
	if len(page.Date) != len("2026-01-02") {
		t.Errorf("date %q is not YYYY-MM-DD", page.Date)
	}
	if page.Content != "one\n" {
		t.Errorf("Content = %q, want the committed bytes", page.Content)
	}
	if _, ok := byRef["raw/notes.md"]; ok {
		t.Errorf("Pages included a raw/ file: %v", snap.Pages)
	}
}

func TestCommittedPagesFullReadTracksLaterEdits(t *testing.T) {
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

	snap, err := repo.CommittedPages("")
	if err != nil {
		t.Fatalf("CommittedPages: %v", err)
	}
	byRef := pagesByRef(snap)
	for _, ref := range []string{"wiki/concepts/a.md", "wiki/concepts/b.md"} {
		if _, ok := byRef[ref]; !ok {
			t.Errorf("Pages = %v, missing %s", snap.Pages, ref)
		}
	}
	if got := byRef["wiki/concepts/a.md"].Content; got != "one, edited\n" {
		t.Errorf("a.md Content = %q, want the latest committed bytes", got)
	}
}

func TestCommittedPagesIsLenientOnANonRepo(t *testing.T) {
	// The lenient surface: a missing repo means nothing to index, never a
	// failure — the search index reads that policy off this method.
	snap, err := New(t.TempDir()).CommittedPages("")
	if err != nil {
		t.Fatalf("CommittedPages: %v", err)
	}
	if snap.Head != "" || len(snap.Pages) != 0 {
		t.Fatalf("CommittedPages = %+v, want an empty Snapshot", snap)
	}
}

func TestCommittedPagesIsLenientOnAnEmptyRepo(t *testing.T) {
	root := t.TempDir()
	repo := New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	snap, err := repo.CommittedPages("")
	if err != nil {
		t.Fatalf("CommittedPages: %v", err)
	}
	if snap.Head != "" || len(snap.Pages) != 0 {
		t.Fatalf("CommittedPages = %+v, want an empty Snapshot for a repo with no HEAD", snap)
	}
}

func TestCommittedPagesRangeEnumeratesOnlyChangedPaths(t *testing.T) {
	root := t.TempDir()
	repo := New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	writePage(t, root, "wiki/concepts/a.md", "one\n")
	writePage(t, root, "wiki/concepts/b.md", "b\n")
	commitAll(t, repo, "first")
	first, err := repo.CommittedPages("")
	if err != nil {
		t.Fatalf("CommittedPages: %v", err)
	}
	watermark := first.Head

	writePage(t, root, "wiki/concepts/a.md", "one, edited\n")
	commitAll(t, repo, "second")

	snap, err := repo.CommittedPages(watermark)
	if err != nil {
		t.Fatalf("CommittedPages: %v", err)
	}
	if snap.FullRebuild {
		t.Fatal("a reachable watermark should not fall back to a full read")
	}
	if snap.Head == watermark {
		t.Fatal("Head should have advanced past the watermark")
	}
	byRef := pagesByRef(snap)
	if len(byRef) != 1 {
		t.Fatalf("Pages = %v, want only the changed path", snap.Pages)
	}
	if got := byRef["wiki/concepts/a.md"].Content; got != "one, edited\n" {
		t.Errorf("a.md Content = %q, want the latest committed bytes", got)
	}
}

func TestCommittedPagesRangeAtHeadIsANoOp(t *testing.T) {
	root := t.TempDir()
	repo := New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	writePage(t, root, "wiki/concepts/a.md", "one\n")
	commitAll(t, repo, "first")
	head, err := repo.CommittedPages("")
	if err != nil {
		t.Fatalf("CommittedPages: %v", err)
	}

	snap, err := repo.CommittedPages(head.Head)
	if err != nil {
		t.Fatalf("CommittedPages: %v", err)
	}
	if snap.FullRebuild {
		t.Fatal("since == Head should not fall back to a full read")
	}
	if snap.Head != head.Head || len(snap.Pages) != 0 {
		t.Fatalf("CommittedPages(Head) = %+v, want the same Head and no pages", snap)
	}
}

func TestCommittedPagesFallsBackToAFullReadOnAnUnreachableWatermark(t *testing.T) {
	root := t.TempDir()
	repo := New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	writePage(t, root, "wiki/concepts/a.md", "one\n")
	commitAll(t, repo, "first")

	// Never a real commit in this history — the reachability walk reaches
	// the root commit without finding it.
	snap, err := repo.CommittedPages("0000000000000000000000000000000000000000")
	if err != nil {
		t.Fatalf("CommittedPages: %v", err)
	}
	if !snap.FullRebuild {
		t.Fatal("an unreachable watermark should fall back to a full read")
	}
	byRef := pagesByRef(snap)
	if _, ok := byRef["wiki/concepts/a.md"]; !ok {
		t.Fatalf("Pages = %v, want the full tree", snap.Pages)
	}
}

func TestCommittedPagesRangeEnumeratesDeletions(t *testing.T) {
	root := t.TempDir()
	repo := New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	writePage(t, root, "wiki/concepts/a.md", "one\n")
	commitAll(t, repo, "first")
	first, err := repo.CommittedPages("")
	if err != nil {
		t.Fatalf("CommittedPages: %v", err)
	}

	if err := removeFile(root, "wiki/concepts/a.md"); err != nil {
		t.Fatal(err)
	}
	commitAll(t, repo, "second")

	snap, err := repo.CommittedPages(first.Head)
	if err != nil {
		t.Fatalf("CommittedPages: %v", err)
	}
	byRef := pagesByRef(snap)
	page, ok := byRef["wiki/concepts/a.md"]
	if !ok || !page.Deleted {
		t.Fatalf("Pages = %v, want a.md marked Deleted", snap.Pages)
	}
}

func TestCommittedPagesReadsChangedPathFromHeadNotIntermediateCommit(t *testing.T) {
	// A path touched three times in one range is read once and always holds
	// the current committed state — not any intermediate value.
	root := t.TempDir()
	repo := New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	writePage(t, root, "wiki/concepts/a.md", "v1\n")
	commitAll(t, repo, "first")
	first, err := repo.CommittedPages("")
	if err != nil {
		t.Fatalf("CommittedPages: %v", err)
	}

	writePage(t, root, "wiki/concepts/a.md", "v2\n")
	commitAll(t, repo, "second")
	writePage(t, root, "wiki/concepts/a.md", "v3\n")
	commitAll(t, repo, "third")

	snap, err := repo.CommittedPages(first.Head)
	if err != nil {
		t.Fatalf("CommittedPages: %v", err)
	}
	byRef := pagesByRef(snap)
	if len(byRef) != 1 {
		t.Fatalf("Pages = %v, want a.md enumerated exactly once", snap.Pages)
	}
	if got := byRef["wiki/concepts/a.md"].Content; got != "v3\n" {
		t.Errorf("Content = %q, want the HEAD blob v3", got)
	}
}

// TestCommittedPagesRangeEnumeratesPathsTouchedOnlyByAMerge builds a real
// merge commit — a path changed differently on two branches, resolved on
// merge — and checks that the merge commit's own diff against its first
// parent (not the range's non-merge commits) is what surfaces the path. The
// mtime scan this design replaces self-healed a path like this by rereading
// every file every search; the commit-range walk must enumerate it
// explicitly instead, per ADR-0015.
func TestCommittedPagesRangeEnumeratesPathsTouchedOnlyByAMerge(t *testing.T) {
	root := t.TempDir()
	repo := New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	writePage(t, root, "wiki/concepts/base.md", "base\n")
	writePage(t, root, "wiki/concepts/conflict.md", "base version\n")
	commitAll(t, repo, "base")
	base, err := repo.CommittedPages("")
	if err != nil {
		t.Fatalf("CommittedPages: %v", err)
	}

	gitRepo, err := git.PlainOpen(root)
	if err != nil {
		t.Fatalf("PlainOpen: %v", err)
	}
	worktree, err := gitRepo.Worktree()
	if err != nil {
		t.Fatalf("Worktree: %v", err)
	}

	if err := worktree.Checkout(&git.CheckoutOptions{
		Branch: plumbing.NewBranchReferenceName("feature"),
		Create: true,
	}); err != nil {
		t.Fatalf("checkout feature: %v", err)
	}
	writePage(t, root, "wiki/concepts/conflict.md", "feature version\n")
	commitAll(t, repo, "feature change")
	featureHead, err := gitRepo.Head()
	if err != nil {
		t.Fatalf("Head: %v", err)
	}

	if err := worktree.Checkout(&git.CheckoutOptions{
		Branch: plumbing.NewBranchReferenceName("master"),
	}); err != nil {
		t.Fatalf("checkout master: %v", err)
	}
	writePage(t, root, "wiki/concepts/conflict.md", "main version\n")
	commitAll(t, repo, "main change")
	mainHead, err := gitRepo.Head()
	if err != nil {
		t.Fatalf("Head: %v", err)
	}

	// Simulate the merge's conflict resolution directly in the worktree, then
	// commit with both branch tips as parents — a real two-parent commit,
	// exactly what `git merge` produces.
	writePage(t, root, "wiki/concepts/conflict.md", "merged version\n")
	if _, err := worktree.Add("."); err != nil {
		t.Fatalf("Add: %v", err)
	}
	mergeHash, err := worktree.Commit("merge feature", &git.CommitOptions{
		Parents: []plumbing.Hash{mainHead.Hash(), featureHead.Hash()},
		Author:  testSignature(),
	})
	if err != nil {
		t.Fatalf("merge commit: %v", err)
	}
	if err := gitRepo.Storer.SetReference(plumbing.NewHashReference(
		plumbing.NewBranchReferenceName("master"), mergeHash)); err != nil {
		t.Fatalf("updating master ref: %v", err)
	}

	snap, err := repo.CommittedPages(base.Head)
	if err != nil {
		t.Fatalf("CommittedPages: %v", err)
	}
	byRef := pagesByRef(snap)
	page, ok := byRef["wiki/concepts/conflict.md"]
	if !ok {
		t.Fatalf("Pages = %v, want conflict.md enumerated from the merge commit's own diff", snap.Pages)
	}
	if page.Content != "merged version\n" {
		t.Errorf("Content = %q, want the merge resolution", page.Content)
	}
}

// TestCommittedPagesRangeAttributesDateAcrossAMergeSecondParent pins a bug
// found in review: the range walk stops at `since` while descending head's
// *first-parent* chain, so it never visits a merge's other parents. A page
// introduced only on a branch merged in that way is still enumerated
// correctly (the merge commit's own diff against its first parent surfaces
// it), but without the fallback in [rangeSnapshot], its date would come back
// empty even though it has one.
func TestCommittedPagesRangeAttributesDateAcrossAMergeSecondParent(t *testing.T) {
	root := t.TempDir()
	repo := New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	writePage(t, root, "wiki/concepts/base.md", "base\n")
	commitAll(t, repo, "base")
	base, err := repo.CommittedPages("")
	if err != nil {
		t.Fatalf("CommittedPages: %v", err)
	}

	gitRepo, err := git.PlainOpen(root)
	if err != nil {
		t.Fatalf("PlainOpen: %v", err)
	}
	worktree, err := gitRepo.Worktree()
	if err != nil {
		t.Fatalf("Worktree: %v", err)
	}
	if err := worktree.Checkout(&git.CheckoutOptions{
		Branch: plumbing.NewBranchReferenceName("feature"),
		Create: true,
	}); err != nil {
		t.Fatalf("checkout feature: %v", err)
	}
	writePage(t, root, "wiki/concepts/x.md", "x\n")
	commitAll(t, repo, "add x")
	featureHead, err := gitRepo.Head()
	if err != nil {
		t.Fatalf("Head: %v", err)
	}

	if err := worktree.Checkout(&git.CheckoutOptions{
		Branch: plumbing.NewBranchReferenceName("master"),
	}); err != nil {
		t.Fatalf("checkout master: %v", err)
	}
	writePage(t, root, "wiki/concepts/y.md", "y\n")
	commitAll(t, repo, "add y")
	mainHead, err := gitRepo.Head()
	if err != nil {
		t.Fatalf("Head: %v", err)
	}

	// Fast-forward the worktree to the merge result: both x.md and y.md
	// present, exactly what a real `git merge` (no conflicts) would leave.
	writePage(t, root, "wiki/concepts/x.md", "x\n")
	if _, err := worktree.Add("."); err != nil {
		t.Fatalf("Add: %v", err)
	}
	mergeHash, err := worktree.Commit("merge feature", &git.CommitOptions{
		Parents: []plumbing.Hash{mainHead.Hash(), featureHead.Hash()},
		Author:  testSignature(),
	})
	if err != nil {
		t.Fatalf("merge commit: %v", err)
	}
	if err := gitRepo.Storer.SetReference(plumbing.NewHashReference(
		plumbing.NewBranchReferenceName("master"), mergeHash)); err != nil {
		t.Fatalf("updating master ref: %v", err)
	}

	snap, err := repo.CommittedPages(base.Head)
	if err != nil {
		t.Fatalf("CommittedPages: %v", err)
	}
	byRef := pagesByRef(snap)
	x, ok := byRef["wiki/concepts/x.md"]
	if !ok {
		t.Fatalf("Pages = %v, want x.md enumerated via the merge's own diff", snap.Pages)
	}
	if x.Date == "" {
		t.Error("x.md Date is empty, want the commit date from the feature branch commit")
	}
}

func testSignature() *object.Signature {
	return &object.Signature{Name: "test", Email: "test@example.com", When: time.Now()}
}

func pagesByRef(snap Snapshot) map[string]PageChange {
	out := make(map[string]PageChange, len(snap.Pages))
	for _, p := range snap.Pages {
		out[p.PageRef] = p
	}
	return out
}

func removeFile(root, rel string) error {
	return os.Remove(filepath.Join(root, filepath.FromSlash(rel)))
}

func TestLastCommitDateReturnsDateForACommittedPath(t *testing.T) {
	root := t.TempDir()
	repo := New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	writePage(t, root, "raw/notes.md", "raw\n")
	commitAll(t, repo, "first")

	got := repo.LastCommitDate("raw/notes.md")
	if len(got) != len("2026-01-02") {
		t.Fatalf("LastCommitDate = %q, want a YYYY-MM-DD date", got)
	}
}

func TestLastCommitDateTracksTheLatestCommit(t *testing.T) {
	root := t.TempDir()
	repo := New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	writePage(t, root, "raw/notes.md", "v1\n")
	commitAll(t, repo, "first")

	first := repo.LastCommitDate("raw/notes.md")
	writePage(t, root, "raw/notes.md", "v2\n")
	commitAll(t, repo, "second")

	// Same-day commits are indistinguishable at day granularity, so this only
	// asserts the path is still resolvable after a second commit — the
	// strictly-newer comparison is what the sweep does with these dates.
	if got := repo.LastCommitDate("raw/notes.md"); got != first {
		t.Errorf("LastCommitDate changed %q -> %q across same-day commits", first, got)
	}
}

func TestLastCommitDateIsEmptyForAnUntrackedPath(t *testing.T) {
	root := t.TempDir()
	repo := New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	if got := repo.LastCommitDate("raw/nope.md"); got != "" {
		t.Fatalf("LastCommitDate = %q, want empty for a never-committed path", got)
	}
}

func TestLastCommitDateIsLenientOnANonRepo(t *testing.T) {
	if got := New(t.TempDir()).LastCommitDate("raw/notes.md"); got != "" {
		t.Fatalf("LastCommitDate = %q, want empty on a non-repo", got)
	}
}

func TestPorcelainMentionsAnUntrackedFile(t *testing.T) {
	root := t.TempDir()
	repo := New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	writePage(t, root, "raw/notes.md", "raw\n")
	if !repo.PorcelainMentions("raw/notes.md") {
		t.Fatal("an untracked file did not report as dirty")
	}
}

func TestPorcelainMentionsAModifiedFile(t *testing.T) {
	root := t.TempDir()
	repo := New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	writePage(t, root, "raw/notes.md", "raw\n")
	commitAll(t, repo, "first")
	writePage(t, root, "raw/notes.md", "raw, edited\n")
	if !repo.PorcelainMentions("raw/notes.md") {
		t.Fatal("a modified file did not report as dirty")
	}
}

func TestPorcelainMentionsACommittedUnchangedFileIsClean(t *testing.T) {
	root := t.TempDir()
	repo := New(root)
	if err := repo.Init(); err != nil {
		t.Fatalf("Init: %v", err)
	}
	writePage(t, root, "raw/notes.md", "raw\n")
	commitAll(t, repo, "first")
	if repo.PorcelainMentions("raw/notes.md") {
		t.Fatal("a clean committed file reported as dirty")
	}
}

func TestPorcelainMentionsIsLenientOnANonRepo(t *testing.T) {
	if New(t.TempDir()).PorcelainMentions("raw/notes.md") {
		t.Fatal("a non-repo reported a path as dirty")
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
