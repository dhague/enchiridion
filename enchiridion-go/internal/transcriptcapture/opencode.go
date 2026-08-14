// OpenCode host support for /save-conversation, over the same seam the Claude
// Code path uses. Ported from the deleted
// `wiki-plugin/scripts/save-session-opencode.py` (#188).
//
// Everything here is OpenCode-shaped: the session id comes from
// `$OPENCODE_SESSION_ID` (injected into every shell env by the session-tracker
// plugin's `shell.env` hook), is validated against the session-tracker state
// (`.opencode/wiki-knowledge/sessions/`), the transcript is fetched via
// `opencode export <sessionID>`, normalized from the export format (`info` +
// `messages[{info:{role}, parts[...]}]`) into (role, text) turns, re-encoded
// into the Claude Code JSONL shape TranscriptToPage already reads, and written
// by WriteCapture. The pure seam is untouched.
//
// A transcript written this way carries the same "Source: Claude Code session
// transcript" line TranscriptToPage always emits — the seam is shared verbatim
// by both hosts, and OpenCode captures inherit the generic label.

package transcriptcapture

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// openCodeSessionsSubdir is where the session-tracker plugin writes its state.
var openCodeSessionsSubdir = filepath.Join(".opencode", "wiki-knowledge", "sessions")

// openCodeSessionsDir returns the session-tracker state dir for this project:
// the nearest ancestor of cwd containing `.opencode/` — writer and reader must
// agree even when cwd is a subdirectory — and cwd itself when no ancestor holds
// the marker, so a path is always returned (it may not exist yet).
func openCodeSessionsDir(cwd string) string {
	if cwd == "" {
		cwd, _ = os.Getwd()
	}
	base := cwd
	for dir := cwd; ; {
		if info, err := os.Stat(filepath.Join(dir, ".opencode")); err == nil && info.IsDir() {
			base = dir
			break
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	return filepath.Join(base, openCodeSessionsSubdir)
}

// openCodeSessionIsTracked reports whether the tracker recorded this session:
// the `<id>.json` file exists, parses, and names sessionID back. A corrupt file
// counts as untracked, mirroring ReadTranscriptPath's JSON-decode guard.
func openCodeSessionIsTracked(sessionID, stateDir string) bool {
	data, err := os.ReadFile(filepath.Join(stateDir, sessionID+".json"))
	if err != nil {
		return false
	}
	var payload map[string]any
	if err := json.Unmarshal(data, &payload); err != nil {
		return false
	}
	recorded, _ := payload["session_id"].(string)
	return recorded == sessionID
}

// FindOpenCodeSessionID returns (sessionID, errorMessage); exactly one is
// non-empty.
//
// Three distinct failures, kept distinct so the user can tell them apart: no
// `$OPENCODE_SESSION_ID` (the session-tracker plugin's `shell.env` hook must
// inject it); state directory not located (no `.opencode/` ancestor of cwd, so
// the plugin has never recorded state in this project); located but no entry
// for this session (started before the plugin was installed).
func FindOpenCodeSessionID(cwd string, lookupEnv func(string) (string, bool)) (string, string) {
	if lookupEnv == nil {
		lookupEnv = os.LookupEnv
	}
	if cwd == "" {
		cwd, _ = os.Getwd()
	}

	sessionID, ok := lookupEnv("OPENCODE_SESSION_ID")
	if !ok || sessionID == "" {
		return "", "$OPENCODE_SESSION_ID is not set in this environment. (The " +
			"session-tracker plugin's shell.env hook injects it; is the plugin " +
			"installed and loaded in this project?)"
	}

	stateDir := openCodeSessionsDir(cwd)
	if info, err := os.Stat(stateDir); err != nil || !info.IsDir() {
		// Name the directory actually looked in, not just the search rule: when
		// a nested `.opencode/` shadows the project's own, the resolved path is
		// the only thing that says so.
		return "", "Could not locate OpenCode session-tracker state. Searched " +
			cwd + " and its ancestors for a '.opencode/' directory, and found no " +
			stateDir + ". (Has the session-tracker plugin ever run in this " +
			"project? Start a new session in the project root and try again.)"
	}

	if !openCodeSessionIsTracked(sessionID, stateDir) {
		return "", "No state recorded for session " + sessionID + " under " +
			stateDir + ", per the session-tracker plugin. (If this session was " +
			"started before the plugin was installed, it was never recorded; " +
			"start a new session and try again.)"
	}

	return sessionID, ""
}

// Exporter fetches one OpenCode session's export document. Injectable so the
// pipeline can be tested without the `opencode` CLI; nil means ExportTranscript.
type Exporter func(sessionID string) ([]byte, error)

// ExportTranscript runs `<command> export <sessionID>` and returns its stdout.
//
// **Strict:** errors when the CLI is absent from PATH or the command exits
// non-zero.
//
// `opencode export` truncates its JSON when stdout is a pipe (observed on
// 1.18.15: output stops ~64KB in), so stdout is written to a real temp file and
// read back from there — a file redirect carries the whole transcript.
func ExportTranscript(sessionID, command string) ([]byte, error) {
	if command == "" {
		command = "opencode"
	}
	binary, err := exec.LookPath(command)
	if err != nil {
		return nil, CaptureError{fmt.Sprintf("%s CLI is required but was not found on PATH", command)}
	}

	tmp, err := os.CreateTemp("", "opencode-export-*.json")
	if err != nil {
		return nil, CaptureError{fmt.Sprintf("Could not create a temp file for the export: %v", err)}
	}
	defer os.Remove(tmp.Name())

	var stderr strings.Builder
	cmd := exec.Command(binary, "export", sessionID)
	cmd.Stdout = tmp
	cmd.Stderr = &stderr
	runErr := cmd.Run()
	closeErr := tmp.Close()
	if runErr != nil {
		return nil, CaptureError{fmt.Sprintf("opencode export failed (%v): %s", runErr, strings.TrimSpace(stderr.String()))}
	}
	if closeErr != nil {
		return nil, CaptureError{fmt.Sprintf("Could not flush the export: %v", closeErr)}
	}

	data, err := os.ReadFile(tmp.Name())
	if err != nil {
		return nil, CaptureError{fmt.Sprintf("Could not read the export back: %v", err)}
	}
	return data, nil
}

// Turn is one (role, text) exchange, the shape both hosts reduce to.
type Turn struct {
	Role string
	Text string
}

// NormalizeExport maps an `opencode export` document into (role, text) turns.
//
// The export is `info` + `messages[{info:{role}, parts[{type:"text"}]}]`. Only
// user/assistant messages and `type: "text"` parts count — tool calls,
// reasoning, step markers, and patches are not the back-and-forth anyone
// re-reads later. Sub-agent work runs in its own OpenCode session, so it never
// appears in a parent session's export and needs no sidechain filter here.
// Multiple text parts in one message join with a blank line.
//
// Malformed messages and parts are skipped rather than fatal, mirroring
// TranscriptToPage's tolerance of a garbled JSONL line; only a document that is
// not a JSON object at all is an error.
func NormalizeExport(export []byte) ([]Turn, error) {
	if !json.Valid(export) {
		return nil, CaptureError{"opencode export returned invalid JSON"}
	}
	var document map[string]json.RawMessage
	if err := json.Unmarshal(export, &document); err != nil || document == nil {
		return nil, CaptureError{"opencode export returned an unexpected shape"}
	}
	// A messages key of the wrong shape leaves the list empty rather than
	// failing — same tolerance the per-message decode below applies.
	var messages []json.RawMessage
	if raw, ok := document["messages"]; ok {
		_ = json.Unmarshal(raw, &messages)
	}

	var turns []Turn
	for _, raw := range messages {
		var message struct {
			Info struct {
				Role string `json:"role"`
			} `json:"info"`
			Parts []json.RawMessage `json:"parts"`
		}
		if err := json.Unmarshal(raw, &message); err != nil {
			continue
		}
		if message.Info.Role != "user" && message.Info.Role != "assistant" {
			continue
		}
		var texts []string
		for _, rawPart := range message.Parts {
			var part struct {
				Type string `json:"type"`
				Text string `json:"text"`
			}
			if err := json.Unmarshal(rawPart, &part); err != nil || part.Type != "text" {
				continue
			}
			if text := strings.TrimSpace(part.Text); text != "" {
				texts = append(texts, text)
			}
		}
		if len(texts) > 0 {
			turns = append(turns, Turn{Role: message.Info.Role, Text: strings.Join(texts, "\n\n")})
		}
	}
	return turns, nil
}

// EncodeTurns re-encodes turns as the Claude Code JSONL shape TranscriptToPage
// reads, so the pure seam needs no change to serve OpenCode transcripts.
func EncodeTurns(turns []Turn) []string {
	lines := make([]string, 0, len(turns))
	for _, turn := range turns {
		line, err := json.Marshal(map[string]any{
			"type":        turn.Role,
			"isMeta":      false,
			"isSidechain": false,
			"message":     map[string]any{"role": turn.Role, "content": turn.Text},
		})
		if err != nil {
			continue
		}
		lines = append(lines, string(line))
	}
	return lines
}

// CaptureOpenCodeSession resolves the current OpenCode session, exports and
// normalizes its transcript, and writes the capture; returns its vault-relative
// path.
//
// The whole pipeline (FindOpenCodeSessionID -> export -> NormalizeExport ->
// TranscriptToPage -> WriteCapture) in one call. export is the injectable
// fetch seam; nil runs the real `opencode export`.
func CaptureOpenCodeSession(wikiRoot, slug, cwd string, lookupEnv func(string) (string, bool), now time.Time, export Exporter) (string, error) {
	sessionID, errMsg := FindOpenCodeSessionID(cwd, lookupEnv)
	if errMsg != "" {
		return "", CaptureError{errMsg}
	}
	if export == nil {
		export = func(id string) ([]byte, error) { return ExportTranscript(id, "opencode") }
	}
	if now.IsZero() {
		now = time.Now()
	}

	document, err := export(sessionID)
	if err != nil {
		return "", err
	}
	turns, err := NormalizeExport(document)
	if err != nil {
		return "", err
	}

	filename, markdown, err := TranscriptToPage(EncodeTurns(turns), sessionID, now, slug, "User", "Claude", 2)
	if err != nil {
		return "", CaptureError{"Not enough conversation to save: " + err.Error()}
	}

	shortID, _, _ := strings.Cut(sessionID, "-")
	return WriteCapture(wikiRoot, filename, markdown, shortID)
}
