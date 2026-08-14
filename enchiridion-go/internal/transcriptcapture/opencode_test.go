package transcriptcapture

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

// --- helpers: building an OpenCode-shaped export ------------------------------

func textPart(text string) map[string]any {
	return map[string]any{"type": "text", "text": text}
}

func message(role string, parts ...map[string]any) map[string]any {
	return map[string]any{"info": map[string]any{"role": role}, "parts": parts}
}

func exportJSON(messages ...map[string]any) []byte {
	b, _ := json.Marshal(map[string]any{
		"info":     map[string]any{"id": "ses_abc123"},
		"messages": messages,
	})
	return b
}

func twoTurnExport() []byte {
	return exportJSON(
		message("user", textPart("hi there")),
		message("assistant", textPart("hello back")),
	)
}

// exporterFor returns an Exporter yielding data, recording the ids it saw.
func exporterFor(data []byte, seen *[]string) Exporter {
	return func(sessionID string) ([]byte, error) {
		if seen != nil {
			*seen = append(*seen, sessionID)
		}
		return data, nil
	}
}

// --- NormalizeExport ----------------------------------------------------------

func TestNormalizeExport(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name   string
		export []byte
		want   []Turn
	}{
		{
			name:   "extracts text parts in order",
			export: twoTurnExport(),
			want:   []Turn{{"user", "hi there"}, {"assistant", "hello back"}},
		},
		{
			name: "keeps only text parts",
			export: exportJSON(message("assistant",
				map[string]any{"type": "step-start"},
				map[string]any{"type": "reasoning", "text": "thinking out loud"},
				map[string]any{"type": "tool", "tool": "bash"},
				textPart("the answer"),
				map[string]any{"type": "step-finish"},
			)),
			want: []Turn{{"assistant", "the answer"}},
		},
		{
			name: "skips non user/assistant roles",
			export: exportJSON(
				message("system", textPart("system preamble")),
				message("tool", textPart("tool result text")),
				message("user", textPart("real question")),
			),
			want: []Turn{{"user", "real question"}},
		},
		{
			name: "skips messages with no text parts",
			export: exportJSON(
				message("assistant", map[string]any{"type": "tool", "tool": "bash"}),
				message("user", textPart("still here")),
			),
			want: []Turn{{"user", "still here"}},
		},
		{
			name:   "joins multiple text parts with a blank line",
			export: exportJSON(message("assistant", textPart("first"), textPart("second"))),
			want:   []Turn{{"assistant", "first\n\nsecond"}},
		},
		{
			name: "skips blank text parts",
			export: exportJSON(
				message("user", textPart("   "), textPart("the question")),
				message("assistant", textPart("")),
			),
			want: []Turn{{"user", "the question"}},
		},
		{
			name:   "tolerates malformed messages",
			export: []byte(`{"messages":[{},{"info":{"role":"user"}},{"parts":[{"type":"text","text":"no info"}]},{"info":"not an object"},7]}`),
			want:   nil,
		},
		{
			name:   "tolerates a missing messages key",
			export: []byte(`{"info":{}}`),
			want:   nil,
		},
		{
			name:   "tolerates malformed parts",
			export: []byte(`{"messages":[{"info":{"role":"user"},"parts":["nope",3,{"type":"text","text":"kept"}]}]}`),
			want:   []Turn{{"user", "kept"}},
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			got, err := NormalizeExport(tc.export)
			if err != nil {
				t.Fatalf("NormalizeExport: %v", err)
			}
			if len(got) != len(tc.want) {
				t.Fatalf("got %+v, want %+v", got, tc.want)
			}
			for i := range got {
				if got[i] != tc.want[i] {
					t.Errorf("turn %d = %+v, want %+v", i, got[i], tc.want[i])
				}
			}
		})
	}
}

func TestNormalizeExportRejectsNonObject(t *testing.T) {
	t.Parallel()
	for _, raw := range []string{"not json", "[]", "null"} {
		if _, err := NormalizeExport([]byte(raw)); err == nil {
			t.Errorf("NormalizeExport(%q): want error, got nil", raw)
		}
	}
}

// --- EncodeTurns --------------------------------------------------------------

