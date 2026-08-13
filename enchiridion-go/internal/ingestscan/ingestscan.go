// Package ingestscan is the ingestion sweep — scan `raw/` for files that
// need ingestion. Ported from `wiki-plugin/scripts/ingest_scan.py`.
//
// Two independent gates: derived done-state (computed here) and declared
// policy (a human-authored `.ingestignore`). A raw file is *offered* when
// (a) no wiki page's `raw_source` points at it, or (b) one does but the raw
// file is strictly newer than that page's `git_date`, or `git status
// --porcelain` reports it dirty.
//
// `.ingestignore` is read from the file's own folder only, with **no
// ancestor walk** — the same rule `INGESTION.md` follows, and what keeps a
// hand-written policy file from drifting into a machine-written done-list.
// The parse/append halves live in [ingestignore], shared with `ingest
// --ignore`.
//
// This is the deterministic layer the `wiki-ingest` skill shells out to. The
// interactive half — the per-file `yes / skip / never` prompt — lives in
// `wiki-ingest/SKILL.md` and must run in the *invoking* session: a subagent
// has no channel to the user.
package ingestscan

import (
	"fmt"
	"io/fs"
	"os"
	"path/filepath"

	"github.com/dhague/enchiridion/enchiridion-go/internal/ingestignore"
	"github.com/dhague/enchiridion/enchiridion-go/internal/vault"
	"github.com/dhague/enchiridion/enchiridion-go/internal/vaultgit"
)

// Git is the slice of [vaultgit.Repo] the sweep needs, named as an interface
// so tests can script the git facts rather than standing up a work tree.
//
// Both methods are the *lenient* surface: an absent date ("") and an unknown
// dirty state (false) are read as "fail toward offering", the safe direction.
type Git interface {
	// LastCommitDate returns the last commit date of rel (YYYY-MM-DD), or ""
	// when rel was never committed or root isn't a work tree.
	LastCommitDate(rel string) string
	// PorcelainMentions reports whether rel is modified or untracked.
	PorcelainMentions(rel string) bool
}

// The two reasons a raw file is offered.
const (
	// ReasonNeverIngested: no page's raw_source points at it.
	ReasonNeverIngested = "never-ingested"
	// ReasonChangedSinceIngestion: pages point at it, and it has moved on.
	ReasonChangedSinceIngestion = "changed-since-ingestion"
)

// Candidate is one raw file the sweep wants to offer. RawRel is
// vault-relative.
type Candidate struct {
	// RawRel is the vault-relative path of the raw file.
	RawRel string `json:"raw_rel"`
	// Reason is either [ReasonNeverIngested] (BackPointers empty by
	// construction) or [ReasonChangedSinceIngestion] (BackPointers lists the
	// pointing pages vault-relative — the invoking session passes them to
	// `wiki-ingest` as a reconciliation hint).
	Reason string `json:"reason"`
	// BackPointers lists the pages whose raw_source points at RawRel.
	BackPointers []string `json:"back_pointers"`
}

// Result is the sweep's verdict on one (vault, folder) pair.
//
// Eligible is in walk order. Ignored holds `.ingestignore` matches, reported
// rather than silently dropped so the sweep can say "3 ignored".
type Result struct {
	Eligible []Candidate
	Ignored  []string
}

// skipNames are the raw/ files that are instructions and policy, not content.
var skipNames = map[string]bool{"INGESTION.md": true, ".ingestignore": true}

// WalkRaw returns every file under `root/raw/` (or `root/raw/<folder>`), as
// vault-relative paths in filesystem order.
//
// Skips `INGESTION.md` and `.ingestignore`. A nonexistent folder yields
// nothing rather than an error.
func WalkRaw(root, folder string) ([]string, error) {
	rawRoot := filepath.Join(root, "raw")
	if folder != "" {
		rawRoot = filepath.Join(rawRoot, filepath.FromSlash(folder))
	}
	info, err := os.Stat(rawRoot)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	if !info.IsDir() {
		return nil, nil
	}

	var rels []string
	err = filepath.WalkDir(rawRoot, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || skipNames[d.Name()] {
			return nil
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		rels = append(rels, filepath.ToSlash(rel))
		return nil
	})
	return rels, err
}

