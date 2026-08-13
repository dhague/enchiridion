package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/dhague/enchiridion/enchiridion-go/internal/watch"
)

// runSubcommand executes the root command with args, returning combined
// output.
func runSubcommand(t *testing.T, args ...string) (string, error) {
	t.Helper()
	cmd := NewRootCommand()
	out := &bytes.Buffer{}
	cmd.SetOut(out)
	cmd.SetErr(out)
	cmd.SetArgs(args)
	err := cmd.Execute()
	return out.String(), err
}

// --- commit ------------------------------------------------------------------

func TestCommitPrintsOnlyTheSHA(t *testing.T) {
	root := ingestVault(t, map[string]string{
		"wiki/synthesis/foo.md": "---\ntitle: Foo\n---\nbody\n",
	})
	initRepo(t, root)
	manifest := `{"title":"Synthesize","action":"synthesize","created":["wiki/synthesis/foo.md"]}`

	out, err := runSubcommand(t, "commit", "--manifest", writePlan(t, manifest))
	if err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}
	sha := strings.TrimSpace(out)
	if len(sha) != 40 {
		t.Fatalf("stdout = %q, want a bare 40-char SHA", out)
	}
}

func TestCommitGatesOnChainOfEvidence(t *testing.T) {
	root := ingestVault(t, map[string]string{"raw/doc.md": "raw\n"})
	initRepo(t, root)
	// A raw_source with no source stub page must be gated.
	manifest := `{"title":"Ingest","raw_source":"raw/doc.md","created":["wiki/concepts/x.md"]}`

	if _, err := runSubcommand(t, "commit", "--manifest", writePlan(t, manifest)); err == nil {
		t.Error("manifest naming a raw_source without a stub: want an error, got nil")
	}
}

func TestCommitRequiresManifest(t *testing.T) {
	ingestVault(t, nil)
	if _, err := runSubcommand(t, "commit"); err == nil {
		t.Error("commit without --manifest: want an error, got nil")
	}
}

// --- discover ----------------------------------------------------------------

func TestDiscoverSinglePageMode(t *testing.T) {
	ingestVault(t, map[string]string{
		"wiki/concepts/connection-pooling.md": "---\ntitle: Connection Pooling in Postgres\nsummary: reuse connections\n---\nbody\n",
	})

	out, err := runSubcommand(t, "discover", "--title", "Connection Pooling in Postgres")
	if err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}
	var candidate map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(out)), &candidate); err != nil {
		t.Fatalf("output is not JSON: %v\n%s", err, out)
	}
	if candidate["page_ref"] != "wiki/concepts/connection-pooling.md" {
		t.Errorf("page_ref = %v", candidate["page_ref"])
	}
	if _, ok := candidate["hint"]; !ok {
		t.Errorf("candidate missing hint: %v", candidate)
	}
}

func TestDiscoverPlanModeCarriesPagesAndVocabulary(t *testing.T) {
	ingestVault(t, map[string]string{
		"wiki/concepts/connection-pooling.md": "---\ntitle: Connection Pooling in Postgres\nsummary: reuse connections\ntags:\n  - database\n---\nbody\n",
	})
	plan := `{"title":"draft","pages":[{"op":"create","title":"Connection Pooling in Postgres","frontmatter":{"summary":""}}]}`

	out, err := runSubcommand(t, "discover", "--plan", writePlan(t, plan))
	if err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}
	var payload struct {
		Pages      []map[string]any `json:"pages"`
		Vocabulary []map[string]any `json:"vocabulary"`
	}
	if err := json.Unmarshal([]byte(out), &payload); err != nil {
		t.Fatalf("output is not JSON: %v\n%s", err, out)
	}
	if len(payload.Pages) != 1 || payload.Pages[0]["title"] != "Connection Pooling in Postgres" {
		t.Errorf("pages = %v", payload.Pages)
	}
	found := false
	for _, v := range payload.Vocabulary {
		if v["tag"] == "database" {
			found = true
		}
	}
	if !found {
		t.Errorf("vocabulary missing 'database' tag: %v", payload.Vocabulary)
	}
}

func TestDiscoverTagCountReplacesVocabularyWithPlainText(t *testing.T) {
	ingestVault(t, map[string]string{
		"wiki/concepts/connection-pooling.md": "---\ntitle: Connection Pooling in Postgres\nsummary: s\ntags:\n  - access-management\n---\nbody\n",
	})
	plan := `{"title":"draft","pages":[{"op":"create","title":"Connection Pooling in Postgres","frontmatter":{"summary":""}}]}`

	out, err := runSubcommand(t, "discover", "--plan", writePlan(t, plan), "--tag-count", "access-management, user-provisioning")
	if err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}
	if !strings.Contains(out, "access-management count: 1") {
		t.Errorf("missing existing-tag count: %s", out)
	}
	if !strings.Contains(out, "user-provisioning count: 0") {
		t.Errorf("missing mintable-tag count: %s", out)
	}
}

// --- ingest-scan -------------------------------------------------------------

func TestIngestScanJSON(t *testing.T) {
	ingestVault(t, map[string]string{
		"raw/a.md":          "a\n",
		"raw/.ingestignore": "ignored.md\n",
		"raw/ignored.md":    "x\n",
	})
	out, err := runSubcommand(t, "ingest-scan", "--json")
	if err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}
	var eligible, ignored []string
	for _, line := range strings.Split(out, "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		var rec struct {
			Kind   string `json:"kind"`
			RawRel string `json:"raw_rel"`
		}
		if err := json.Unmarshal([]byte(line), &rec); err != nil {
			t.Fatalf("bad JSON line %q: %v", line, err)
		}
		if rec.Kind == "eligible" {
			eligible = append(eligible, rec.RawRel)
		} else {
			ignored = append(ignored, rec.RawRel)
		}
	}
	if len(eligible) != 1 || eligible[0] != "raw/a.md" {
		t.Errorf("eligible = %v", eligible)
	}
	if len(ignored) != 1 || ignored[0] != "raw/ignored.md" {
		t.Errorf("ignored = %v", ignored)
	}
}

