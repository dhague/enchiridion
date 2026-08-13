package transcriptcapture

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"pgregory.net/rapid"
)

func entry(role, text string, isMeta, isSidechain bool) string {
	e := map[string]any{
		"type":        role,
		"isMeta":      isMeta,
		"isSidechain": isSidechain,
		"message":     map[string]any{"role": role, "content": text},
	}
	b, _ := json.Marshal(e)
	return string(b)
}

func env(m map[string]string) func(string) (string, bool) {
	return func(key string) (string, bool) {
		v, ok := m[key]
		return v, ok
	}
}

// --- SanitizeSlug -----------------------------------------------------------

func TestSanitizeSlug(t *testing.T) {
	tests := []struct {
		in   string
		max  int
		want string
	}{
		{"Charting wayfinder 33", 60, "charting-wayfinder-33"},
		{"Charting: wayfinder #33!!", 60, "charting-wayfinder-33"},
		{"--- hello ---", 60, "hello"},
		{"Café naïve", 60, "cafe-naive"},
		{"日本語", 60, ""},
		{"../../etc/passwd", 60, "etc-passwd"},
		{`C:\Windows\System32`, 60, "c-windows-system32"},
		{"", 60, ""},
		{"..", 60, ""},
		{"///", 60, ""},
		{"###", 60, ""},
		{"   ", 60, ""},
		{"-", 60, ""},
		{"!@#$%^&*()", 60, ""},
		{"alpha beta gamma delta epsilon zeta eta theta iota kappa lambda", 60, "alpha-beta-gamma-delta-epsilon-zeta-eta-theta-iota-kappa"},
		{strings.Repeat("x", 200), 60, strings.Repeat("x", 60)},
	}
	for _, tc := range tests {
		if got := SanitizeSlug(tc.in, tc.max); got != tc.want {
			t.Errorf("SanitizeSlug(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

var slugShape = regexp.MustCompile(`^[a-z0-9]+(-[a-z0-9]+)*$`)

func TestSanitizeSlugShapeProperty(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		phrase := rapid.String().Draw(t, "phrase")
		slug := SanitizeSlug(phrase, SlugMaxLength)
		if slug != "" && !slugShape.MatchString(slug) {
			t.Fatalf("SanitizeSlug(%q) = %q, not a slug", phrase, slug)
		}
	})
}

func TestSanitizeSlugLengthProperty(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		phrase := rapid.String().Draw(t, "phrase")
		maxLength := rapid.IntRange(1, 120).Draw(t, "max")
		slug := SanitizeSlug(phrase, maxLength)
		if len(slug) > maxLength {
			t.Fatalf("SanitizeSlug(%q, %d) = %q, too long", phrase, maxLength, slug)
		}
	})
}

func TestSanitizeSlugIdempotent(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		phrase := rapid.String().Draw(t, "phrase")
		once := SanitizeSlug(phrase, SlugMaxLength)
		if again := SanitizeSlug(once, SlugMaxLength); again != once {
			t.Fatalf("SanitizeSlug not idempotent: %q -> %q", once, again)
		}
	})
}

// --- TranscriptToPage --------------------------------------------------------

func TestTranscriptToPageFilename(t *testing.T) {
	lines := []string{entry("user", "Hello", false, false), entry("assistant", "Hi there", false, false)}
	filename, _, err := TranscriptToPage(lines, "abc123-uuid", time.Date(2026, 7, 28, 10, 26, 0, 0, time.UTC), "", "User", "Claude", 2)
	if err != nil {
		t.Fatal(err)
	}
	if filename != "2026-07-28-1026-abc123.md" {
		t.Errorf("filename = %q", filename)
	}
}

func TestTranscriptToPageFiltersMetaAndSidechain(t *testing.T) {
	lines := []string{
		entry("user", "real question", false, false),
		entry("assistant", "real answer", false, false),
		entry("user", "synthetic", true, false),
		entry("user", "sub-agent scratch", false, true),
	}
	_, markdown, err := TranscriptToPage(lines, "sid", time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), "", "User", "Claude", 2)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(markdown, "real question") || !strings.Contains(markdown, "real answer") {
		t.Errorf("missing real turns: %q", markdown)
	}
	if strings.Contains(markdown, "synthetic") || strings.Contains(markdown, "sub-agent scratch") {
		t.Errorf("synthetic/sidechain text leaked: %q", markdown)
	}
}