// LoadIngestignore reads the `.ingestignore` in folder, if any — this folder
// only, no ancestor walk. An empty slice when absent.
//
// A malformed policy file is an error, not an empty policy: silently reading
// it as "ignore nothing" would offer every file it was meant to withdraw.
func LoadIngestignore(folder string) ([]string, error) {
	path := filepath.Join(folder, ingestignore.Filename)
	text, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	return ingestignore.Parse(string(text))
}

// matchesIngestignore reports whether filename matches any pattern, using
// filepath.Match for glob semantics (a bare filename or a simple glob are the
// only supported shapes).
func matchesIngestignore(filename string, patterns []string) bool {
	for _, pattern := range patterns {
		if ok, _ := filepath.Match(pattern, filename); ok {
			return true
		}
	}
	return false
}

// backPointersByRaw returns `{raw_rel: [page_ref, …]}` for every page with a
// raw_source. Both sides vault-relative.
//
// pagerecord hands back each raw_source target already resolved to
// vault-relative by construction (ADR-0009), so there is no re-resolution
// step to write here.
func backPointersByRaw(pages map[string]vault.PageWithText) map[string][]string {
	out := map[string][]string{}
	for pageRef, page := range pages {
		for _, edge := range page.Record.Edges {
			if edge.Key != "raw_source" {
				continue
			}
			for _, target := range edge.Targets {
				out[target] = append(out[target], pageRef)
			}
		}
	}
	return out
}

// strictlyNewer reports whether rawDate > pageDate (YYYY-MM-DD lexicographic).
//
// An absent page date — never committed, or not a git repo — fails toward
// true, so the file is offered rather than silently skipped.
func strictlyNewer(rawDate, pageDate string) bool {
	if pageDate == "" {
		return true
	}
	if rawDate == "" {
		return false
	}
	return rawDate > pageDate
}

// Scan walks `root/raw/` and returns the sweep's verdict (see the package
// comment for the eligibility rule).
//
// Policy trumps the eligibility signal: a file matching its own folder's
// `.ingestignore` lands in Ignored without being evaluated.
//
// git is injectable for tests; pass nil for the real repository at root. The
// absent-git policy — fail toward offering — is read off [Git.LastCommitDate]
// and [Git.PorcelainMentions], whose lenient defaults this sweep relies on.
func Scan(root, folder string, git Git) (Result, error) {
	v := vault.New(root)
	pages, err := v.PagesWithText()
	if err != nil {
		return Result{}, err
	}
	backPointers := backPointersByRaw(pages)
	if git == nil {
		git = vaultgit.New(root)
	}

	rels, err := WalkRaw(root, folder)
	if err != nil {
		return Result{}, err
	}

	var result Result
	for _, rel := range rels {
		// Own folder, no ancestor walk: a raw/emails/.ingestignore does not
		// govern raw/emails/sub/ — that folder needs its own.
		dir := filepath.Dir(filepath.Join(root, filepath.FromSlash(rel)))
		patterns, err := LoadIngestignore(dir)
		if err != nil {
			return Result{}, fmt.Errorf("%s: %w", rel, err)
		}
		if matchesIngestignore(filepath.Base(rel), patterns) {
			result.Ignored = append(result.Ignored, rel)
			continue
		}

		pointing := backPointers[rel]
		if len(pointing) == 0 {
			result.Eligible = append(result.Eligible, Candidate{RawRel: rel, Reason: ReasonNeverIngested})
			continue
		}

		// Ingested at least once. Offer it again only if it has moved on:
		// dirty working tree, or newer than a back-pointer page.
		if git.PorcelainMentions(rel) {
			result.Eligible = append(result.Eligible, Candidate{RawRel: rel, Reason: ReasonChangedSinceIngestion, BackPointers: pointing})
			continue
		}

		rawDate := git.LastCommitDate(rel)
		for _, pageRel := range pointing {
			if strictlyNewer(rawDate, git.LastCommitDate(pageRel)) {
				result.Eligible = append(result.Eligible, Candidate{RawRel: rel, Reason: ReasonChangedSinceIngestion, BackPointers: pointing})
				break
			}
		}
	}
	return result, nil
}
