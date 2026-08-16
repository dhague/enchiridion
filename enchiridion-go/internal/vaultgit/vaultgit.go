// Package vaultgit is the one package for git facts about the vault (#126).
//
// It uses embedded go-git rather than shelling out to a `git` binary: a
// hidden `git`-on-PATH dependency would undercut the whole point of a
// single static binary for the non-coding-agent hosts it targets
// (ADR-0011). Each caller's absent-git policy reads as one of two surfaces:
//
//   - **Strict** — [Repo.Init], [Repo.Add], [Repo.Commit]: return an error
//     when the operation can't be performed. This is `commit`'s "git is a
//     hard dependency" reading.
//   - **Lenient** — [Repo.IsWorkTree], [Repo.CommittedPages],
//     [Repo.LastCommitDate]: a missing or broken repository yields the
//     documented default (false / an empty Snapshot / "") rather than an
//     error. `search` reads "no commits means nothing to index, never a
//     failure" off this.
//
// This touches ADR-0003 (attribution from ingested content, not git
// identity) only in *where* git is invoked from; it is not reopened.
package vaultgit

import (
	"errors"
	"fmt"
	"os"
	"os/user"
	"time"

	"github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/config"
	"github.com/go-git/go-git/v5/plumbing"
	"github.com/go-git/go-git/v5/plumbing/object"
	"github.com/go-git/go-git/v5/plumbing/storer"
)

// Repo holds git verbs and facts over one vault root.
//
// Constructing one never touches the filesystem — it just pins the root. All
// probing is lazy, so a caller can build one, ask an availability question,
// and never pay for opening a repository if git isn't needed.
type Repo struct {
	Root string
}

// New returns a Repo pinned to root.
func New(root string) *Repo { return &Repo{Root: root} }

func (r *Repo) open() (*git.Repository, error) {
	return git.PlainOpen(r.Root)
}

// IsWorkTree reports whether root is a git work tree.
//
// **Lenient:** false when the repository is absent or unreadable — a
// question, not a demand.
func (r *Repo) IsWorkTree() bool {
	repo, err := r.open()
	if err != nil {
		return false
	}
	_, err = repo.Worktree()
	return err == nil
}

// Init initialises a git repository at root.
//
// **Strict:** returns an error on failure.
func (r *Repo) Init() error {
	if _, err := git.PlainInit(r.Root, false); err != nil {
		return fmt.Errorf("git init %s: %w", r.Root, err)
	}
	return nil
}

// Add stages paths (each vault-relative; a directory is staged
// recursively).
//
// **Strict:** returns an error on failure.
func (r *Repo) Add(paths ...string) error {
	repo, err := r.open()
	if err != nil {
		return fmt.Errorf("git add: %w", err)
	}
	worktree, err := repo.Worktree()
	if err != nil {
		return fmt.Errorf("git add: %w", err)
	}
	for _, path := range paths {
		if _, err := worktree.Add(path); err != nil {
			return fmt.Errorf("git add %s: %w", path, err)
		}
	}
	return nil
}

// Commit writes one commit with message and returns its SHA.
//
// **Strict:** returns an error on failure.
func (r *Repo) Commit(message string) (string, error) {
	repo, err := r.open()
	if err != nil {
		return "", fmt.Errorf("git commit: %w", err)
	}
	worktree, err := repo.Worktree()
	if err != nil {
		return "", fmt.Errorf("git commit: %w", err)
	}
	signature, err := r.signature(repo)
	if err != nil {
		return "", err
	}
	hash, err := worktree.Commit(message, &git.CommitOptions{Author: signature})
	if err != nil {
		return "", fmt.Errorf("git commit: %w", err)
	}
	return hash.String(), nil
}

// signature reads the committer identity from the merged system + global +
// local git config.
//
// An unconfigured identity falls back to a user@hostname signature rather
// than failing — the same fallback the `git` CLI itself derives when
// `user.name`/`user.email` are unset. ADR-0003 already says attribution
// comes from ingested content, not git identity, so the committer here is
// bookkeeping, not provenance.
func (r *Repo) signature(repo *git.Repository) (*object.Signature, error) {
	cfg, err := repo.ConfigScoped(config.SystemScope)
	if err != nil {
		return nil, fmt.Errorf("git commit: reading git config: %w", err)
	}
	name, email := cfg.User.Name, cfg.User.Email
	if name == "" {
		name = fallbackUser()
	}
	if email == "" {
		email = name + "@" + fallbackHost()
	}
	return &object.Signature{Name: name, Email: email, When: time.Now()}, nil
}