func TestEncodeTurnsProducesClaudeCodeShapedJSONL(t *testing.T) {
	t.Parallel()
	lines := EncodeTurns([]Turn{{"user", "question"}, {"assistant", "answer"}})
	if len(lines) != 2 {
		t.Fatalf("got %d lines, want 2", len(lines))
	}
	var got []map[string]any
	for _, line := range lines {
		var m map[string]any
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			t.Fatalf("line %q: %v", line, err)
		}
		got = append(got, m)
	}
	for i, want := range []struct{ typ, role, content string }{
		{"user", "user", "question"},
		{"assistant", "assistant", "answer"},
	} {
		if got[i]["type"] != want.typ {
			t.Errorf("line %d type = %v, want %q", i, got[i]["type"], want.typ)
		}
		if got[i]["isMeta"] != false || got[i]["isSidechain"] != false {
			t.Errorf("line %d should be neither meta nor sidechain: %+v", i, got[i])
		}
		msg, _ := got[i]["message"].(map[string]any)
		if msg["role"] != want.role || msg["content"] != want.content {
			t.Errorf("line %d message = %+v, want role %q content %q", i, msg, want.role, want.content)
		}
	}
}

func TestEncodeTurnsFeedsTranscriptToPageUnchanged(t *testing.T) {
	t.Parallel()
	lines := EncodeTurns([]Turn{{"user", "question"}, {"assistant", "answer"}})
	filename, markdown, err := TranscriptToPage(
		lines, "ses_abc123", time.Date(2026, 8, 9, 15, 30, 0, 0, time.UTC),
		"", "User", "Claude", 2,
	)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(filename, "-ses_abc123.md") {
		t.Errorf("filename = %q", filename)
	}
	if !strings.Contains(markdown, "question") || !strings.Contains(markdown, "answer") {
		t.Errorf("markdown missing turns:\n%s", markdown)
	}
}

// --- FindOpenCodeSessionID ----------------------------------------------------

// openCodeState writes the session-tracker record the plugin would write.
func openCodeState(t *testing.T, root, sessionID string) string {
	t.Helper()
	stateDir := filepath.Join(root, ".opencode", "wiki-knowledge", "sessions")
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	data, _ := json.Marshal(map[string]string{"session_id": sessionID})
	if err := os.WriteFile(filepath.Join(stateDir, sessionID+".json"), data, 0o644); err != nil {
		t.Fatal(err)
	}
	return stateDir
}

func TestFindOpenCodeSessionIDMissingEnvVar(t *testing.T) {
	t.Parallel()
	id, errMsg := FindOpenCodeSessionID(t.TempDir(), env(map[string]string{}))
	if id != "" {
		t.Errorf("id = %q, want empty", id)
	}
	if !strings.Contains(errMsg, "OPENCODE_SESSION_ID") {
		t.Errorf("errMsg = %q", errMsg)
	}
}

func TestFindOpenCodeSessionIDStateDirNotLocated(t *testing.T) {
	t.Parallel()
	cwd := t.TempDir()
	id, errMsg := FindOpenCodeSessionID(cwd, env(map[string]string{"OPENCODE_SESSION_ID": "ses_abc123"}))
	if id != "" {
		t.Errorf("id = %q, want empty", id)
	}
	if !strings.Contains(strings.ToLower(errMsg), "state") || !strings.Contains(errMsg, "session-tracker") {
		t.Errorf("errMsg = %q", errMsg)
	}
}

func TestFindOpenCodeSessionIDLocatedButNoEntry(t *testing.T) {
	t.Parallel()
	cwd := t.TempDir()
	stateDir := filepath.Join(cwd, ".opencode", "wiki-knowledge", "sessions")
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	id, errMsg := FindOpenCodeSessionID(cwd, env(map[string]string{"OPENCODE_SESSION_ID": "ses_abc123"}))
	if id != "" {
		t.Errorf("id = %q, want empty", id)
	}
	if !strings.Contains(errMsg, "ses_abc123") || !strings.Contains(errMsg, stateDir) {
		t.Errorf("errMsg = %q", errMsg)
	}
}

func TestFindOpenCodeSessionIDReturnsIDWhenStatePresent(t *testing.T) {
	t.Parallel()
	cwd := t.TempDir()
	openCodeState(t, cwd, "ses_abc123")
	id, errMsg := FindOpenCodeSessionID(cwd, env(map[string]string{"OPENCODE_SESSION_ID": "ses_abc123"}))
	if errMsg != "" {
		t.Fatalf("unexpected error: %s", errMsg)
	}
	if id != "ses_abc123" {
		t.Errorf("id = %q", id)
	}
}