func TestTranscriptToPageFiltersNonUserAssistantTypes(t *testing.T) {
	lines := []string{
		entry("user", "u1", false, false),
		entry("assistant", "a1", false, false),
		`{"type":"file-history-snapshot","message":{"role":"system","content":"x"}}`,
		`{"type":"system","message":{"role":"system","content":"y"}}`,
	}
	_, markdown, err := TranscriptToPage(lines, "sid", time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), "", "User", "Claude", 2)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(markdown, "u1") || !strings.Contains(markdown, "a1") {
		t.Errorf("missing u1/a1: %q", markdown)
	}
	if strings.Contains(markdown, "\nx\n") || strings.Contains(markdown, "\ny\n") {
		t.Errorf("system text leaked")
	}
}

func TestTranscriptToPageExtractsListContent(t *testing.T) {
	first := map[string]any{
		"type": "user", "isMeta": false, "isSidechain": false,
		"message": map[string]any{
			"role":    "user",
			"content": []any{map[string]any{"type": "text", "text": "first block"}, map[string]any{"type": "text", "text": "second block"}},
		},
	}
	bf, _ := json.Marshal(first)
	_, markdown, err := TranscriptToPage([]string{string(bf), entry("assistant", "ack", false, false)}, "sid", time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), "", "User", "Claude", 2)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(markdown, "first block\n\nsecond block") {
		t.Errorf("list content not joined: %q", markdown)
	}
}

func TestTranscriptToPageRaisesForTooFewTurns(t *testing.T) {
	_, _, err := TranscriptToPage([]string{entry("user", "just me", false, false)}, "sid", time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), "", "User", "Claude", 2)
	if err == nil {
		t.Fatal("want error for a single turn")
	}
}

func TestTranscriptToPageSpeakerLabels(t *testing.T) {
	lines := []string{entry("user", "hi", false, false), entry("assistant", "hello", false, false)}
	_, markdown, err := TranscriptToPage(lines, "sid", time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), "", "Alex", "Claude", 2)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(markdown, "## Alex") || !strings.Contains(markdown, "## Claude") {
		t.Errorf("labels wrong: %q", markdown)
	}
}

func TestTranscriptToPageHeaderAndSurvivesGarbled(t *testing.T) {
	lines := []string{"not json", entry("user", "hi", false, false), "{malformed", entry("assistant", "hello", false, false)}
	_, markdown, err := TranscriptToPage(lines, "abc-123", time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), "", "User", "Claude", 2)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(markdown, "# Session abc-123") {
		t.Errorf("header missing: %q", markdown)
	}
	if !strings.Contains(markdown, "hi") || !strings.Contains(markdown, "hello") {
		t.Errorf("garbled lines broke the parse: %q", markdown)
	}
}

func TestTranscriptToPageSlugInFilename(t *testing.T) {
	lines := []string{entry("user", "hi", false, false), entry("assistant", "hello", false, false)}
	now := time.Date(2026, 7, 28, 14, 30, 0, 0, time.UTC)
	filename, _, err := TranscriptToPage(lines, "1dc3e094-rest-of-uuid", now, "charting wayfinder 33", "User", "Claude", 2)
	if err != nil {
		t.Fatal(err)
	}
	if filename != "2026-07-28-1430-charting-wayfinder-33-1dc3e094.md" {
		t.Errorf("filename = %q", filename)
	}

	// Sanitized, not trusted.
	filename, _, err = TranscriptToPage(lines, "1dc3e094-rest", now, "../../Charting: Wayfinder #33!", "User", "Claude", 2)
	if err != nil {
		t.Fatal(err)
	}
	if filename != "2026-07-28-1430-charting-wayfinder-33-1dc3e094.md" {
		t.Errorf("unsanitized slug = %q", filename)
	}

	// Pure-punctuation slug degrades to the bare shape.
	filename, _, err = TranscriptToPage(lines, "1dc3e094-rest", now, "///", "User", "Claude", 2)
	if err != nil {
		t.Fatal(err)
	}
	if filename != "2026-07-28-1430-1dc3e094.md" {
		t.Errorf("empty-slug filename = %q", filename)
	}
}

