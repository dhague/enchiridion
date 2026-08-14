// Package toolcallstats summarises the tool-call log written by the
// PostToolUse hook. Ported from `wiki-plugin/scripts/tool_call_stats.py`.
//
// Makes a run's cost visible: total tool calls, a per-tool histogram, and a
// prompt count with calls-per-prompt.
//
// **"Prompts" is a proxy, not a turn count** (#99). The PostToolUse payload
// carries no per-assistant-message identifier and no timestamp, so exact
// assistant turns aren't recoverable. prompt_id is the closest grouping key
// available, but it spans a whole user-prompt turn — which may itself cover
// several assistant turns. Labelled honestly wherever it's printed.
package toolcallstats

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/dhague/enchiridion/enchiridion-go/internal/sessionstate"
)

// LogPath returns the tool-call log path for sessionID under stateDir. An
// empty stateDir resolves the session state directory, matching the Python
// `state_dir or sessions_dir()` default.
func LogPath(sessionID, stateDir string) string {
	if stateDir == "" {
		stateDir = sessionstate.SessionsDir("", "", nil)
	}
	return filepath.Join(stateDir, sessionID+"-tool-calls.jsonl")
}

// ReadLog returns the logged events for sessionID, oldest first. An empty
// slice when no log exists. Malformed or blank lines are skipped.
func ReadLog(sessionID, stateDir string) ([]map[string]any, error) {
	data, err := os.ReadFile(LogPath(sessionID, stateDir))
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	var events []map[string]any
	for _, line := range strings.Split(string(data), "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		var event map[string]any
		if err := json.Unmarshal([]byte(line), &event); err != nil {
			continue
		}
		events = append(events, event)
	}
	return events, nil
}

// ToolCount is one tool with the number of calls, in histogram order.
type ToolCount struct {
	Tool  string
	Count int
}

// Summary is the aggregate of one run's tool-call log.
type Summary struct {
	Total int
	// ByTool is the per-tool histogram, most-called first (ties broken by
	// first-seen order, matching Python's Counter.most_common).
	ByTool []ToolCount
	// Prompts is the prompt-count proxy; see the package comment.
	Prompts int
	// CallsPerPrompt is valid only when HasCallsPerPrompt is true (i.e. there
	// is at least one prompt to divide by).
	CallsPerPrompt    float64
	HasCallsPerPrompt bool
}

// Summarize aggregates events into totals, a per-tool histogram, and the
// prompt-count proxy.
func Summarize(events []map[string]any) Summary {
	total := len(events)

	counts := map[string]int{}
	order := []string{}
	promptIDs := map[string]bool{}
	for _, event := range events {
		tool, _ := event["tool"].(string)
		if tool == "" {
			tool = "?"
		}
		if _, seen := counts[tool]; !seen {
			order = append(order, tool)
		}
		counts[tool]++
		if id, ok := event["prompt_id"].(string); ok && id != "" {
			promptIDs[id] = true
		}
	}

	byTool := make([]ToolCount, 0, len(order))
	for _, tool := range order {
		byTool = append(byTool, ToolCount{Tool: tool, Count: counts[tool]})
	}
	// Stable sort by count descending, preserving first-seen order on ties.
	for i := 1; i < len(byTool); i++ {
		for j := i; j > 0 && byTool[j].Count > byTool[j-1].Count; j-- {
			byTool[j], byTool[j-1] = byTool[j-1], byTool[j]
		}
	}

	prompts := len(promptIDs)
	s := Summary{Total: total, ByTool: byTool, Prompts: prompts}
	if prompts > 0 {
		s.CallsPerPrompt = float64(total) / float64(prompts)
		s.HasCallsPerPrompt = true
	}
	return s
}

// FormatSummary renders a summary as the fixed text the CLI prints.
func FormatSummary(s Summary) string {
	var b strings.Builder
	fmt.Fprintf(&b, "Total tool calls: %d\n", s.Total)
	for _, tc := range s.ByTool {
		fmt.Fprintf(&b, "  %3d  %s\n", tc.Count, tc.Tool)
	}
	if s.Prompts > 0 {
		fmt.Fprintf(&b,
			"Prompts (proxy for turns, not exact — see #99): %d, %.1f calls/prompt",
			s.Prompts, s.CallsPerPrompt)
	}
	return b.String()
}