func TestFindOpenCodeSessionIDWalksUpForOpenCodeAncestor(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	openCodeState(t, root, "ses_abc123")
	subdir := filepath.Join(root, "src", "deep")
	if err := os.MkdirAll(subdir, 0o755); err != nil {
		t.Fatal(err)
	}
	id, errMsg := FindOpenCodeSessionID(subdir, env(map[string]string{"OPENCODE_SESSION_ID": "ses_abc123"}))
	if errMsg != "" {
		t.Fatalf("unexpected error: %s", errMsg)
	}
	if id != "ses_abc123" {
		t.Errorf("id = %q", id)
	}
}

func TestFindOpenCodeSessionIDCorruptStateFileCountsAsUntracked(t *testing.T) {
	t.Parallel()
	cwd := t.TempDir()
	stateDir := filepath.Join(cwd, ".opencode", "wiki-knowledge", "sessions")
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(stateDir, "ses_abc123.json"), []byte("not json"), 0o644); err != nil {
		t.Fatal(err)
	}
	id, errMsg := FindOpenCodeSessionID(cwd, env(map[string]string{"OPENCODE_SESSION_ID": "ses_abc123"}))
	if id != "" || errMsg == "" {
		t.Errorf("id = %q, errMsg = %q", id, errMsg)
	}
}

func TestFindOpenCodeSessionIDStateNamingADifferentSessionIsUntracked(t *testing.T) {
	t.Parallel()
	cwd := t.TempDir()
	stateDir := filepath.Join(cwd, ".opencode", "wiki-knowledge", "sessions")
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	data, _ := json.Marshal(map[string]string{"session_id": "ses_other"})
	if err := os.WriteFile(filepath.Join(stateDir, "ses_abc123.json"), data, 0o644); err != nil {
		t.Fatal(err)
	}
	id, errMsg := FindOpenCodeSessionID(cwd, env(map[string]string{"OPENCODE_SESSION_ID": "ses_abc123"}))
	if id != "" || errMsg == "" {
		t.Errorf("id = %q, errMsg = %q", id, errMsg)
	}
}

// --- ExportTranscript ---------------------------------------------------------

func TestExportTranscriptErrorsWhenOpenCodeAbsent(t *testing.T) {
	t.Parallel()
	_, err := ExportTranscript("ses_abc123", "definitely-not-a-real-binary-xyzzy")
	if err == nil {
		t.Fatal("want error, got nil")
	}
	if !strings.Contains(err.Error(), "definitely-not-a-real-binary-xyzzy") {
		t.Errorf("err = %v", err)
	}
}

// openCodeStub writes an executable `opencode` stand-in and returns its path.
// The stub is a shell script, so the tests using it are POSIX-only.
func openCodeStub(t *testing.T, script string) string {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("stub is a POSIX shell script")
	}
	stub := filepath.Join(t.TempDir(), "opencode")
	if err := os.WriteFile(stub, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	return stub
}

// TestExportTranscriptRunsTheCommand guards the observed 1.18.15 truncation
// bug: `opencode export` truncates its JSON when stdout is a pipe, so the
// payload here is deliberately larger than the 64KB pipe buffer and must come
// back whole.
func TestExportTranscriptRunsTheCommand(t *testing.T) {
	payload := `{"messages":[],"pad":"` + strings.Repeat("x", 200_000) + `"}`
	stub := openCodeStub(t, "#!/bin/sh\ncat <<'EOF'\n"+payload+"\nEOF\n")

	out, err := ExportTranscript("ses_abc123", stub)
	if err != nil {
		t.Fatal(err)
	}
	if len(out) < len(payload) {
		t.Errorf("truncated output: got %d bytes, want >= %d", len(out), len(payload))
	}
	if !strings.Contains(string(out), `"messages"`) {
		t.Errorf("unexpected output head: %.80s", out)
	}
}

func TestExportTranscriptErrorsOnNonZeroExit(t *testing.T) {
	stub := openCodeStub(t, "#!/bin/sh\necho boom >&2\nexit 1\n")
	_, err := ExportTranscript("ses_abc123", stub)
	if err == nil {
		t.Fatal("want error, got nil")
	}
	if !strings.Contains(err.Error(), "boom") {
		t.Errorf("err = %v", err)
	}
}

func TestExportTranscriptPassesTheSessionID(t *testing.T) {
	// The stub echoes its own argv so the test can assert `export <id>`.
	stub := openCodeStub(t, "#!/bin/sh\nprintf '{\"argv\":\"%s %s\"}' \"$1\" \"$2\"\n")
	out, err := ExportTranscript("ses_abc123", stub)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(out), `"export ses_abc123"`) {
		t.Errorf("argv = %s", out)
	}
}