// --- FindTranscriptPath ------------------------------------------------------

func TestFindTranscriptPathMissingSessionID(t *testing.T) {
	_, errMsg := FindTranscriptPath(t.TempDir(), env(nil))
	if !strings.Contains(errMsg, "CLAUDE_CODE_SESSION_ID") {
		t.Errorf("errMsg = %q", errMsg)
	}
}

func TestFindTranscriptPathStateDirNotLocated(t *testing.T) {
	cwd := t.TempDir()
	_, errMsg := FindTranscriptPath(cwd, env(map[string]string{"CLAUDE_CODE_SESSION_ID": "sid-abc"}))
	if !strings.Contains(strings.ToLower(errMsg), "state") {
		t.Errorf("errMsg = %q", errMsg)
	}
	if strings.Contains(errMsg, "SessionStart hook may not have run") {
		t.Errorf("wrong failure class: %q", errMsg)
	}
}

func TestFindTranscriptPathLocatedButNoEntry(t *testing.T) {
	cwd := t.TempDir()
	stateDir := filepath.Join(cwd, ".claude", "wiki-knowledge", "sessions")
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	_, errMsg := FindTranscriptPath(cwd, env(map[string]string{"CLAUDE_CODE_SESSION_ID": "sid-abc"}))
	if !strings.Contains(errMsg, "sid-abc") {
		t.Errorf("errMsg = %q", errMsg)
	}
	if strings.Contains(strings.ToLower(errMsg), "could not locate") {
		t.Errorf("wrong failure class: %q", errMsg)
	}
}