func fallbackUser() string {
	if u, err := user.Current(); err == nil && u.Username != "" {
		return u.Username
	}
	return "enchiridion"
}

func fallbackHost() string {
	if host, err := os.Hostname(); err == nil && host != "" {
		return host
	}
	return "localhost"
}

// LastCommitDate returns the last commit date of rel (YYYY-MM-DD), or "" when
// root isn't a work tree, rel was never committed, or the history can't be
// walked.
//
// **Lenient:** "" is the default, never an error — the sweep reads "fail
// toward offering" off this, since a missing date must err toward re-offering
// the file, the only safe direction for a signal that must not lose data.
func (r *Repo) LastCommitDate(rel string) string {
	repo, err := r.open()
	if err != nil {
		return ""
	}
	head, err := repo.Head()
	if err != nil {
		return ""
	}
	iter, err := repo.Log(&git.LogOptions{
		From:       head.Hash(),
		PathFilter: func(path string) bool { return path == rel },
	})
	if err != nil {
		return ""
	}
	defer iter.Close()
	commit, err := iter.Next()
	if err != nil {
		return ""
	}
	return commit.Author.When.Format(time.DateOnly)
}

// PorcelainMentions reports whether rel is modified or untracked in the
// working tree — the `git status --porcelain -- rel` signal. Untracked
// counts: a brand-new file isn't in git's index at all, and finding it is the
// point.
//
// **Lenient:** false when root isn't a work tree or the status can't be read —
// the sweep treats "can't tell if dirty" as "clean".
func (r *Repo) PorcelainMentions(rel string) bool {
	repo, err := r.open()
	if err != nil {
		return false
	}
	worktree, err := repo.Worktree()
	if err != nil {
		return false
	}
	status, err := worktree.Status()
	if err != nil {
		return false
	}
	_, ok := status[rel]
	return ok
}

// PageChange is one `wiki/**.md` page's committed state as of a [Snapshot]'s
// Head.
type PageChange struct {
	// PageRef is vault-relative (ADR-0009).
	PageRef string
	// Date is the latest non-merge commit date touching this page
	// (YYYY-MM-DD), or "" if it can't be attributed within the read that
	// produced this Snapshot.
	Date string
	// Content is the page's bytes at Head — always read from Head's tree,
	// never from the intermediate commit that changed it, so a path touched
	// three times in one range is read once and always holds the current
	// committed state. Empty when Deleted.
	Content string
	// Deleted reports whether the page no longer exists in Head's tree.
	Deleted bool
}

// Snapshot is one [Repo.CommittedPages] read.
type Snapshot struct {
	// Head is the resolved HEAD commit SHA, or "" for a repo with no commits.
	Head string
	// FullRebuild reports whether this read fell back to (or was asked for)
	// a full tree read rather than an enumerated delta — Pages then holds
	// every `wiki/**.md` page in Head's tree, not a changed subset.
	FullRebuild bool
	// Pages is the per-page delta (or, when FullRebuild, the whole tree).
	Pages []PageChange
}

// CommittedPages returns the vault's `wiki/**.md` pages changed since commit
// since, read from HEAD's tree. since == "" means "all of HEAD's tree", so a
// first build and a full rebuild are the same call.
//
// **Lenient:** a missing repository or a repository with no commits yields
// an empty Snapshot (Head == ""), never an error — "nothing committed,
// nothing indexed."
//
// Reachability is not a separate query: the range walk that enumerates
// changed paths stops the moment it finds since, and reaching the root
// commit without finding it — an unreachable or unrecognised watermark, from
// an amend, rebase, `reset --hard`, or a re-clone over an existing index —
// falls back to a full tree read (Snapshot.FullRebuild = true).
func (r *Repo) CommittedPages(since string) (Snapshot, error) {
	repo, err := r.open()
	if err != nil {
		return Snapshot{}, nil //nolint:nilerr // lenient: missing repo, nothing to index
	}
	head, err := repo.Head()
	if err != nil {
		return Snapshot{}, nil //nolint:nilerr // lenient: no HEAD, nothing to index
	}

	if since != "" {
		snap, found, err := r.rangeSnapshot(repo, head.Hash(), since)
		if err != nil {
			return Snapshot{}, err
		}
		if found {
			return snap, nil
		}
	}
	return r.fullSnapshot(repo, head.Hash())
}

