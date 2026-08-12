// Package vault answers where the vault is and enumerates what's inside it.
// Ported from `wiki-plugin/scripts/vault.py`.
//
// This ticket (#150) ports the parts `search`/`init` need: root resolution
// and the `wiki/**` page walk. The mutating half (`write`, `move_page`,
// `rewrite_inbound_links`) lands with `ingest` in #151.
package vault

import (
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

// Markers are the filenames that make a directory a vault root.
var Markers = []string{"wiki", ".wiki-root"}

func hasMarker(dir string) bool {
	for _, marker := range Markers {
		if _, err := os.Stat(filepath.Join(dir, marker)); err == nil {
			return true
		}
	}
	return false
}

// ResolveRoot returns the resolved vault root. See
// docs/adr/0004-deployment-modes-and-vault-root-resolution.md for why.
// Order, highest priority first:
//
//  1. $WIKI_ROOT if set and non-empty — wins always (query-from-anywhere mode).
//  2. else the nearest ancestor of start containing a vault marker: a `wiki/`
//     directory or a `.wiki-root` sentinel file.
//  3. else start (the dedicated-mode default, cwd).
//
// start defaults to cwd when empty, and lookupEnv defaults to os.LookupEnv
// when nil; both are injectable so the resolution logic is testable without
// touching the real process environment.
func ResolveRoot(start string, lookupEnv func(string) (string, bool)) (string, error) {
	if lookupEnv == nil {
		lookupEnv = os.LookupEnv
	}
	if wikiRoot, ok := lookupEnv("WIKI_ROOT"); ok && wikiRoot != "" {
		return resolve(wikiRoot)
	}

	if start == "" {
		cwd, err := os.Getwd()
		if err != nil {
			return "", err
		}
		start = cwd
	}
	startPath, err := resolve(start)
	if err != nil {
		return "", err
	}

	for dir := startPath; ; {
		if hasMarker(dir) {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	return startPath, nil
}

// resolve mirrors Python's `Path(...).resolve()`: absolute, symlinks
// followed. A path that doesn't exist yet still resolves (init scaffolds one
// that doesn't), so a failure to walk symlinks falls back to the absolute
// path.
func resolve(path string) (string, error) {
	abs, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	if real, err := filepath.EvalSymlinks(abs); err == nil {
		return real, nil
	}
	return abs, nil
}

// PageRefs returns every `wiki/**/*.md` path in the vault at root, as
// vault-relative page refs (ADR-0009), sorted. `raw/` is never walked.
func PageRefs(root string) ([]string, error) {
	wikiDir := filepath.Join(root, "wiki")
	var refs []string
	err := filepath.WalkDir(wikiDir, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			// A vault with no `wiki/` yet has no pages — not an error.
			if os.IsNotExist(err) && path == wikiDir {
				return fs.SkipAll
			}
			return err
		}
		if d.IsDir() || !strings.HasSuffix(d.Name(), ".md") {
			return nil
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		refs = append(refs, filepath.ToSlash(rel))
		return nil
	})
	if err != nil {
		return nil, err
	}
	return refs, nil
}
