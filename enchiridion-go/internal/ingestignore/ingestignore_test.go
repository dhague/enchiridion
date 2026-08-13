package ingestignore

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestParse(t *testing.T) {
	patterns, err := Parse("# a comment\n\nliteral.md\n*.tmp  # trailing comment\n   \n")
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	if strings.Join(patterns, ",") != "literal.md,*.tmp" {
		t.Errorf("Parse = %v, want [literal.md *.tmp]", patterns)
	}
}

func TestParseRejectsRicherPatterns(t *testing.T) {
	for _, line := range []string{"sub/dir.md", "!keep.md", "**/deep.md"} {
		if _, err := Parse(line); err == nil {
			t.Errorf("Parse(%q): want an error, got nil", line)
		}
	}
}

func TestAppendCreatesFile(t *testing.T) {
	folder := t.TempDir()
	if err := Append(folder, "doc.md", "ingested before back-pointers were mandatory"); err != nil {
		t.Fatalf("Append: %v", err)
	}
	text := readIgnore(t, folder)
	want := "doc.md  # ingested before back-pointers were mandatory\n"
	if text != want {
		t.Errorf("file = %q, want %q", text, want)
	}
}

func TestAppendIsIdempotent(t *testing.T) {
	folder := t.TempDir()
	for range 3 {
		if err := Append(folder, "doc.md", ""); err != nil {
			t.Fatalf("Append: %v", err)
		}
	}
	if text := readIgnore(t, folder); text != "doc.md\n" {
		t.Errorf("file = %q, want a single entry", text)
	}
}

func TestAppendPreservesExistingContent(t *testing.T) {
	folder := t.TempDir()
	if err := os.WriteFile(filepath.Join(folder, Filename), []byte("# policy\n*.tmp\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := Append(folder, "doc.md", ""); err != nil {
		t.Fatalf("Append: %v", err)
	}
	if text := readIgnore(t, folder); text != "# policy\n*.tmp\ndoc.md\n" {
		t.Errorf("file = %q", text)
	}
}

// The folder always exists in a real call — the file being withdrawn is
// sitting in it. A missing one means a mistyped path, and must not be
// papered over by creating it.
func TestAppendRefusesMissingFolder(t *testing.T) {
	folder := filepath.Join(t.TempDir(), "emials")
	if err := Append(folder, "doc.eml", ""); err == nil {
		t.Error("Append into a folder that doesn't exist: want an error, got nil")
	}
	if _, err := os.Stat(folder); err == nil {
		t.Error("Append created the mistyped folder")
	}
}

func readIgnore(t *testing.T, folder string) string {
	t.Helper()
	text, err := os.ReadFile(filepath.Join(folder, Filename))
	if err != nil {
		t.Fatal(err)
	}
	return string(text)
}
