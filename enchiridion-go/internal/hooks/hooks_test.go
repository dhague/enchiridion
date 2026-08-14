package hooks

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/dhague/enchiridion/enchiridion-go/internal/sessionstate"
)

func sessionsDir(t *testing.T, root string) string {
	t.Helper()
	return sessionstate.SessionsDir(root, "", func(string) (string, bool) { return "", false })
}

// --- SessionStart ------------------------------------------------------------

func TestSessionStartRecordsTranscriptPathUnderPayloadCwd(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	payload := `{"session_id":"abc123","transcript_path":"/x/abc123.jsonl","cwd":"` + root + `"}`

	if err := SessionStart(strings.NewReader(payload)); err != nil {
		t.Fatalf("SessionStart: %v", err)
	}

	got, ok := sessionstate.ReadTranscriptPath("abc123", sessionsDir(t, root))
	if !ok || got != "/x/abc123.jsonl" {
		t.Errorf("ReadTranscriptPath = (%q, %v), want (%q, true)", got, ok, "/x/abc123.jsonl")
	}
}

func TestSessionStartIncompletePayloadIsASilentNoop(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct {
		name    string
		payload string
	}{
		{"missing session_id", `{"transcript_path":"/x/abc123.jsonl","cwd":%q}`},
		{"missing transcript_path", `{"session_id":"abc123","cwd":%q}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			root := t.TempDir()
			payload := strings.Replace(tc.payload, "%q", `"`+root+`"`, 1)

			if err := SessionStart(strings.NewReader(payload)); err != nil {
				t.Fatalf("SessionStart: %v", err)
			}
			if _, err := os.Stat(sessionsDir(t, root)); !os.IsNotExist(err) {
				t.Errorf("sessions dir was created; want no state written")
			}
		})
	}
}

func TestSessionStartMalformedJSONReturnsAnErrorWithoutWriting(t *testing.T) {
	root := t.TempDir()
	t.Chdir(root)

	if err := SessionStart(strings.NewReader("not json")); err == nil {
		t.Error("malformed payload: want an error, got nil")
	}
	if _, err := os.Stat(sessionsDir(t, root)); !os.IsNotExist(err) {
		t.Errorf("sessions dir was created; want no state written")
	}
}

// --- PostToolUse -------------------------------------------------------------

func logLines(t *testing.T, root, sessionID string) []map[string]any {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(sessionsDir(t, root), sessionID+"-tool-calls.jsonl"))
	if err != nil {
		t.Fatalf("read log: %v", err)
	}
	var events []map[string]any
	for line := range strings.SplitSeq(strings.TrimSuffix(string(data), "\n"), "\n") {
		var event map[string]any
		if err := json.Unmarshal([]byte(line), &event); err != nil {
			t.Fatalf("log line %q is not JSON: %v", line, err)
		}
		events = append(events, event)
	}
	return events
}

func TestPostToolUseAppendsOneJSONLineUnderPayloadCwd(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	payload := `{"session_id":"abc123","cwd":"` + root + `","tool_name":"Bash",` +
		`"tool_use_id":"tu_1","prompt_id":"pr_1","duration_ms":42}`

	if err := PostToolUse(strings.NewReader(payload)); err != nil {
		t.Fatalf("PostToolUse: %v", err)
	}

	events := logLines(t, root, "abc123")
	if len(events) != 1 {
		t.Fatalf("logged %d lines, want 1", len(events))
	}
	want := map[string]any{
		"tool":        "Bash",
		"tool_use_id": "tu_1",
		"prompt_id":   "pr_1",
		"agent_id":    nil,
		"agent_type":  nil,
		"duration_ms": float64(42),
	}
	for key, wantValue := range want {
		if got, ok := events[0][key]; !ok || got != wantValue {
			t.Errorf("event[%q] = %v (present=%v), want %v", key, got, ok, wantValue)
		}
	}
	if len(events[0]) != len(want) {
		t.Errorf("event has keys %v, want exactly %v", events[0], want)
	}
}

func TestPostToolUseSecondCallAppendsRatherThanOverwrites(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	for _, tool := range []string{"Bash", "Read"} {
		payload := `{"session_id":"abc123","cwd":"` + root + `","tool_name":"` + tool + `"}`
		if err := PostToolUse(strings.NewReader(payload)); err != nil {
			t.Fatalf("PostToolUse(%s): %v", tool, err)
		}
	}

	events := logLines(t, root, "abc123")
	if len(events) != 2 || events[0]["tool"] != "Bash" || events[1]["tool"] != "Read" {
		t.Errorf("logged %v, want Bash then Read", events)
	}
}

func TestPostToolUseRecordsSubagentFields(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	payload := `{"session_id":"abc123","cwd":"` + root + `","tool_name":"Read",` +
		`"agent_id":"agent_1","agent_type":"general-purpose"}`

	if err := PostToolUse(strings.NewReader(payload)); err != nil {
		t.Fatalf("PostToolUse: %v", err)
	}

	event := logLines(t, root, "abc123")[0]
	if event["agent_id"] != "agent_1" || event["agent_type"] != "general-purpose" {
		t.Errorf("agent fields = %v/%v, want agent_1/general-purpose", event["agent_id"], event["agent_type"])
	}
}

func TestPostToolUseMissingSessionIDIsASilentNoop(t *testing.T) {
	t.Parallel()
	root := t.TempDir()

	if err := PostToolUse(strings.NewReader(`{"tool_name":"Bash","cwd":"` + root + `"}`)); err != nil {
		t.Fatalf("PostToolUse: %v", err)
	}
	if _, err := os.Stat(sessionsDir(t, root)); !os.IsNotExist(err) {
		t.Errorf("sessions dir was created; want no log written")
	}
}

func TestPostToolUseMalformedJSONReturnsAnErrorWithoutWriting(t *testing.T) {
	root := t.TempDir()
	t.Chdir(root)

	if err := PostToolUse(strings.NewReader("not json")); err == nil {
		t.Error("malformed payload: want an error, got nil")
	}
	if _, err := os.Stat(sessionsDir(t, root)); !os.IsNotExist(err) {
		t.Errorf("sessions dir was created; want no log written")
	}
}