// rangeSnapshot walks commits reachable from head, stopping the moment it
// finds since. found is false when the root commit is reached without ever
// seeing since — the caller's cue to fall back to a full tree read.
func (r *Repo) rangeSnapshot(repo *git.Repository, head plumbing.Hash, since string) (Snapshot, bool, error) {
	sinceHash := plumbing.NewHash(since)
	iter, err := repo.Log(&git.LogOptions{From: head})
	if err != nil {
		return Snapshot{}, false, fmt.Errorf("walking commit range: %w", err)
	}
	defer iter.Close()

	changed := map[string]bool{}
	latest := map[string]time.Time{}
	found := false
	err = iter.ForEach(func(commit *object.Commit) error {
		if commit.Hash == sinceHash {
			found = true
			return storer.ErrStop
		}
		paths, err := changedPaths(commit)
		if err != nil {
			return nil //nolint:nilerr // lenient surface: skip what can't be diffed
		}
		for _, path := range paths {
			changed[path] = true
		}
		// Merge commits contribute to path enumeration above (so a path
		// touched only by a conflict resolution is still enumerated) but not
		// to date attribution, matching CommitDates' historical semantics.
		if commit.NumParents() <= 1 {
			when := commit.Author.When
			for _, path := range paths {
				if prev, ok := latest[path]; !ok || when.After(prev) {
					latest[path] = when
				}
			}
		}
		return nil
	})
	if err != nil {
		return Snapshot{}, false, fmt.Errorf("walking commit range: %w", err)
	}
	if !found {
		return Snapshot{}, false, nil
	}

	headTree, err := headTree(repo, head)
	if err != nil {
		return Snapshot{}, false, err
	}

	pages := make([]PageChange, 0, len(changed))
	for path := range changed {
		content, deleted, err := blobContent(headTree, path)
		if err != nil {
			return Snapshot{}, false, err
		}
		date := ""
		if when, ok := latest[path]; ok {
			date = when.Format(time.DateOnly)
		} else {
			// The range walk stops at `since` down head's first-parent
			// chain, so it never visits a merge's other parents — a path
			// introduced only on a branch merged in by a non-first-parent
			// commit has no entry in `latest` even though it does have an
			// attributable date. Fall back to a dedicated, unbounded walk
			// for just this path; rare, so the extra cost stays bounded to
			// the pages that actually need it.
			date = pathDate(repo, head, path)
		}
		pages = append(pages, PageChange{PageRef: path, Date: date, Content: content, Deleted: deleted})
	}
	return Snapshot{Head: head.String(), Pages: pages}, true, nil
}

// fullSnapshot reads every `wiki/**.md` page out of head's tree.
func (r *Repo) fullSnapshot(repo *git.Repository, head plumbing.Hash) (Snapshot, error) {
	tree, err := headTree(repo, head)
	if err != nil {
		return Snapshot{}, err
	}
	dates := commitDates(repo, head)

	var pages []PageChange
	err = tree.Files().ForEach(func(f *object.File) error {
		if !isWikiPage(f.Name) {
			return nil
		}
		content, err := f.Contents()
		if err != nil {
			return fmt.Errorf("reading %s: %w", f.Name, err)
		}
		pages = append(pages, PageChange{PageRef: f.Name, Date: dates[f.Name], Content: content})
		return nil
	})
	if err != nil {
		return Snapshot{}, err
	}
	return Snapshot{Head: head.String(), FullRebuild: true, Pages: pages}, nil
}

func headTree(repo *git.Repository, head plumbing.Hash) (*object.Tree, error) {
	commit, err := repo.CommitObject(head)
	if err != nil {
		return nil, fmt.Errorf("resolving HEAD commit: %w", err)
	}
	tree, err := commit.Tree()
	if err != nil {
		return nil, fmt.Errorf("resolving HEAD tree: %w", err)
	}
	return tree, nil
}

