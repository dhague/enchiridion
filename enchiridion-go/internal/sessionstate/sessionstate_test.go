package sessionstate

import (
	"os"
	"path/filepath"
	"testing"
)

func env(m map[string]string) func(string) (string, bool) {
	return func(key string) (string, bool) {
		v, ok := m[key]
		return v, ok
	}
}

func TestWriteThenReadRoundTrips(t *testing.T) {
	dir := t.TempDir()
	if err := WriteTranscriptPath("session-a", "/path/to/a.jsonl", dir); err != nil {
		t.Fatal(err)
	}
	got, ok := ReadTranscriptPath("session-a", dir)
	if !ok || got != "/path/to/a.jsonl" {
		t.Fatalf("got (%q, %v), want (/path/to/a.jsonl, true)", got, ok)
	}
}

func TestReadReturnsFalseForUnknownSession(t *testing.T) {
	if _, ok := ReadTranscriptPath("never-written", t.TempDir()); ok {
		t.Fatal("unknown session reported as present")
	}
}

func TestSessionsAreIsolated(t *testing.T) {
	dir := t.TempDir()
	if err := WriteTranscriptPath("session-a", "/path/to/a.jsonl", dir); err != nil {
		t.Fatal(err)
	}
	if err := WriteTranscriptPath("session-b", "/path/to/b.jsonl", dir); err != nil {
		t.Fatal(err)
	}
	if got, _ := ReadTranscriptPath("session-a", dir); got != "/path/to/a.jsonl" {
		t.Errorf("session-a = %q", got)
	}
	if got, _ := ReadTranscriptPath("session-b", dir); got != "/path/to/b.jsonl" {
		t.Errorf("session-b = %q", got)
	}
}

func TestWriteOverwritesPreviousValue(t *testing.T) {
	dir := t.TempDir()
	if err := WriteTranscriptPath("session-a", "/old.jsonl", dir); err != nil {
		t.Fatal(err)
	}
	if err := WriteTranscriptPath("session-a", "/new.jsonl", dir); err != nil {
		t.Fatal(err)
	}
	if got, _ := ReadTranscriptPath("session-a", dir); got != "/new.jsonl" {
		t.Errorf("got %q, want /new.jsonl", got)
	}
}

func TestReadReturnsFalseForCorruptedState(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "session-a.json"), []byte("not json"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, ok := ReadTranscriptPath("session-a", dir); ok {
		t.Fatal("corrupted state reported as present")
	}
}

func TestWriteCreatesStateDirIfMissing(t *testing.T) {
	nested := filepath.Join(t.TempDir(), "nested", "sessions")
	if err := WriteTranscriptPath("session-a", "/a.jsonl", nested); err != nil {
		t.Fatal(err)
	}
	if got, _ := ReadTranscriptPath("session-a", nested); got != "/a.jsonl" {
		t.Errorf("got %q", got)
	}
}

func TestSessionsDirUnderGivenRoot(t *testing.T) {
	got := SessionsDir("/some/project", "", env(nil))
	want := filepath.Join("/some/project", ".claude", "wiki-knowledge", "sessions")
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestSessionsDirDefaultsToCwd(t *testing.T) {
	dir := t.TempDir()
	got := SessionsDir("", dir, env(nil))
	want := filepath.Join(dir, ".claude", "wiki-knowledge", "sessions")
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestSessionsDirWalksUpForClaudeAncestor(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, ".claude"), 0o755); err != nil {
		t.Fatal(err)
	}
	subdir := filepath.Join(root, "src", "deep")
	if err := os.MkdirAll(subdir, 0o755); err != nil {
		t.Fatal(err)
	}
	got := SessionsDir("", subdir, env(nil))
	want := filepath.Join(root, ".claude", "wiki-knowledge", "sessions")
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestSessionsDirProjectDirOverridesWalkup(t *testing.T) {
	stateRoot := filepath.Join(t.TempDir(), "elsewhere")
	if err := os.MkdirAll(stateRoot, 0o755); err != nil {
		t.Fatal(err)
	}
	cwd := t.TempDir()
	got := SessionsDir("", cwd, env(map[string]string{"CLAUDE_PROJECT_DIR": stateRoot}))
	want := filepath.Join(stateRoot, ".claude", "wiki-knowledge", "sessions")
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestSessionsDirRootInjectionSkipsEnvAndCwd(t *testing.T) {
	got := SessionsDir("/explicit", t.TempDir(), env(map[string]string{"CLAUDE_PROJECT_DIR": "/should/be/ignored"}))
	want := filepath.Join("/explicit", ".claude", "wiki-knowledge", "sessions")
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}
