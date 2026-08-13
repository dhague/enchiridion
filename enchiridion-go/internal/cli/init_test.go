package cli

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestInitPrintsTheResolvedVaultRoot(t *testing.T) {
	// The resolved root is the only thing on stdout, so a caller can
	// capture it — the same contract as `init_wiki.py`.
	parent := t.TempDir()
	root := filepath.Join(parent, "vault")

	cmd := NewRootCommand()
	out := &bytes.Buffer{}
	cmd.SetOut(out)
	cmd.SetErr(out)
	cmd.SetArgs([]string{"init", root, "--mode", "dedicated"})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("execute: %v\n%s", err, out.String())
	}

	want, err := filepath.Abs(root)
	if err != nil {
		t.Fatal(err)
	}
	if got := strings.TrimSpace(out.String()); got != want {
		t.Fatalf("stdout = %q, want %q", got, want)
	}
	if _, err := os.Stat(filepath.Join(root, "wiki", "concepts")); err != nil {
		t.Errorf("vault was not scaffolded: %v", err)
	}
}

func TestInitRequiresAMode(t *testing.T) {
	cmd := NewRootCommand()
	out := &bytes.Buffer{}
	cmd.SetOut(out)
	cmd.SetErr(out)
	cmd.SetArgs([]string{"init", filepath.Join(t.TempDir(), "vault")})
	if err := cmd.Execute(); err == nil {
		t.Fatal("expected --mode to be required")
	}
}

func TestInitSurfacesScaffoldingErrors(t *testing.T) {
	cmd := NewRootCommand()
	out := &bytes.Buffer{}
	cmd.SetOut(out)
	cmd.SetErr(out)
	cmd.SetArgs([]string{"init", filepath.Join(t.TempDir(), "vault"),
		"--mode", "query-from-anywhere"})
	if err := cmd.Execute(); err == nil {
		t.Fatal("expected query-from-anywhere without --plugin-root to fail")
	}
}
