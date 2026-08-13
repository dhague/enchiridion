// Package initwiki scaffolds a brand-new, empty wiki vault: folders, git
// repo, .gitignore, and (for query-from-anywhere mode) the
// plugin-registration settings.json. Ported from
// `wiki-plugin/scripts/init_wiki.py`.
//
// One-time setup, distinct from `wiki-ingest`, which fills a vault that
// already exists — [Init] refuses to run against a directory that already
// looks like one ([IsVault]).
//
// Deployment mode (ADR-0004) is the caller's judgment call, never inferred
// here: [ModeQueryFromAnywhere] writes `.claude/settings.json` registering
// pluginRoot as a local-directory marketplace; [ModeDedicated] skips that
// write, since installing a plugin project-scope into someone else's
// directory isn't this package's job.
package initwiki

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"github.com/dhague/enchiridion/enchiridion-go/internal/place"
	"github.com/dhague/enchiridion/enchiridion-go/internal/vault"
	"github.com/dhague/enchiridion/enchiridion-go/internal/vaultgit"
)

// The two deployment modes of ADR-0004.
const (
	ModeQueryFromAnywhere = "query-from-anywhere"
	ModeDedicated         = "dedicated"
)

// Modes lists the accepted --mode values, for CLI help and validation.
var Modes = []string{ModeQueryFromAnywhere, ModeDedicated}

const gitignore = "*.rsls\n" +
	".claude/wiki-knowledge/sessions/\n" +
	// Search index, gitignored per ADR-0006. Must ALSO be added to Resilio
	// Sync's own ignore list — a gitignore doesn't propagate to the syncer,
	// and a synced SQLite sidecar corrupts.
	".wiki-knowledge/\n"

// IsVault reports whether root already looks like a vault — either vault
// marker being present is enough.
func IsVault(root string) bool {
	return vault.HasMarker(root)
}

func settingsJSON(pluginRoot string) (string, error) {
	settings := map[string]any{
		"extraKnownMarketplaces": map[string]any{
			"wiki-knowledge-plugin": map[string]any{
				"source": map[string]any{"source": "directory", "path": pluginRoot},
			},
		},
		"enabledPlugins": map[string]any{"wiki-knowledge@wiki-knowledge-plugin": true},
	}
	encoded, err := json.MarshalIndent(settings, "", "  ")
	if err != nil {
		return "", err
	}
	return string(encoded) + "\n", nil
}

// Init scaffolds vaultRoot as a new vault and returns the vault root.
//
// mode is [ModeQueryFromAnywhere] (requires pluginRoot, the plugin's install
// directory) or [ModeDedicated] (no settings.json; the caller installs the
// plugin themselves).
//
// git comes from [vaultgit.Repo], the one package that talks to git (#126).
// Where the Python version's absent-git policy was "git missing on PATH is a
// hard failure before any scaffolding", the Go port embeds git, so the
// equivalent failures — an unwritable root, an unconfigured committer
// identity — surface from the git verbs themselves.
func Init(vaultRoot, mode, pluginRoot string) (string, error) {
	switch mode {
	case ModeQueryFromAnywhere:
		if pluginRoot == "" {
			return "", fmt.Errorf("%s mode requires a plugin root", ModeQueryFromAnywhere)
		}
	case ModeDedicated:
	default:
		return "", fmt.Errorf("unknown mode %q; must be one of %v", mode, Modes)
	}

	if IsVault(vaultRoot) {
		return "", fmt.Errorf("%s already looks like a vault (wiki/ or .wiki-root exists)", vaultRoot)
	}

	if err := os.MkdirAll(vaultRoot, 0o755); err != nil {
		return "", err
	}

	for _, folder := range place.KindFolders {
		kindDir := filepath.Join(vaultRoot, "wiki", folder)
		if err := os.MkdirAll(kindDir, 0o755); err != nil {
			return "", err
		}
		if err := touch(filepath.Join(kindDir, ".gitkeep")); err != nil {
			return "", err
		}
	}
	rawDir := filepath.Join(vaultRoot, "raw")
	if err := os.MkdirAll(rawDir, 0o755); err != nil {
		return "", err
	}
	if err := touch(filepath.Join(rawDir, ".gitkeep")); err != nil {
		return "", err
	}

	if err := os.WriteFile(filepath.Join(vaultRoot, ".gitignore"), []byte(gitignore), 0o644); err != nil {
		return "", err
	}

	addPaths := []string{"wiki", ".gitignore", "raw/.gitkeep"}
	if mode == ModeQueryFromAnywhere {
		claudeDir := filepath.Join(vaultRoot, ".claude")
		if err := os.MkdirAll(claudeDir, 0o755); err != nil {
			return "", err
		}
		settings, err := settingsJSON(pluginRoot)
		if err != nil {
			return "", err
		}
		if err := os.WriteFile(filepath.Join(claudeDir, "settings.json"), []byte(settings), 0o644); err != nil {
			return "", err
		}
		addPaths = append(addPaths, ".claude/settings.json")
	}

	repo := vaultgit.New(vaultRoot)
	if !repo.IsWorkTree() {
		if err := repo.Init(); err != nil {
			return "", err
		}
	}
	if err := repo.Add(addPaths...); err != nil {
		return "", err
	}
	if _, err := repo.Commit("Initialize wiki vault"); err != nil {
		return "", err
	}

	return filepath.Abs(vaultRoot)
}

func touch(path string) error {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	return f.Close()
}
