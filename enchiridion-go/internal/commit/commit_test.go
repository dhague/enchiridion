package commit

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/dhague/enchiridion/enchiridion-go/internal/vaultgit/vaultgittest"
)

func TestBuildMessage(t *testing.T) {
	got := BuildMessage(Manifest{
		Title:      "Deploy notes",
		Action:     "ingest",
		Created:    []string{"wiki/concepts/a.md"},
		Updated:    []string{"wiki/concepts/b.md"},
		Superseded: []Supersession{{"wiki/sources/old.md", "wiki/sources/new.md"}},
		SourceDate: "2026-03-01",
	})
	want := "ingest: Deploy notes\n\n" +
		"created: wiki/concepts/a.md\n" +
		"updated: wiki/concepts/b.md\n" +
		"superseded: wiki/sources/old.md -> wiki/sources/new.md\n" +
		"source-date: 2026-03-01\n"
	if got != want {
		t.Errorf("BuildMessage =\n%q\nwant\n%q", got, want)
	}
}

func TestBuildMessageOmitsAbsentSections(t *testing.T) {
	got := BuildMessage(Manifest{Title: "Bare", Action: "synthesize"})
	if got != "synthesize: Bare\n\n" {
		t.Errorf("BuildMessage = %q", got)
	}
}

func TestBuildMessageDefaultsAction(t *testing.T) {
	if got := BuildMessage(Manifest{Title: "T"}); !strings.HasPrefix(got, "ingest: T") {
		t.Errorf("BuildMessage with no action = %q, want an 'ingest:' subject", got)
	}
}

func TestStagedPathsDeduplicatesInOrder(t *testing.T) {
	m := Manifest{
		Created:    []string{"a.md", "b.md"},
		Updated:    []string{"b.md", "c.md"},
		Superseded: []Supersession{{"c.md", "d.md"}},
		RawSource:  "raw/doc.md",
	}
	got := strings.Join(m.StagedPaths(), ",")
	want := "a.md,b.md,c.md,d.md,raw/doc.md"
	if got != want {
		t.Errorf("StagedPaths = %q, want %q", got, want)
	}
}

func TestManifestJSONRoundTripsManifestShape(t *testing.T) {
	const src = `{"title":"T","action":"ingest","created":["a.md"],"updated":[],
	  "superseded":[["old.md","new.md"]],"source_date":"2026-03-01","raw_source":"raw/d.md"}`
	var m Manifest
	if err := json.Unmarshal([]byte(src), &m); err != nil {
		t.Fatalf("Unmarshal: %v", err)
	}
	if len(m.Superseded) != 1 || m.Superseded[0].Old != "old.md" || m.Superseded[0].New != "new.md" {
		t.Fatalf("superseded pair = %+v", m.Superseded)
	}
	encoded, err := json.Marshal(m)
	if err != nil {
		t.Fatalf("Marshal: %v", err)
	}
	if !strings.Contains(string(encoded), `"superseded":[["old.md","new.md"]]`) {
		t.Errorf("re-encoded manifest lost the [old, new] array shape: %s", encoded)
	}
}

func TestSupersededMalformedPairIsAnError(t *testing.T) {
	var m Manifest
	if err := json.Unmarshal([]byte(`{"superseded":[["only-one"]]}`), &m); err == nil {
		t.Error("a one-element superseded pair: want an error, got nil")
	}
}

