package cli

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/dhague/enchiridion/enchiridion-go/internal/vaultgit"
)

// ingestVault scaffolds a real git-backed vault (the ingest subcommand
// commits through go-git, so the work tree has to be real) plus the given
// files, and points $WIKI_ROOT at it for the duration of the test.
func ingestVault(t *testing.T, files map[string]string) string {
	t.Helper()
	root := t.TempDir()
	for ref, text := range files {
		path := filepath.Join(root, filepath.FromSlash(ref))
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.MkdirAll(filepath.Join(root, "wiki"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("WIKI_ROOT", root)
	// Cleared so a test run inside a real hooked Claude Code session doesn't
	// pick up that session's tool-call log and print its cost summary. Tests
	// that want the summary set it back.
	t.Setenv("CLAUDE_CODE_SESSION_ID", "")
	return root
}

// initRepo makes root a git work tree, so the commit at the end of a real
// ingest run has somewhere to land.
func initRepo(t *testing.T, root string) {
	t.Helper()
	if err := vaultgit.New(root).Init(); err != nil {
		t.Fatal(err)
	}
}

// writePlan writes an IngestPlan JSON file outside the vault and returns its
// path.
func writePlan(t *testing.T, src string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "plan.json")
	if err := os.WriteFile(path, []byte(src), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

// runIngest executes the ingest subcommand with args, returning its combined
// output.
func runIngest(t *testing.T, args ...string) (string, error) {
	t.Helper()
	cmd := NewRootCommand()
	out := &bytes.Buffer{}
	cmd.SetOut(out)
	cmd.SetErr(out)
	cmd.SetArgs(append([]string{"ingest"}, args...))
	err := cmd.Execute()
	return out.String(), err
}

const cliPlan = `{"title":"Deploy notes","raw":"raw/doc.md","pages":[
  {"op":"create","title":"Doc","kind":"source","body":"stub\n","frontmatter":{"raw_source":true}},
  {"op":"create","title":"Prepared Statements","kind":"concept","body":"page\n",
   "edges":{"source":["wiki/sources/doc.md"]}}]}`

func TestIngestDryRunWritesNothing(t *testing.T) {
	root := ingestVault(t, map[string]string{"raw/doc.md": "raw\n"})
	out, err := runIngest(t, "--plan", writePlan(t, cliPlan), "--dry-run")
	if err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}
	for _, want := range []string{
		"ingest: Deploy notes",
		"create wiki/sources/doc.md",
		"create wiki/concepts/prepared-statements.md",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("dry-run output missing %q:\n%s", want, out)
		}
	}
	if _, err := os.Stat(filepath.Join(root, "wiki", "sources", "doc.md")); err == nil {
		t.Error("--dry-run wrote a page")
	}
}

// The commit SHA is the only thing on stdout, so a caller can capture it —
// the same contract as `ingest.py`.
func TestIngestPrintsOnlyTheCommitSHA(t *testing.T) {
	root := ingestVault(t, map[string]string{"raw/doc.md": "raw\n"})
	initRepo(t, root)

	out, err := runIngest(t, "--plan", writePlan(t, cliPlan))
	if err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}
	sha := strings.TrimSpace(out)
	if len(sha) != 40 {
		t.Fatalf("stdout = %q, want a bare 40-char SHA", out)
	}
	if _, err := os.Stat(filepath.Join(root, "wiki", "concepts", "prepared-statements.md")); err != nil {
		t.Errorf("page was not written: %v", err)
	}
}

// A raw filename with a space and parens is the shape the encoding rules
// exist for, and it is also the shape a stager can mistake for a glob or
// split on whitespace. This checks the whole path end to end: the raw
// artifact lands in the same commit as the pages it produced, under its
// verbatim name.
func TestIngestStagesNewRawFileWithAwkwardName(t *testing.T) {
	root := ingestVault(t, map[string]string{"raw/deploy notes (v2).md": "raw\n"})
	initRepo(t, root)

	plan := `{"title":"D","raw":"raw/deploy notes (v2).md","pages":[
	  {"op":"create","title":"Deploy Notes (v2)","kind":"source","body":"stub\n",
	   "frontmatter":{"raw_source":true}}]}`
	if out, err := runIngest(t, "--plan", writePlan(t, plan)); err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}

	// Ask real git what the commit contains, rather than trusting the same
	// library that wrote it.
	listing, err := exec.Command("git", "-C", root, "show", "--name-only", "--format=", "HEAD").Output()
	if err != nil {
		t.Skipf("no system git to cross-check with: %v", err)
	}
	files := string(listing)
	for _, want := range []string{"raw/deploy notes (v2).md", "wiki/sources/deploy-notes-v2.md"} {
		if !strings.Contains(files, want) {
			t.Errorf("commit is missing %q; it contains:\n%s", want, files)
		}
	}
}

// Git is a hard dependency: a vault that is not a work tree fails rather than
// silently skipping the commit.
func TestIngestFailsOutsideAWorkTree(t *testing.T) {
	ingestVault(t, map[string]string{"raw/doc.md": "raw\n"})
	if _, err := runIngest(t, "--plan", writePlan(t, cliPlan)); err == nil {
		t.Error("ingest into a non-git vault: want an error, got nil")
	}
}

