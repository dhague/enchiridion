// Package transcriptcapture turns a JSONL session transcript into a
// vault-ready raw markdown file. Ported from
// `wiki-plugin/scripts/transcript_capture.py`.
//
// The capability behind the /save-conversation skill — parse a Claude Code
// transcript, filter to the real user/assistant back-and-forth, render to
// markdown, sanitise an agent-authored slug, bind a filename in the vault's
// raw inbox. Pure or filesystem-local only; argv parsing lives in the CLI.
//
// **The name is bound once, at first save.** A re-save finds the existing
// file by its short session id and rewrites it in place rather than renaming,
// so inbound raw_source links never break.
package transcriptcapture

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"golang.org/x/text/unicode/norm"

	"github.com/dhague/enchiridion/enchiridion-go/internal/sessionstate"
)

// SlugMaxLength is the default cap on a sanitized slug.
const SlugMaxLength = 60

// nonSlugRe collapses runs of non-[a-z0-9] to a single separator.
var nonSlugRe = regexp.MustCompile(`[^a-z0-9]+`)

// SanitizeSlug reduces a free-text phrase to a filesystem-safe kebab-case
// slug.
//
// The phrase is model-authored, so this sanitizes rather than trusts:
// NFKD-fold to ASCII, lowercase, collapse non-`[a-z0-9]` runs to one `-`,
// strip the ends, cap on a word boundary where there is one. The result is
// `[a-z0-9-]` only, so it never needs percent-encoding in a link destination.
//
// Returns "" when nothing survives (empty, pure punctuation,
// non-transliterable script) — the caller then falls back to the bare
// `<date>-<short_id>` name.
func SanitizeSlug(phrase string, maxLength int) string {
	if maxLength <= 0 {
		maxLength = SlugMaxLength
	}
	if phrase == "" {
		return ""
	}
	folded := norm.NFKD.String(phrase)
	var b strings.Builder
	for _, r := range folded {
		if r < 128 {
			b.WriteRune(r)
		}
	}
	slug := strings.Trim(nonSlugRe.ReplaceAllString(strings.ToLower(b.String()), "-"), "-")
	if len(slug) <= maxLength {
		return slug
	}
	// Look one character past the cap: if the cut lands on a separator, the
	// word before it is whole. Otherwise fall back to the last boundary inside
	// the window, and to a hard truncation when the slug is one long word with
	// no boundary at all.
	window := slug[:maxLength+1]
	head := ""
	if idx := strings.LastIndex(window, "-"); idx >= 0 {
		head = window[:idx]
	}
	if head == "" {
		head = slug[:maxLength]
	}
	return strings.Trim(head, "-")
}

// transcriptEntry is one JSONL line of a Claude Code transcript.
type transcriptEntry struct {
	Type        string `json:"type"`
	IsMeta      bool   `json:"isMeta"`
	IsSidechain bool   `json:"isSidechain"`
	Message     struct {
		Role    string `json:"role"`
		Content any    `json:"content"`
	} `json:"message"`
}

// extractText pulls the prose out of a transcript entry's content field.
//
// Two shapes: a plain string, or a list of blocks. Only `text` blocks count —
// tool_use / tool_result / image aren't part of the conversation anyone
// re-reads later.
func extractText(content any) string {
	switch c := content.(type) {
	case string:
		return strings.TrimSpace(c)
	case []any:
		var parts []string
		for _, block := range c {
			m, ok := block.(map[string]any)
			if !ok || m["type"] != "text" {
				continue
			}
			text, _ := m["text"].(string)
			if text = strings.TrimSpace(text); text != "" {
				parts = append(parts, text)
			}
		}
		return strings.Join(parts, "\n\n")
	}
	return ""
}

// ErrTooFewTurns wraps the too-short-transcript failure so the CLI can print a
// "not enough conversation to save" exit.
type ErrTooFewTurns struct{ Turns, MinTurns int }

func (e ErrTooFewTurns) Error() string {
	return fmt.Sprintf("Transcript has %d non-empty turn(s); need at least %d.", e.Turns, e.MinTurns)
}

// TranscriptToPage renders a session transcript into a vault-ready page.
//
// Pure: no I/O, no env, no filesystem. jsonlLines are the raw lines of the
// Claude Code transcript. Returns (filename, markdown).
//
// slug is a free-text phrase naming what the session covered; it is sanitized
// here, not trusted, and a phrase that sanitizes to nothing degrades to the
// bare `<date>-<short_id>` name. Speaker labels are parameters, not baked in,
// so a caller can match an existing vault's captures.
func TranscriptToPage(jsonlLines []string, sessionID string, now time.Time, slug, userLabel, assistantLabel string, minTurns int) (string, string, error) {
	type turn struct{ role, text string }
	var turns []turn

	for _, line := range jsonlLines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var entry transcriptEntry
		if err := json.Unmarshal([]byte(line), &entry); err != nil {
			continue
		}
		if entry.Type != "user" && entry.Type != "assistant" {
			continue
		}
		if entry.IsMeta || entry.IsSidechain {
			continue
		}
		text := extractText(entry.Message.Content)
		if text != "" {
			turns = append(turns, turn{role: entry.Message.Role, text: text})
		}
	}

	if len(turns) < minTurns {
		return "", "", ErrTooFewTurns{Turns: len(turns), MinTurns: minTurns}
	}

	// The short id goes last so a re-save can find the already-bound file with
	// one '*-<short_id>.md' glob, slug present or not.
	shortID, _, _ := strings.Cut(sessionID, "-")
	safeSlug := SanitizeSlug(slug, SlugMaxLength)
	middle := ""
	if safeSlug != "" {
		middle = safeSlug + "-"
	}
	filename := now.Format("2006-01-02-1504") + "-" + middle + shortID + ".md"

	var lines []string
	lines = append(lines,
		"# Session "+sessionID,
		"",
		"**Saved:** "+now.Format("2006-01-02 15:04")+"  ",
		"**Source:** Claude Code session transcript (save-conversation skill, enchiridion repo)",
		"",
		"---",
		"",
	)
	for _, t := range turns {
		label := assistantLabel
		if t.role == "user" {
			label = userLabel
		}
		lines = append(lines, "## "+label, "", t.text, "")
	}

	return filename, strings.Join(lines, "\n"), nil
}