func TestIngestScanTableEmpty(t *testing.T) {
	ingestVault(t, nil)
	out, err := runSubcommand(t, "ingest-scan")
	if err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}
	if !strings.Contains(out, "no eligible files; 0 ignored") {
		t.Errorf("empty-table output = %q", out)
	}
}

func TestIngestScanScopedToOneFolder(t *testing.T) {
	ingestVault(t, map[string]string{
		"raw/a.md":       "a\n",
		"raw/notes/b.md": "b\n",
	})
	out, err := runSubcommand(t, "ingest-scan", "notes")
	if err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}
	if strings.Contains(out, "raw/a.md") || !strings.Contains(out, "raw/notes/b.md") {
		t.Errorf("scoped scan = %q", out)
	}
}

// --- superseded-by -----------------------------------------------------------

func TestSupersededByJSON(t *testing.T) {
	ingestVault(t, map[string]string{
		"wiki/concepts/old.md": "---\ntitle: Old\n---\nbody\n",
		"wiki/concepts/new.md": "---\ntitle: New\nsupersedes:\n  - \"[Old](old.md)\"\n---\nbody\n",
	})
	out, err := runSubcommand(t, "superseded-by", "wiki/concepts/old.md", "--json")
	if err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}
	var res map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(out)), &res); err != nil {
		t.Fatalf("bad JSON: %v\n%s", err, out)
	}
	if res["seed"] != "wiki/concepts/old.md" || res["active"] != "wiki/concepts/new.md" {
		t.Errorf("resolution = %v", res)
	}
}

func TestSupersededByTableMarksCurrent(t *testing.T) {
	ingestVault(t, map[string]string{
		"wiki/concepts/current.md": "---\ntitle: Current\n---\nbody\n",
	})
	out, err := runSubcommand(t, "superseded-by", "wiki/concepts/current.md")
	if err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}
	if !strings.Contains(out, "wiki/concepts/current.md") || !strings.Contains(out, "(current)") {
		t.Errorf("table output = %q", out)
	}
}

// --- save-session ------------------------------------------------------------

func TestSaveSessionEndToEnd(t *testing.T) {
	root := ingestVault(t, nil)
	projDir := t.TempDir()
	stateDir := filepath.Join(projDir, ".claude", "wiki-knowledge", "sessions")
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	transcript := filepath.Join(projDir, "abc123-x.jsonl")
	if err := os.WriteFile(transcript, []byte(
		"{\"type\":\"user\",\"isMeta\":false,\"isSidechain\":false,\"message\":{\"role\":\"user\",\"content\":\"hi\"}}\n"+
			"{\"type\":\"assistant\",\"isMeta\":false,\"isSidechain\":false,\"message\":{\"role\":\"assistant\",\"content\":\"hello\"}}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(stateDir, "abc123-x.json"),
		[]byte(`{"transcript_path":"`+transcript+`"}`), 0o644); err != nil {
		t.Fatal(err)
	}

	t.Setenv("CLAUDE_CODE_SESSION_ID", "abc123-x")
	t.Setenv("CLAUDE_PROJECT_DIR", projDir)

	out, err := runSubcommand(t, "save-session", "--slug", "a slug")
	if err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}
	rel := strings.TrimSpace(out)
	if rel != "raw/conversations/2026-"+rel[19:] && !strings.HasPrefix(rel, "raw/conversations/") {
		t.Errorf("rel = %q", rel)
	}
	if !strings.Contains(rel, "-a-slug-abc123.md") {
		t.Errorf("rel = %q, want slug + short-id in name", rel)
	}
	if _, err := os.Stat(filepath.Join(root, filepath.FromSlash(rel))); err != nil {
		t.Errorf("capture not written: %v", err)
	}
}

// --- tool-call-stats ---------------------------------------------------------

func TestToolCallStatsSummarizes(t *testing.T) {
	projDir := t.TempDir()
	stateDir := filepath.Join(projDir, ".claude", "wiki-knowledge", "sessions")
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	log := filepath.Join(stateDir, "sess1-tool-calls.jsonl")
	if err := os.WriteFile(log, []byte(
		"{\"tool\":\"Bash\",\"prompt_id\":\"p1\"}\n{\"tool\":\"Write\",\"prompt_id\":\"p1\"}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("CLAUDE_CODE_SESSION_ID", "sess1")
	t.Setenv("CLAUDE_PROJECT_DIR", projDir)

	out, err := runSubcommand(t, "tool-call-stats")
	if err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}
	if !strings.Contains(out, "Total tool calls: 2") {
		t.Errorf("output = %q", out)
	}
}

// --- watch -------------------------------------------------------------------

func TestWatchDequeueRemovesEntry(t *testing.T) {
	root := t.TempDir()
	paths := watch.ForRoot(root)
	if err := os.MkdirAll(filepath.Dir(paths.Queue), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(paths.Queue, []byte("raw/a.md\nraw/b.md\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	if _, err := runSubcommand(t, "watch", "--vault", root, "--dequeue", "raw/a.md"); err != nil {
		t.Fatalf("execute: %v", err)
	}
	got, err := watch.ReadQueue(paths.Queue)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0] != "raw/b.md" {
		t.Errorf("queue = %v, want [raw/b.md]", got)
	}
}