// blobContent reads path's blob out of tree. deleted is true when path no
// longer exists in tree, which is not an error here — it is the expected
// shape for a path a range walk saw removed.
func blobContent(tree *object.Tree, path string) (content string, deleted bool, err error) {
	f, err := tree.File(path)
	if err != nil {
		if errors.Is(err, object.ErrFileNotFound) {
			return "", true, nil
		}
		return "", false, fmt.Errorf("reading %s: %w", path, err)
	}
	content, err = f.Contents()
	if err != nil {
		return "", false, fmt.Errorf("reading %s: %w", path, err)
	}
	return content, false, nil
}

// commitDates walks every commit reachable from head and returns
// {path: YYYY-MM-DD}, the most recent non-merge commit date per `wiki/**.md`
// path. Used by fullSnapshot; the range-walk counterpart is inlined in
// rangeSnapshot, since that walk stops early and enumerates paths in the same
// pass.
//
// Merge commits contribute nothing, matching `git log --name-only`'s default
// of showing no diff for them; the root commit is diffed against the empty
// tree, so a vault's scaffold commit still dates its files.
// pathDate returns the latest non-merge commit date touching path, walking
// back from head with no stopping point short of the root commit. It's the
// per-path fallback [rangeSnapshot] uses when its bounded walk can't
// attribute a date, so unlike [commitDates] it deliberately doesn't take a
// range: the whole point is to see history the range walk didn't.
func pathDate(repo *git.Repository, head plumbing.Hash, path string) string {
	iter, err := repo.Log(&git.LogOptions{
		From:       head,
		PathFilter: func(p string) bool { return p == path },
	})
	if err != nil {
		return ""
	}
	defer iter.Close()
	for {
		commit, err := iter.Next()
		if err != nil {
			return ""
		}
		if commit.NumParents() > 1 {
			continue
		}
		return commit.Author.When.Format(time.DateOnly)
	}
}

func commitDates(repo *git.Repository, head plumbing.Hash) map[string]string {
	dates := map[string]string{}

	iter, err := repo.Log(&git.LogOptions{From: head})
	if err != nil {
		return dates
	}
	defer iter.Close()

	// Keep the latest date per path rather than trusting log order, so the
	// result doesn't depend on how go-git orders a branchy history.
	latest := map[string]time.Time{}
	_ = iter.ForEach(func(commit *object.Commit) error {
		if commit.NumParents() > 1 {
			return nil
		}
		paths, err := changedPaths(commit)
		if err != nil {
			return nil //nolint:nilerr // lenient surface: skip what can't be diffed
		}
		when := commit.Author.When
		for _, path := range paths {
			if prev, seen := latest[path]; !seen || when.After(prev) {
				latest[path] = when
			}
		}
		return nil
	})

	for path, when := range latest {
		dates[path] = when.Format(time.DateOnly)
	}
	return dates
}

// changedPaths returns the `wiki/**.md` paths this commit touched, relative
// to its first parent (or to the empty tree, for a root commit). Merge
// commits are diffed against their first parent too, so a path touched only
// by a conflict resolution is still enumerated.
func changedPaths(commit *object.Commit) ([]string, error) {
	tree, err := commit.Tree()
	if err != nil {
		return nil, err
	}
	var parentTree *object.Tree
	if commit.NumParents() >= 1 {
		parent, err := commit.Parent(0)
		if err != nil {
			return nil, err
		}
		if parentTree, err = parent.Tree(); err != nil {
			return nil, err
		}
	}
	changes, err := object.DiffTree(parentTree, tree)
	if err != nil {
		return nil, err
	}
	var paths []string
	for _, change := range changes {
		for _, name := range []string{change.From.Name, change.To.Name} {
			if isWikiPage(name) {
				paths = append(paths, name)
			}
		}
	}
	return paths, nil
}

func isWikiPage(name string) bool {
	return len(name) > len("wiki/") &&
		name[:len("wiki/")] == "wiki/" &&
		len(name) > len(".md") &&
		name[len(name)-len(".md"):] == ".md"
}