// stagedVault writes pages under a temp root so the chain-of-evidence gate
// has something to read.
func stagedVault(t *testing.T, pages map[string]string) string {
	t.Helper()
	root := t.TempDir()
	for ref, text := range pages {
		path := filepath.Join(root, filepath.FromSlash(ref))
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func TestCommitStagesAndReturnsSHA(t *testing.T) {
	root := stagedVault(t, map[string]string{"wiki/concepts/a.md": "---\ntitle: A\n---\nbody\n"})
	git := &vaultgittest.Fake{}

	sha, err := Commit(root, Manifest{Title: "T", Created: []string{"wiki/concepts/a.md"}}, git)
	if err != nil {
		t.Fatalf("Commit: %v", err)
	}
	if len(sha) != 40 {
		t.Errorf("SHA = %q, want 40 hex chars", sha)
	}
	if strings.Join(git.Added, ",") != "wiki/concepts/a.md" {
		t.Errorf("Added = %v", git.Added)
	}
	if len(git.Messages) != 1 || !strings.HasPrefix(git.Messages[0], "ingest: T") {
		t.Errorf("Messages = %v", git.Messages)
	}
}

func TestCommitRequiresAWorkTree(t *testing.T) {
	git := &vaultgittest.Fake{NotAWorkTree: true}
	if _, err := Commit(t.TempDir(), Manifest{Title: "T"}, git); err == nil {
		t.Error("Commit outside a work tree: want an error, got nil")
	}
	if len(git.Messages) != 0 {
		t.Error("Commit wrote a commit despite not being in a work tree")
	}
}

// The gate is the hard block: a manifest naming a raw artifact with no
// `sources/` stub must fail before anything is staged.
func TestCommitGatesOnChainOfEvidence(t *testing.T) {
	root := stagedVault(t, map[string]string{"wiki/concepts/a.md": "---\ntitle: A\n---\nbody\n"})
	git := &vaultgittest.Fake{}

	_, err := Commit(root, Manifest{
		Title:     "T",
		Created:   []string{"wiki/concepts/a.md"},
		RawSource: "raw/doc.md",
	}, git)
	if !errors.Is(err, ErrGate) {
		t.Fatalf("Commit = %v, want an ErrGate", err)
	}
	if len(git.Added) != 0 || len(git.Messages) != 0 {
		t.Errorf("gate ran after staging: added %v, messages %v", git.Added, git.Messages)
	}
}

func TestCommitPassesGateWithStubAndBackEdges(t *testing.T) {
	root := stagedVault(t, map[string]string{
		"wiki/sources/doc.md": "---\nraw_source: \"[doc.md](../../raw/doc.md)\"\n---\nstub\n",
		"wiki/concepts/a.md":  "---\nsource:\n  - \"[doc.md](../sources/doc.md)\"\n---\nbody\n",
		"raw/doc.md":          "raw\n",
	})
	git := &vaultgittest.Fake{}

	if _, err := Commit(root, Manifest{
		Title:     "T",
		Created:   []string{"wiki/sources/doc.md", "wiki/concepts/a.md"},
		RawSource: "raw/doc.md",
	}, git); err != nil {
		t.Fatalf("Commit: %v", err)
	}
	if !strings.Contains(strings.Join(git.Added, ","), "raw/doc.md") {
		t.Errorf("the raw artifact should be staged alongside its pages: %v", git.Added)
	}
}

// A manifest with no raw artifact (a synthesis save) has no stub to demand.
func TestCommitWithoutRawSourceSkipsTheGate(t *testing.T) {
	root := stagedVault(t, map[string]string{"wiki/synthesis/s.md": "---\ntitle: S\n---\nbody\n"})
	git := &vaultgittest.Fake{}
	if _, err := Commit(root, Manifest{
		Title:   "S",
		Action:  "synthesize",
		Created: []string{"wiki/synthesis/s.md"},
	}, git); err != nil {
		t.Fatalf("Commit: %v", err)
	}
}

func TestCommitWithNoPathsStillCommits(t *testing.T) {
	git := &vaultgittest.Fake{}
	if _, err := Commit(t.TempDir(), Manifest{Title: "empty"}, git); err != nil {
		t.Fatalf("Commit: %v", err)
	}
	if len(git.Added) != 0 {
		t.Errorf("Added = %v, want nothing staged", git.Added)
	}
	if len(git.Messages) != 1 {
		t.Errorf("Messages = %v, want one commit", git.Messages)
	}
}

func TestCommitPropagatesGitFailure(t *testing.T) {
	git := &vaultgittest.Fake{CommitErr: vaultgittest.ErrNoGit}
	if _, err := Commit(t.TempDir(), Manifest{Title: "T"}, git); !errors.Is(err, vaultgittest.ErrNoGit) {
		t.Errorf("Commit = %v, want the underlying git failure", err)
	}
}
