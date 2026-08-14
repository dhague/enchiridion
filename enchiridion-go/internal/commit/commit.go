// Package commit writes one structured git commit per ingestion/edit.
// Ported from `wiki-plugin/scripts/commit.py`.
//
// The commit message is a compounding asset — audit log, "what changed this
// week" feed, manager-report source — so it is emitted here, never freehand
// by the agent. This doc comment is the format's only specification:
//
//	ingest: <source doc title>
//
//	created: wiki/concepts/prepared-statements.md
//	updated: wiki/concepts/db-connection-pooling.md
//	superseded: wiki/sources/deploy-capistrano.md -> wiki/sources/deploy-github-actions.md
//	source-date: 2026-03-01
//
// Git is a **hard dependency**: a root that isn't a work tree is an error,
// never a silent skip — the time model depends on the history being
// complete. The Go port embeds go-git rather than shelling out (ADR-0011),
// so "git is missing from PATH" can no longer arise; what remains is "root
// is not a git work tree".
//
// A manifest naming a RawSource is additionally gated on
// [chainofevidence.Check], failing before anything is staged. This is the
// hard block; the ingest package runs the same check earlier, at
// plan-validation time, as a courtesy to the agent.
package commit

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/dhague/enchiridion/enchiridion-go/internal/chainofevidence"
	"github.com/dhague/enchiridion/enchiridion-go/internal/wikipage"
)

// ErrGate is returned when a manifest fails the chain-of-evidence gate.
//
// Distinct from a git failure: a rejected manifest is a planning bug, a git
// failure is an environment problem.
var ErrGate = errors.New("commit gated")

// Git is the slice of [vaultgit.Repo] this package needs, named as an
// interface so tests can commit against an in-memory fake instead of a real
// repository.
type Git interface {
	IsWorkTree() bool
	Add(paths ...string) error
	Commit(message string) (string, error)
}

// Supersession is one `old -> new` pair in a manifest.
type Supersession struct {
	Old string
	New string
}

// UnmarshalJSON decodes the two-element `[old, new]` array the manifest
// format uses for a supersession pair.
func (s *Supersession) UnmarshalJSON(data []byte) error {
	var pair []string
	if err := json.Unmarshal(data, &pair); err != nil {
		return err
	}
	if len(pair) != 2 {
		return fmt.Errorf("superseded entry must be [old, new], got %d elements", len(pair))
	}
	s.Old, s.New = pair[0], pair[1]
	return nil
}

// MarshalJSON writes the same two-element array [Supersession.UnmarshalJSON]
// reads.
func (s Supersession) MarshalJSON() ([]byte, error) {
	return json.Marshal([]string{s.Old, s.New})
}

// Manifest is the deterministic description of one ingestion/edit's touched
// files.
type Manifest struct {
	Title      string         `json:"title"`
	Action     string         `json:"action"`
	Created    []string       `json:"created"`
	Updated    []string       `json:"updated"`
	Superseded []Supersession `json:"superseded"`
	SourceDate string         `json:"source_date"`
	// RawSource is the raw/ artifact this ingestion is sourced from, if any.
	// Staged automatically, so the source document always lands in the same
	// commit as the pages it produced.
	RawSource string `json:"raw_source"`
}

// defaultAction is the verb a manifest that names none commits under.
const defaultAction = "ingest"

// StagedPaths returns every path this manifest touches, de-duplicated, in a
// stable order.
func (m Manifest) StagedPaths() []string {
	var paths []string
	paths = append(paths, m.Created...)
	paths = append(paths, m.Updated...)
	for _, s := range m.Superseded {
		paths = append(paths, s.Old, s.New)
	}
	if m.RawSource != "" {
		paths = append(paths, m.RawSource)
	}

	seen := map[string]bool{}
	ordered := make([]string, 0, len(paths))
	for _, path := range paths {
		if !seen[path] {
			seen[path] = true
			ordered = append(ordered, path)
		}
	}
	return ordered
}

// BuildMessage renders manifest to the structured commit message (see the
// package comment for the format). Deterministic.
func BuildMessage(m Manifest) string {
	action := m.Action
	if action == "" {
		action = defaultAction
	}
	lines := []string{action + ": " + m.Title, ""}
	for _, pageRef := range m.Created {
		lines = append(lines, "created: "+pageRef)
	}
	for _, pageRef := range m.Updated {
		lines = append(lines, "updated: "+pageRef)
	}
	for _, s := range m.Superseded {
		lines = append(lines, "superseded: "+s.Old+" -> "+s.New)
	}
	if m.SourceDate != "" {
		lines = append(lines, "source-date: "+m.SourceDate)
	}
	return strings.Join(lines, "\n") + "\n"
}

// checkChainOfEvidence gates the commit on [chainofevidence.Check].
//
// A no-op when RawSource is unset (a synthesis save has no raw artifact to
// demand a stub for). Pages are read from disk — the caller has already
// written them by the time [Commit] runs. A staged page missing from disk is
// silently skipped: that's the caller's bug to report, not this gate's.
func checkChainOfEvidence(root string, m Manifest) error {
	if m.RawSource == "" {
		return nil
	}

	staged := map[string]wikipage.Page{}
	for _, pageRef := range append(append([]string{}, m.Created...), m.Updated...) {
		path := filepath.Join(root, filepath.FromSlash(pageRef))
		text, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		staged[pageRef] = wikipage.Page{Text: string(text)}
	}

	problems, err := chainofevidence.Check(staged, m.RawSource)
	if err != nil {
		return err
	}
	if len(problems) > 0 {
		return fmt.Errorf("%w: %s", ErrGate, strings.Join(problems, "; "))
	}
	return nil
}

// Commit stages the manifest's paths and writes one structured commit,
// returning the SHA.
//
// git is injectable for tests; pass a [vaultgit.Repo] over root in
// production. Git stays a hard dependency: a root that isn't a work tree is
// an error, not a skip.
func Commit(root string, m Manifest, git Git) (string, error) {
	if !git.IsWorkTree() {
		return "", fmt.Errorf("%s is not a git work tree; the vault's history is not optional", root)
	}
	if err := checkChainOfEvidence(root, m); err != nil {
		return "", err
	}
	if paths := m.StagedPaths(); len(paths) > 0 {
		if err := git.Add(paths...); err != nil {
			return "", err
		}
	}
	return git.Commit(BuildMessage(m))
}
