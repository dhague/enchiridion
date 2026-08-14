package initwiki

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/dhague/enchiridion/enchiridion-go/internal/place"
	"github.com/dhague/enchiridion/enchiridion-go/internal/vaultgit"
)

func TestInitScaffoldsTheKindAxedLayout(t *testing.T) {
	root := filepath.Join(t.TempDir(), "vault")

	got, err := Init(root, ModeDedicated, "")
	if err != nil {
		t.Fatalf("Init: %v", err)
	}
	if want, _ := filepath.Abs(root); got != want {
		t.Errorf("Init returned %q, want %q", got, want)
	}

	for _, folder := range place.KindFolders {
		mustExist(t, filepath.Join(root, "wiki", folder, ".gitkeep"))
	}
	mustExist(t, filepath.Join(root, "raw", ".gitkeep"))
	mustExist(t, filepath.Join(root, ".gitignore"))
}

func TestInitGitignoresTheSearchIndex(t *testing.T) {
	// ADR-0006: `.wiki-knowledge/` is never committed. A vault scaffolded
	// without that line would start syncing a SQLite sidecar.
	root := filepath.Join(t.TempDir(), "vault")
	if _, err := Init(root, ModeDedicated, ""); err != nil {
		t.Fatalf("Init: %v", err)
	}
	content := mustRead(t, filepath.Join(root, ".gitignore"))
	for _, line := range []string{"*.rsls", ".claude/wiki-knowledge/sessions/", ".opencode/wiki-knowledge/sessions/", ".wiki-knowledge/"} {
		if !containsLine(content, line) {
			t.Errorf(".gitignore is missing %q; got:\n%s", line, content)
		}
	}
}

func TestInitCommitsTheScaffold(t *testing.T) {
	root := filepath.Join(t.TempDir(), "vault")
	if _, err := Init(root, ModeDedicated, ""); err != nil {
		t.Fatalf("Init: %v", err)
	}
	repo := vaultgit.New(root)
	if !repo.IsWorkTree() {
		t.Fatal("Init left no git work tree behind")
	}
	// The scaffold commit is what makes the vault's git history complete
	// from page one — commit dates are the `git_date` signal search reads.
	if _, err := repo.Commit("should fail: nothing left to commit"); err == nil {
		t.Error("expected the scaffold to already be committed")
	}
}

func TestInitQueryFromAnywhereRegistersThePlugin(t *testing.T) {
	root := filepath.Join(t.TempDir(), "vault")
	pluginRoot := "/somewhere/wiki-plugin"
	if _, err := Init(root, ModeQueryFromAnywhere, pluginRoot); err != nil {
		t.Fatalf("Init: %v", err)
	}

	var settings struct {
		ExtraKnownMarketplaces map[string]struct {
			Source struct {
				Source string `json:"source"`
				Path   string `json:"path"`
			} `json:"source"`
		} `json:"extraKnownMarketplaces"`
		EnabledPlugins map[string]bool `json:"enabledPlugins"`
	}
	raw := mustRead(t, filepath.Join(root, ".claude", "settings.json"))
	if err := json.Unmarshal([]byte(raw), &settings); err != nil {
		t.Fatalf("settings.json is not valid JSON: %v\n%s", err, raw)
	}
	marketplace, ok := settings.ExtraKnownMarketplaces["wiki-knowledge-plugin"]
	if !ok {
		t.Fatalf("settings.json registers no marketplace:\n%s", raw)
	}
	if marketplace.Source.Source != "directory" || marketplace.Source.Path != pluginRoot {
		t.Errorf("marketplace source = %+v, want a directory at %q", marketplace.Source, pluginRoot)
	}
	if !settings.EnabledPlugins["wiki-knowledge@wiki-knowledge-plugin"] {
		t.Errorf("plugin not enabled in:\n%s", raw)
	}
}

func TestInitDedicatedWritesNoSettings(t *testing.T) {
	// Installing a plugin project-scope into someone else's directory isn't
	// this package's job (ADR-0004) — dedicated mode only skips the write.
	root := filepath.Join(t.TempDir(), "vault")
	if _, err := Init(root, ModeDedicated, ""); err != nil {
		t.Fatalf("Init: %v", err)
	}
	if _, err := os.Stat(filepath.Join(root, ".claude", "settings.json")); !os.IsNotExist(err) {
		t.Fatal("dedicated mode wrote a settings.json")
	}
}

func TestInitRefusesToRunTwice(t *testing.T) {
	root := filepath.Join(t.TempDir(), "vault")
	if _, err := Init(root, ModeDedicated, ""); err != nil {
		t.Fatalf("Init: %v", err)
	}
	if _, err := Init(root, ModeDedicated, ""); err == nil {
		t.Fatal("Init ran against a directory that already looks like a vault")
	}
}

func TestInitRefusesADirectoryWithAWikiRootSentinel(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, ".wiki-root"), nil, 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := Init(root, ModeDedicated, ""); err == nil {
		t.Fatal("Init ran against a .wiki-root-marked directory")
	}
}

func TestInitValidatesItsArguments(t *testing.T) {
	tests := []struct {
		name, mode, pluginRoot string
	}{
		{"unknown mode", "sideways", ""},
		{"query-from-anywhere without a plugin root", ModeQueryFromAnywhere, ""},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			root := filepath.Join(t.TempDir(), "vault")
			if _, err := Init(root, tc.mode, tc.pluginRoot); err == nil {
				t.Fatal("expected an error")
			}
			if _, err := os.Stat(root); !os.IsNotExist(err) {
				t.Error("validation failed but the vault directory was still created")
			}
		})
	}
}

func TestIsVault(t *testing.T) {
	root := t.TempDir()
	if IsVault(root) {
		t.Error("an empty directory reported as a vault")
	}
	if err := os.Mkdir(filepath.Join(root, "wiki"), 0o755); err != nil {
		t.Fatal(err)
	}
	if !IsVault(root) {
		t.Error("a directory with wiki/ did not report as a vault")
	}
}

func mustExist(t *testing.T, path string) {
	t.Helper()
	if _, err := os.Stat(path); err != nil {
		t.Errorf("expected %s to exist: %v", path, err)
	}
}

func mustRead(t *testing.T, path string) string {
	t.Helper()
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(content)
}

func containsLine(content, line string) bool {
	return slices.Contains(strings.Split(content, "\n"), line)
}