func TestIngestReportsValidationErrors(t *testing.T) {
	ingestVault(t, nil)
	out, err := runIngest(t, "--plan", writePlan(t, `{"pages":[{"op":"create"}]}`))
	if err == nil {
		t.Fatalf("expected validation to fail:\n%s", out)
	}
	if !strings.Contains(err.Error(), "plan.title is required") {
		t.Errorf("error = %v, want the shape errors listed", err)
	}
}

func TestIngestMissingPlanFile(t *testing.T) {
	ingestVault(t, nil)
	if _, err := runIngest(t, "--plan", filepath.Join(t.TempDir(), "absent.json")); err == nil {
		t.Error("a missing plan file: want an error, got nil")
	}
}

func TestIngestRequiresPlanOrIgnore(t *testing.T) {
	ingestVault(t, nil)
	if _, err := runIngest(t); err == nil {
		t.Error("neither --plan nor --ignore: want an error, got nil")
	}
}

func TestIngestRejectsPlanAndIgnoreTogether(t *testing.T) {
	ingestVault(t, nil)
	if _, err := runIngest(t, "--plan", writePlan(t, cliPlan), "--ignore", "raw/doc.md"); err == nil {
		t.Error("--plan with --ignore: want an error, got nil")
	}
}

func TestIngestRejectsDryRunWithIgnore(t *testing.T) {
	ingestVault(t, nil)
	if _, err := runIngest(t, "--ignore", "raw/doc.md", "--dry-run"); err == nil {
		t.Error("--dry-run with --ignore: want an error, got nil")
	}
}

func TestIngestIgnoreAppendsToTheFilesOwnFolder(t *testing.T) {
	root := ingestVault(t, map[string]string{"raw/emails/note.eml": "x\n"})
	if out, err := runIngest(t, "--ignore", "raw/emails/note.eml",
		"--ignore-comment", "ingested before back-pointers were mandatory"); err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}
	text, err := os.ReadFile(filepath.Join(root, "raw", "emails", ".ingestignore"))
	if err != nil {
		t.Fatal(err)
	}
	want := "note.eml  # ingested before back-pointers were mandatory\n"
	if string(text) != want {
		t.Errorf(".ingestignore = %q, want %q", text, want)
	}
}

func TestIngestIgnoreAtRawRoot(t *testing.T) {
	root := ingestVault(t, map[string]string{"raw/doc.md": "x\n"})
	if out, err := runIngest(t, "--ignore", "raw/doc.md"); err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}
	text, err := os.ReadFile(filepath.Join(root, "raw", ".ingestignore"))
	if err != nil {
		t.Fatal(err)
	}
	if string(text) != "doc.md\n" {
		t.Errorf(".ingestignore = %q, want %q", text, "doc.md\n")
	}
}

func TestIngestIgnoreRejectsPathOutsideRaw(t *testing.T) {
	ingestVault(t, nil)
	for _, rel := range []string{"wiki/concepts/a.md", "doc.md", "raw"} {
		if _, err := runIngest(t, "--ignore", rel); err == nil {
			t.Errorf("--ignore %q: want an error, got nil", rel)
		}
	}
}

// #151 deferred the post-commit cost summary to #153, since it reads a log
// only the hooks write. With the hooks ported, ingest reports it again.
func TestIngestPrintsTheToolCallSummaryAfterTheSHA(t *testing.T) {
	root := ingestVault(t, map[string]string{"raw/doc.md": "raw\n"})
	initRepo(t, root)
	project := t.TempDir()
	t.Setenv("CLAUDE_PROJECT_DIR", project)
	t.Setenv("CLAUDE_CODE_SESSION_ID", "abc123")
	logPath := filepath.Join(project, ".claude", "wiki-knowledge", "sessions", "abc123-tool-calls.jsonl")
	if err := os.MkdirAll(filepath.Dir(logPath), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(logPath, []byte(`{"tool":"Bash","prompt_id":"pr_1"}`+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	out, err := runIngest(t, "--plan", writePlan(t, cliPlan))
	if err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}

	lines := strings.SplitN(strings.TrimSpace(out), "\n", 2)
	if len(strings.TrimSpace(lines[0])) != 40 {
		t.Fatalf("first line = %q, want the commit SHA", lines[0])
	}
	if len(lines) < 2 || !strings.Contains(lines[1], "Total tool calls: 1") {
		t.Errorf("summary missing from output:\n%s", out)
	}
}

// No log — an ingest run outside a hooked session — prints the SHA and
// nothing else, so capturing stdout still works.
func TestIngestOmitsTheSummaryWhenNoLogExists(t *testing.T) {
	root := ingestVault(t, map[string]string{"raw/doc.md": "raw\n"})
	initRepo(t, root)
	t.Setenv("CLAUDE_PROJECT_DIR", t.TempDir())
	t.Setenv("CLAUDE_CODE_SESSION_ID", "abc123")

	out, err := runIngest(t, "--plan", writePlan(t, cliPlan))
	if err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}
	if len(strings.TrimSpace(out)) != 40 {
		t.Errorf("stdout = %q, want a bare 40-char SHA", out)
	}
}