// --- CaptureOpenCodeSession ---------------------------------------------------

// runnableOpenCodeSession sets up tracker state and a vault, deliberately
// complete: a "nothing was written" assertion only means something if the save
// would otherwise have succeeded.
func runnableOpenCodeSession(t *testing.T) (root, cwd string) {
	t.Helper()
	cwd = t.TempDir()
	openCodeState(t, cwd, "ses_abc123")
	root = t.TempDir()
	return root, cwd
}

func TestCaptureOpenCodeSessionWritesMarkdownToRawConversations(t *testing.T) {
	t.Parallel()
	root, cwd := runnableOpenCodeSession(t)
	var seen []string

	rel, err := CaptureOpenCodeSession(root, "", cwd,
		env(map[string]string{"OPENCODE_SESSION_ID": "ses_abc123"}),
		time.Date(2026, 8, 9, 15, 30, 0, 0, time.UTC),
		exporterFor(twoTurnExport(), &seen))
	if err != nil {
		t.Fatal(err)
	}
	if want := "raw/conversations/2026-08-09-1530-ses_abc123.md"; rel != want {
		t.Fatalf("rel = %q, want %q", rel, want)
	}
	if len(seen) != 1 || seen[0] != "ses_abc123" {
		t.Errorf("exporter saw %v", seen)
	}
	body, err := os.ReadFile(filepath.Join(root, rel))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(body), "hi there") || !strings.Contains(string(body), "hello back") {
		t.Errorf("capture missing turns:\n%s", body)
	}
	// The rendering seam is shared verbatim, so an OpenCode capture inherits
	// the generic source line.
	if !strings.Contains(string(body), "Source:** Claude Code session transcript") {
		t.Errorf("capture missing the shared source line:\n%s", body)
	}
}

func TestCaptureOpenCodeSessionCarriesTheSlug(t *testing.T) {
	t.Parallel()
	root, cwd := runnableOpenCodeSession(t)
	rel, err := CaptureOpenCodeSession(root, "opencode port", cwd,
		env(map[string]string{"OPENCODE_SESSION_ID": "ses_abc123"}),
		time.Date(2026, 8, 9, 15, 30, 0, 0, time.UTC),
		exporterFor(twoTurnExport(), nil))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(rel, "-opencode-port-ses_abc123.md") {
		t.Errorf("rel = %q", rel)
	}
}

func TestCaptureOpenCodeSessionTooFewTurnsWritesNothing(t *testing.T) {
	t.Parallel()
	root, cwd := runnableOpenCodeSession(t)
	oneTurn := exportJSON(message("user", textPart("hi there")))

	_, err := CaptureOpenCodeSession(root, "", cwd,
		env(map[string]string{"OPENCODE_SESSION_ID": "ses_abc123"}),
		time.Time{}, exporterFor(oneTurn, nil))
	if err == nil {
		t.Fatal("want error, got nil")
	}
	var captureErr CaptureError
	if !errors.As(err, &captureErr) {
		t.Errorf("err = %T (%v), want CaptureError", err, err)
	}
	if _, statErr := os.Stat(filepath.Join(root, "raw", "conversations")); !os.IsNotExist(statErr) {
		t.Errorf("nothing should have been written: %v", statErr)
	}
}

func TestCaptureOpenCodeSessionSurfacesExportFailure(t *testing.T) {
	t.Parallel()
	root, cwd := runnableOpenCodeSession(t)
	failing := func(string) ([]byte, error) { return nil, errors.New("boom") }

	_, err := CaptureOpenCodeSession(root, "", cwd,
		env(map[string]string{"OPENCODE_SESSION_ID": "ses_abc123"}),
		time.Time{}, failing)
	if err == nil || !strings.Contains(err.Error(), "boom") {
		t.Fatalf("err = %v", err)
	}
}

func TestCaptureOpenCodeSessionResaveRewritesTheBoundName(t *testing.T) {
	t.Parallel()
	root, cwd := runnableOpenCodeSession(t)
	lookup := env(map[string]string{"OPENCODE_SESSION_ID": "ses_abc123"})

	first, err := CaptureOpenCodeSession(root, "first name", cwd, lookup,
		time.Date(2026, 8, 9, 15, 30, 0, 0, time.UTC), exporterFor(twoTurnExport(), nil))
	if err != nil {
		t.Fatal(err)
	}
	later := exportJSON(
		message("user", textPart("hi there")),
		message("assistant", textPart("hello back")),
		message("user", textPart("and one more thing")),
	)
	second, err := CaptureOpenCodeSession(root, "second name", cwd, lookup,
		time.Date(2026, 8, 9, 16, 45, 0, 0, time.UTC), exporterFor(later, nil))
	if err != nil {
		t.Fatal(err)
	}
	if second != first {
		t.Errorf("re-save moved the file: %q -> %q", first, second)
	}
	body, err := os.ReadFile(filepath.Join(root, second))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(body), "and one more thing") {
		t.Errorf("re-save did not rewrite contents:\n%s", body)
	}
}