func TestFindTranscriptPathFromSubdirectoryFindsAncestor(t *testing.T) {
	root := t.TempDir()
	stateDir := filepath.Join(root, ".claude", "wiki-knowledge", "sessions")
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	transcript := filepath.Join(root, "transcripts", "sid-abc.jsonl")
	if err := os.MkdirAll(filepath.Dir(transcript), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(transcript, []byte("{}"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := sessionstateWrite(stateDir, "sid-abc", transcript); err != nil {
		t.Fatal(err)
	}
	subdir := filepath.Join(root, "src", "deep", "nested")
	if err := os.MkdirAll(subdir, 0o755); err != nil {
		t.Fatal(err)
	}
	got, errMsg := FindTranscriptPath(subdir, env(map[string]string{"CLAUDE_CODE_SESSION_ID": "sid-abc"}))
	if errMsg != "" {
		t.Fatalf("unexpected error: %s", errMsg)
	}
	if got != transcript {
		t.Errorf("got %q, want %q", got, transcript)
	}
}

func TestFindTranscriptPathHonorsProjectDirEnv(t *testing.T) {
	root := t.TempDir()
	stateDir := filepath.Join(root, ".claude", "wiki-knowledge", "sessions")
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	transcript := filepath.Join(root, "x.jsonl")
	if err := os.WriteFile(transcript, []byte("{}"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := sessionstateWrite(stateDir, "sid-abc", transcript); err != nil {
		t.Fatal(err)
	}
	otherCwd := filepath.Join(root, "scratch")
	if err := os.MkdirAll(otherCwd, 0o755); err != nil {
		t.Fatal(err)
	}
	got, errMsg := FindTranscriptPath(otherCwd, env(map[string]string{"CLAUDE_CODE_SESSION_ID": "sid-abc", "CLAUDE_PROJECT_DIR": root}))
	if errMsg != "" {
		t.Fatalf("unexpected error: %s", errMsg)
	}
	if got != transcript {
		t.Errorf("got %q, want %q", got, transcript)
	}
}

func TestFindTranscriptPathRecordedTranscriptMissing(t *testing.T) {
	cwd := t.TempDir()
	stateDir := filepath.Join(cwd, ".claude", "wiki-knowledge", "sessions")
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := sessionstateWrite(stateDir, "sid-abc", filepath.Join(cwd, "ghost.jsonl")); err != nil {
		t.Fatal(err)
	}
	_, errMsg := FindTranscriptPath(cwd, env(map[string]string{"CLAUDE_CODE_SESSION_ID": "sid-abc"}))
	if !strings.Contains(errMsg, "ghost.jsonl") || !strings.Contains(errMsg, "does not exist") {
		t.Errorf("errMsg = %q", errMsg)
	}
}

// --- WriteCapture ------------------------------------------------------------

func TestWriteCaptureWritesComposedNameOnFirstSave(t *testing.T) {
	root := t.TempDir()
	rel, err := WriteCapture(root, "2026-07-28-1430-a-slug-abc123.md", "body", "abc123")
	if err != nil {
		t.Fatal(err)
	}
	if rel != "raw/conversations/2026-07-28-1430-a-slug-abc123.md" {
		t.Errorf("rel = %q", rel)
	}
	if got, _ := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel))); string(got) != "body" {
		t.Errorf("content = %q", got)
	}
}

func TestWriteCaptureResaveReusesTheBoundName(t *testing.T) {
	root := t.TempDir()
	first, err := WriteCapture(root, "2026-07-28-1430-first-slug-abc123.md", "v1", "abc123")
	if err != nil {
		t.Fatal(err)
	}
	second, err := WriteCapture(root, "2026-07-29-0900-a-totally-different-slug-abc123.md", "v2", "abc123")
	if err != nil {
		t.Fatal(err)
	}
	if second != first {
		t.Errorf("resave renamed: %q -> %q", first, second)
	}
	if got, _ := os.ReadFile(filepath.Join(root, filepath.FromSlash(first))); string(got) != "v2" {
		t.Errorf("content = %q", got)
	}
	entries, _ := os.ReadDir(filepath.Join(root, "raw", "conversations"))
	if len(entries) != 1 || entries[0].Name() != "2026-07-28-1430-first-slug-abc123.md" {
		t.Errorf("dir = %v", entries)
	}
}

func TestWriteCaptureLeavesOtherSessionsAlone(t *testing.T) {
	root := t.TempDir()
	other := filepath.Join(root, "raw", "conversations", "2026-07-01-1200-zzz999.md")
	if err := os.MkdirAll(filepath.Dir(other), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(other, []byte("someone else"), 0o644); err != nil {
		t.Fatal(err)
	}
	rel, err := WriteCapture(root, "2026-07-28-1430-mine-abc123.md", "mine", "abc123")
	if err != nil {
		t.Fatal(err)
	}
	if rel != "raw/conversations/2026-07-28-1430-mine-abc123.md" {
		t.Errorf("rel = %q", rel)
	}
	if got, _ := os.ReadFile(other); string(got) != "someone else" {
		t.Errorf("other session overwritten")
	}
}

// --- CaptureSession ----------------------------------------------------------

func TestCaptureSessionEndToEnd(t *testing.T) {
	root := t.TempDir()
	cwd := t.TempDir()
	stateDir := filepath.Join(cwd, ".claude", "wiki-knowledge", "sessions")
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		t.Fatal(err)
	}
	transcript := filepath.Join(cwd, "abc123-xyz.jsonl")
	lines := []string{entry("user", "hi", false, false), entry("assistant", "hello", false, false)}
	if err := os.WriteFile(transcript, []byte(strings.Join(lines, "\n")), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := sessionstateWrite(stateDir, "abc123-xyz", transcript); err != nil {
		t.Fatal(err)
	}

	rel, err := CaptureSession(root, "a slug", cwd, env(map[string]string{"CLAUDE_CODE_SESSION_ID": "abc123-xyz"}), time.Date(2026, 7, 28, 14, 30, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if rel != "raw/conversations/2026-07-28-1430-a-slug-abc123.md" {
		t.Errorf("rel = %q", rel)
	}
}

// sessionstateWrite writes a transcript_path record, mirroring the hook.
func sessionstateWrite(stateDir, sessionID, transcriptPath string) error {
	data, _ := json.Marshal(map[string]string{"transcript_path": transcriptPath})
	return os.WriteFile(filepath.Join(stateDir, sessionID+".json"), data, 0o644)
}
