// Package sessionstate maps a Claude Code session_id to its transcript_path.
// Ported from `wiki-plugin/scripts/session_state.py`.
//
// The `SessionStart` hook writes; the save-conversation skill reads by
// `$CLAUDE_CODE_SESSION_ID`, so it never guesses which concurrently running
// session is "current". State lives under the *project's*
// `.claude/wiki-knowledge/sessions/` (gitignored), not the vault — in
// query-from-anywhere mode the vault is somewhere else entirely. One JSON file
// per session_id, so parallel sessions sharing a project don't clobber each
// other.
package sessionstate

import (
	"encoding/json"
	"os"
	"path/filepath"
)

// SessionsDir returns the sessions directory for this project.
//
// Resolution order, highest priority first:
//
//  1. root if non-empty — caller-injected (tests, the hook).
//  2. `$CLAUDE_PROJECT_DIR` — the most reliable statement of the current
//     project.
//  3. The nearest ancestor of cwd containing `.claude/` — writer and reader
//     must agree on a root even when cwd is a subdirectory.
//  4. cwd, so a path is always returned. It may not exist yet.
//
// cwd defaults to the process working directory when empty, and lookupEnv
// defaults to os.LookupEnv when nil; both are injectable for tests.
func SessionsDir(root, cwd string, lookupEnv func(string) (string, bool)) string {
	if lookupEnv == nil {
		lookupEnv = os.LookupEnv
	}

	base := ""
	switch {
	case root != "":
		base = root
	default:
		if projectDir, ok := lookupEnv("CLAUDE_PROJECT_DIR"); ok && projectDir != "" {
			base = projectDir
		} else {
			if cwd == "" {
				cwd, _ = os.Getwd()
			}
			base = cwd
			for dir := base; ; {
				if info, err := os.Stat(filepath.Join(dir, ".claude")); err == nil && info.IsDir() {
					base = dir
					break
				}
				parent := filepath.Dir(dir)
				if parent == dir {
					break
				}
				dir = parent
			}
		}
	}
	return filepath.Join(base, ".claude", "wiki-knowledge", "sessions")
}

func statePath(sessionID, stateDir string) string {
	return filepath.Join(stateDir, sessionID+".json")
}

// WriteTranscriptPath records transcriptPath for sessionID, creating the state
// directory as needed.
func WriteTranscriptPath(sessionID, transcriptPath, stateDir string) error {
	path := statePath(sessionID, stateDir)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	data, err := json.Marshal(map[string]string{"transcript_path": transcriptPath})
	if err != nil {
		return err
	}
	return os.WriteFile(path, data, 0o644)
}

// ReadTranscriptPath returns the recorded transcript path for sessionID, or
// ("", false) when no state exists or it is unparsable.
func ReadTranscriptPath(sessionID, stateDir string) (string, bool) {
	data, err := os.ReadFile(statePath(sessionID, stateDir))
	if err != nil {
		return "", false
	}
	var payload map[string]any
	if err := json.Unmarshal(data, &payload); err != nil {
		return "", false
	}
	transcriptPath, ok := payload["transcript_path"].(string)
	return transcriptPath, ok
}
