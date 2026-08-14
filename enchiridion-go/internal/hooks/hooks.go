// Package hooks implements the plugin's automatic hook handlers.
//
// Both read a Claude Code hook payload as JSON on stdin and write per-session
// state under the *project's* `.claude/wiki-knowledge/sessions/`, resolved from
// the payload's own `cwd` (the project root at session start) rather than this
// process's cwd — so state lands in the project the session belongs to.
//
// Unlike a skill, a hook runs automatically and unattended, so it must never
// interrupt the session that triggered it (#153). These functions return errors
// for the caller to decide about; the `hook` subcommands swallow them, and
// hooks.json additionally tolerates the bootstrap itself failing, so a flaky
// binary download degrades one session's side effects instead of blocking it.
package hooks

import (
	"encoding/json"
	"io"
	"os"
	"path/filepath"

	"github.com/dhague/enchiridion/enchiridion-go/internal/sessionstate"
	"github.com/dhague/enchiridion/enchiridion-go/internal/toolcallstats"
)

// sessionStartPayload is the subset of the SessionStart payload this hook uses.
type sessionStartPayload struct {
	SessionID      string `json:"session_id"`
	TranscriptPath string `json:"transcript_path"`
	Cwd            string `json:"cwd"`
}

// SessionStart records this session's transcript_path so /save-conversation can
// retrieve it later by session_id, rather than guessing "most recently modified
// transcript" — which breaks when sessions run in parallel (#23).
//
// A payload missing either field is a silent no-op: there is nothing to record,
// and creating the state directory anyway would be a lie about it.
func SessionStart(r io.Reader) error {
	var payload sessionStartPayload
	if err := json.NewDecoder(r).Decode(&payload); err != nil {
		return err
	}
	if payload.SessionID == "" || payload.TranscriptPath == "" {
		return nil
	}
	return sessionstate.WriteTranscriptPath(
		payload.SessionID,
		payload.TranscriptPath,
		sessionstate.SessionsDir(payload.Cwd, "", nil),
	)
}

// postToolUsePayload is the subset of the PostToolUse payload this hook logs.
type postToolUsePayload struct {
	SessionID string `json:"session_id"`
	Cwd       string `json:"cwd"`
	ToolName  any    `json:"tool_name"`
	ToolUseID any    `json:"tool_use_id"`
	PromptID  any    `json:"prompt_id"`
	AgentID   any    `json:"agent_id"`
	AgentType any    `json:"agent_type"`
	Duration  any    `json:"duration_ms"`
}

// loggedCall is one line of the tool-call log. `any` fields, so an absent
// payload key is logged as an explicit null rather than being dropped —
// toolcallstats reads back a stable key set either way.
type loggedCall struct {
	Tool      any `json:"tool"`
	ToolUseID any `json:"tool_use_id"`
	PromptID  any `json:"prompt_id"`
	AgentID   any `json:"agent_id"`
	AgentType any `json:"agent_type"`
	Duration  any `json:"duration_ms"`
}

// PostToolUse appends one JSON line per tool call to the session's log, so
// `enchiridion tool-call-stats` can summarise a run's cost (#100).
//
// Per #99's spike the payload carries no per-assistant-message identifier and
// no timestamp, so tool-call count — not exact turn count — is the recoverable
// metric. prompt_id is logged anyway as the closest available grouping key, and
// agent_id/agent_type separate subagent calls from the top-level agent's own.
func PostToolUse(r io.Reader) error {
	var payload postToolUsePayload
	if err := json.NewDecoder(r).Decode(&payload); err != nil {
		return err
	}
	if payload.SessionID == "" {
		return nil
	}

	line, err := json.Marshal(loggedCall{
		Tool:      payload.ToolName,
		ToolUseID: payload.ToolUseID,
		PromptID:  payload.PromptID,
		AgentID:   payload.AgentID,
		AgentType: payload.AgentType,
		Duration:  payload.Duration,
	})
	if err != nil {
		return err
	}

	logPath := toolcallstats.LogPath(payload.SessionID, sessionstate.SessionsDir(payload.Cwd, "", nil))
	if err := os.MkdirAll(filepath.Dir(logPath), 0o755); err != nil {
		return err
	}
	f, err := os.OpenFile(logPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = f.Write(append(line, '\n'))
	return err
}
