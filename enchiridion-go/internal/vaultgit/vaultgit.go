// Package vaultgit is the one package for git facts about the vault, ported
// from `wiki-plugin/scripts/vault_git.py` (#126).
//
// The Go port swaps the Python module's `subprocess.run(["git", ...])` calls
// for embedded go-git: a hidden `git`-on-PATH dependency would undercut the
// whole point of the rewrite for the non-coding-agent hosts it targets
// (ADR-0011). The interface, and each caller's absent-git policy, is
// otherwise unchanged — what was "git is missing from PATH" is now "root is
// not a git work tree", and the two surfaces still read:
//
//   - **Strict** — [Repo.Init], [Repo.Add], [Repo.Commit]: return an error
//     when the operation can't be performed. This is `commit`'s "git is a
//     hard dependency" reading.
//   - **Lenient** — [Repo.IsWorkTree], [Repo.CommitDates]: a missing or
//     broken repository yields the documented default (false / an empty map)
//     rather than an error. `search` reads "a missing date means git_date is
//     null, never a failure" off this.
//
// This touches ADR-0003 (attribution from ingested content, not git
// identity) only in *where* git is invoked from; it is not reopened.
package vaultgit

import (
	"fmt"
	"os"
	"os/user"
	"time"

	"github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/config"
	"github.com/go-git/go-git/v5/plumbing/object"
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
// than failing. That mirrors the `git` binary the Python implementation
// shelled out to, which derives the same fallback when `user.name`/
// `user.email` are unset — and ADR-0003 already says attribution comes from
// ingested content, not git identity, so the committer here is bookkeeping,
// not provenance.
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

// CommitDates returns {vault-relative page_ref: YYYY-MM-DD} — the most
// recent commit date per file, for `.md` files under `wiki/` only.
//
// **Lenient:** an empty map when root isn't a work tree or the history can't
// be walked — the search index reads "a missing date means git_date is null,
// never a failure" off this. Compute this once per scan, not once per page
// (#124).
//
// Merge commits contribute nothing, matching `git log --name-only`'s default
// of showing no diff for them; the root commit is diffed against the empty
// tree, so a vault's scaffold commit still dates its files.
func (r *Repo) CommitDates() map[string]string {
	dates := map[string]string{}

	repo, err := r.open()
	if err != nil {
		return dates
	}
	head, err := repo.Head()
	if err != nil {
		return dates
	}
	iter, err := repo.Log(&git.LogOptions{From: head.Hash()})
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
// to its first parent (or to the empty tree, for a root commit).
func changedPaths(commit *object.Commit) ([]string, error) {
	tree, err := commit.Tree()
	if err != nil {
		return nil, err
	}
	var parentTree *object.Tree
	if commit.NumParents() == 1 {
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
