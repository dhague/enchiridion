// Package vaultgittest provides an in-memory stand-in for [vaultgit.Repo],
// the Go counterpart of `wiki-plugin/tests/fake_vault_git.py`.
//
// Every package that commits takes its git as an interface so its tests can
// run against this instead of a real repository — no temp repo, no identity
// config, and the staged paths and messages are inspectable afterwards. It
// lives in a non-test file because more than one package's tests need it.
package vaultgittest

import (
	"errors"
	"fmt"

	"github.com/dhague/enchiridion/enchiridion-go/internal/vaultgit"
)

// Fake records what was staged and committed, and returns a synthetic SHA.
type Fake struct {
	// NotAWorkTree makes [Fake.IsWorkTree] report false, for exercising the
	// "git is a hard dependency" path.
	NotAWorkTree bool
	// AddErr / CommitErr, when set, are returned by the matching call.
	AddErr    error
	CommitErr error

	// Added is every path passed to Add, in call order.
	Added []string
	// Messages is every commit message, in call order.
	Messages []string

	// Snapshots scripts what CommittedPages returns for a given `since`
	// argument ("" for the full-tree read). A caller drives the range vs.
	// full-rebuild path by keying the map on the watermark it expects to be
	// passed.
	Snapshots map[string]vaultgit.Snapshot
	// CommittedPagesErr, when set, is returned by every CommittedPages call.
	CommittedPagesErr error
	// CommittedPagesCalls is every `since` argument passed to CommittedPages,
	// in call order — so a test can assert whether the delta or full path
	// was requested.
	CommittedPagesCalls []string
}

// CommittedPages returns the scripted Snapshot for since, or the zero
// Snapshot when Snapshots has no entry for it.
func (f *Fake) CommittedPages(since string) (vaultgit.Snapshot, error) {
	f.CommittedPagesCalls = append(f.CommittedPagesCalls, since)
	if f.CommittedPagesErr != nil {
		return vaultgit.Snapshot{}, f.CommittedPagesErr
	}
	return f.Snapshots[since], nil
}

func (f *Fake) IsWorkTree() bool { return !f.NotAWorkTree }

func (f *Fake) Add(paths ...string) error {
	if f.AddErr != nil {
		return f.AddErr
	}
	f.Added = append(f.Added, paths...)
	return nil
}

func (f *Fake) Commit(message string) (string, error) {
	if f.CommitErr != nil {
		return "", f.CommitErr
	}
	f.Messages = append(f.Messages, message)
	// A distinct, deterministic 40-hex SHA per commit, so a test can assert
	// on the returned value without pattern-matching a random one.
	return fmt.Sprintf("%040x", len(f.Messages)), nil
}

// ErrNoGit is a convenient failure to assign to AddErr/CommitErr.
var ErrNoGit = errors.New("fake git failure")