// --- CaptureSession host dispatch --------------------------------------------

func TestCaptureSessionDispatchesToOpenCode(t *testing.T) {
	t.Parallel()
	// Tracker state deliberately absent, so the OpenCode path fails before it
	// ever shells out to `opencode` — the assertion is about *which* host path
	// ran, and it stays hermetic whether or not opencode is installed here.
	_, err := CaptureSession(t.TempDir(), "", t.TempDir(),
		env(map[string]string{"OPENCODE_SESSION_ID": "ses_abc123"}), time.Time{})
	if err == nil {
		t.Fatal("want an error from the OpenCode path, got nil")
	}
	if !strings.Contains(err.Error(), "session-tracker") {
		t.Errorf("not the OpenCode path: %v", err)
	}
	if strings.Contains(err.Error(), "CLAUDE_CODE_SESSION_ID") {
		t.Errorf("dispatched to the Claude Code path: %v", err)
	}
}

// claudeCodeCapturable makes cwd a project whose SessionStart hook recorded a
// saveable transcript for sessionID.
func claudeCodeCapturable(t *testing.T, cwd, sessionID string) {
	t.Helper()
	stateDir := filepath.Join(cwd, ".claude", "wiki-knowledge", "sessions")
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	transcript := filepath.Join(cwd, sessionID+".jsonl")
	lines := []string{entry("user", "hi", false, false), entry("assistant", "hello", false, false)}
	if err := os.WriteFile(transcript, []byte(strings.Join(lines, "\n")), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := sessionstateWrite(stateDir, sessionID, transcript); err != nil {
		t.Fatal(err)
	}
}

// Both vars set and the OpenCode one is backed by tracker state for this
// project: OpenCode is the innermost host, so it wins.
func TestCaptureSessionPrefersOpenCodeWhenItsSessionIsTracked(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	cwd := t.TempDir()
	claudeCodeCapturable(t, cwd, "abc123-xyz")
	openCodeState(t, cwd, "ses_abc123")

	_, err := CaptureSession(root, "", cwd, env(map[string]string{
		"CLAUDE_CODE_SESSION_ID": "abc123-xyz",
		"OPENCODE_SESSION_ID":    "ses_abc123",
	}), time.Date(2026, 7, 28, 14, 30, 0, 0, time.UTC))
	// The OpenCode path needs the `opencode` CLI, absent here — but reaching it
	// at all is the assertion: the Claude Code path would have succeeded.
	if err == nil {
		t.Fatal("dispatched to the Claude Code path (it would have succeeded)")
	}
	if !strings.Contains(err.Error(), "opencode") {
		t.Errorf("err = %v, want an OpenCode-path failure", err)
	}
}

// Both vars set but no tracker state for the OpenCode id — a variable leaked
// from an unrelated project or an outer session. Claude Code wins.
func TestCaptureSessionFallsBackToClaudeCodeWhenOpenCodeSessionIsUntracked(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	cwd := t.TempDir()
	claudeCodeCapturable(t, cwd, "abc123-xyz")

	rel, err := CaptureSession(root, "", cwd, env(map[string]string{
		"CLAUDE_CODE_SESSION_ID": "abc123-xyz",
		"OPENCODE_SESSION_ID":    "ses_from_elsewhere",
	}), time.Date(2026, 7, 28, 14, 30, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if want := "raw/conversations/2026-07-28-1430-abc123.md"; rel != want {
		t.Errorf("rel = %q, want %q", rel, want)
	}
}

func TestCaptureSessionNeitherHostEnvVarSet(t *testing.T) {
	t.Parallel()
	_, err := CaptureSession(t.TempDir(), "", t.TempDir(), env(map[string]string{}), time.Time{})
	if err == nil {
		t.Fatal("want error, got nil")
	}
	for _, want := range []string{"CLAUDE_CODE_SESSION_ID", "OPENCODE_SESSION_ID"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("err = %v, want it to mention %s", err, want)
		}
	}
}