// CaptureError is a capture failure; its message is user-facing.
type CaptureError struct{ msg string }

func (e CaptureError) Error() string { return e.msg }

// FindTranscriptPath returns (transcriptPath, errorMessage); exactly one is
// non-empty.
//
// Four distinct failures, kept distinct so the user can tell them apart: no
// `$CLAUDE_CODE_SESSION_ID`; state directory not located; located but no entry
// for this session (the SessionStart hook never ran); entry pointing at a
// transcript that no longer exists.
func FindTranscriptPath(cwd string, lookupEnv func(string) (string, bool)) (string, string) {
	if lookupEnv == nil {
		lookupEnv = os.LookupEnv
	}
	if cwd == "" {
		cwd, _ = os.Getwd()
	}

	sessionID, ok := lookupEnv("CLAUDE_CODE_SESSION_ID")
	if !ok || sessionID == "" {
		return "", "$CLAUDE_CODE_SESSION_ID is not set in this environment."
	}

	stateDir := sessionstate.SessionsDir("", cwd, lookupEnv)
	if info, err := os.Stat(stateDir); err != nil || !info.IsDir() {
		return "", "Could not locate a session state directory. Searched " +
			"$CLAUDE_PROJECT_DIR, then walked up from " + cwd +
			" for a '.claude/' ancestor, and did not find one. (Has the " +
			"SessionStart hook ever run in this project? Start a new session " +
			"in the project root and try again.)"
	}

	transcriptPath, found := sessionstate.ReadTranscriptPath(sessionID, stateDir)
	if !found {
		return "", "No state recorded for session " + sessionID + " under " +
			stateDir + ". (If this session was started before the " +
			"SessionStart hook was installed, its transcript was never " +
			"recorded; start a new session and try again.)"
	}

	if info, err := os.Stat(transcriptPath); err != nil || info.IsDir() {
		return "", "Recorded transcript file does not exist: " + transcriptPath
	}

	return transcriptPath, ""
}

// WriteCapture writes the capture into `raw/conversations/`; returns its
// vault-relative path.
//
// One file per session. An earlier capture is found by globbing
// `*-<short_id>.md` and its path reused *verbatim* — same timestamp, same
// slug — with contents rewritten in place; filename is used only when nothing
// is found. So no raw file is ever renamed, and inbound raw_source links stay
// valid with no link rewriting.
func WriteCapture(wikiRoot, filename, markdown, shortID string) (string, error) {
	conversationsDir := filepath.Join(wikiRoot, "raw", "conversations")
	if err := os.MkdirAll(conversationsDir, 0o755); err != nil {
		return "", err
	}

	matches, err := filepath.Glob(filepath.Join(conversationsDir, "*-"+shortID+".md"))
	if err != nil {
		return "", err
	}
	sort.Strings(matches)
	outPath := filepath.Join(conversationsDir, filename)
	if len(matches) > 0 {
		outPath = matches[0]
	}

	if err := os.WriteFile(outPath, []byte(markdown), 0o644); err != nil {
		return "", err
	}

	rel, err := filepath.Rel(wikiRoot, outPath)
	if err != nil {
		return "", err
	}
	return filepath.ToSlash(rel), nil
}

// CaptureSession finds, renders, and writes this session's transcript; returns
// its vault-relative path.
//
// The whole pipeline (FindTranscriptPath -> TranscriptToPage -> WriteCapture)
// in one call. Raises CaptureError with a user-facing message on any failure.
func CaptureSession(wikiRoot, slug, cwd string, lookupEnv func(string) (string, bool), now time.Time) (string, error) {
	transcriptPath, errMsg := FindTranscriptPath(cwd, lookupEnv)
	if errMsg != "" {
		return "", CaptureError{errMsg}
	}
	if now.IsZero() {
		now = time.Now()
	}

	text, err := os.ReadFile(transcriptPath)
	if err != nil {
		return "", CaptureError{fmt.Sprintf("Could not read transcript: %v", err)}
	}

	sessionID := strings.TrimSuffix(filepath.Base(transcriptPath), filepath.Ext(transcriptPath))
	filename, markdown, err := TranscriptToPage(strings.Split(string(text), "\n"), sessionID, now, slug, "User", "Claude", 2)
	if err != nil {
		return "", CaptureError{"Not enough conversation to save: " + err.Error()}
	}

	shortID, _, _ := strings.Cut(sessionID, "-")
	return WriteCapture(wikiRoot, filename, markdown, shortID)
}
