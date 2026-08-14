package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/dhague/enchiridion/enchiridion-go/internal/sessionstate"
)

// runHook executes a hook subcommand with stdin, returning combined output.
func runHook(t *testing.T, stdin string, args ...string) (string, error) {
	t.Helper()
	cmd := NewRootCommand()
	out := &bytes.Buffer{}
	cmd.SetOut(out)
	cmd.SetErr(out)
	cmd.SetIn(strings.NewReader(stdin))
	cmd.SetArgs(args)
	err := cmd.Execute()
	return out.String(), err
}

func hookSessionsDir(t *testing.T, root string) string {
	t.Helper()
	return sessionstate.SessionsDir(root, "", func(string) (string, bool) { return "", false })
}

func TestHookSessionStartRecordsTranscriptPath(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	payload := `{"session_id":"abc123","transcript_path":"/x/abc123.jsonl","cwd":"` + root + `"}`

	out, err := runHook(t, payload, "hook", "session-start")
	if err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}

	got, ok := sessionstate.ReadTranscriptPath("abc123", hookSessionsDir(t, root))
	if !ok || got != "/x/abc123.jsonl" {
		t.Errorf("ReadTranscriptPath = (%q, %v), want (%q, true)", got, ok, "/x/abc123.jsonl")
	}
}

func TestHookPostToolUseLogsTheCall(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	payload := `{"session_id":"abc123","cwd":"` + root + `","tool_name":"Bash"}`

	out, err := runHook(t, payload, "hook", "post-tool-use")
	if err != nil {
		t.Fatalf("execute: %v\n%s", err, out)
	}

	data, err := os.ReadFile(filepath.Join(hookSessionsDir(t, root), "abc123-tool-calls.jsonl"))
	if err != nil {
		t.Fatalf("read log: %v", err)
	}
	var event map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(string(data))), &event); err != nil {
		t.Fatalf("log line is not JSON: %v", err)
	}
	if event["tool"] != "Bash" {
		t.Errorf("event[tool] = %v, want Bash", event["tool"])
	}
}

// A hook runs automatically and unattended: a bad payload must degrade this
// session's side effect, never fail in a way that could interrupt the session.
func TestHookSwallowsFailuresSoASessionIsNeverInterrupted(t *testing.T) {
	t.Parallel()
	for _, sub := range []string{"session-start", "post-tool-use"} {
		t.Run(sub, func(t *testing.T) {
			t.Parallel()
			out, err := runHook(t, "not json", "hook", sub)
			if err != nil {
				t.Errorf("malformed payload: want no error, got %v\n%s", err, out)
			}
			if out != "" {
				t.Errorf("output = %q, want nothing printed", out)
			}
		})
	}
}

func TestHookRequiresAKnownEvent(t *testing.T) {
	t.Parallel()
	if _, err := runHook(t, "", "hook", "no-such-event"); err == nil {
		t.Error("unknown hook event: want an error, got nil")
	}
}
