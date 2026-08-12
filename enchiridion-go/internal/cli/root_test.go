package cli

import (
	"bytes"
	"strings"
	"testing"

	"github.com/dhague/enchiridion/enchiridion-go/internal/version"
)

func TestVersionCommandPrintsVersion(t *testing.T) {
	version.Version = "v1.2.3"
	t.Cleanup(func() { version.Version = "dev" })

	root := NewRootCommand()
	out := &bytes.Buffer{}
	root.SetOut(out)
	root.SetArgs([]string{"version"})

	if err := root.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}

	if got := strings.TrimSpace(out.String()); got != "v1.2.3" {
		t.Fatalf("got %q, want %q", got, "v1.2.3")
	}
}

func TestRootCommandName(t *testing.T) {
	root := NewRootCommand()
	if root.Use != "enchiridion" {
		t.Fatalf("got %q, want %q", root.Use, "enchiridion")
	}
}
