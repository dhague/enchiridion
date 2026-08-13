package toolcallstats

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func writeLog(t *testing.T, stateDir, sessionID string, events []map[string]any) {
	t.Helper()
	path := LogPath(sessionID, stateDir)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	var lines []string
	for _, e := range events {
		b, _ := json.Marshal(e)
		lines = append(lines, string(b))
	}
	if err := os.WriteFile(path, []byte(strings.Join(lines, "\n")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestReadLogReturnsEmptyWhenNoLogExists(t *testing.T) {
	got, err := ReadLog("missing", filepath.Join(t.TempDir(), ".claude", "wiki-knowledge", "sessions"))
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 0 {
		t.Errorf("got %v, want empty", got)
	}
}

func TestReadLogSkipsMalformedLines(t *testing.T) {
	stateDir := t.TempDir()
	path := LogPath("abc", stateDir)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("{\"tool\": \"Bash\"}\nnot json\n{\"tool\": \"Read\"}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	events, err := ReadLog("abc", stateDir)
	if err != nil {
		t.Fatal(err)
	}
	var tools []string
	for _, e := range events {
		tools = append(tools, e["tool"].(string))
	}
	if want := []string{"Bash", "Read"}; !reflect.DeepEqual(tools, want) {
		t.Errorf("tools = %v, want %v", tools, want)
	}
}

func TestSummarizeTotalsAndHistogram(t *testing.T) {
	events := []map[string]any{
		{"tool": "Bash", "prompt_id": "p1"},
		{"tool": "Bash", "prompt_id": "p1"},
		{"tool": "Read", "prompt_id": "p2"},
	}
	s := Summarize(events)
	if s.Total != 3 {
		t.Errorf("total = %d", s.Total)
	}
	wantByTool := []ToolCount{{Tool: "Bash", Count: 2}, {Tool: "Read", Count: 1}}
	if !reflect.DeepEqual(s.ByTool, wantByTool) {
		t.Errorf("by_tool = %v, want %v", s.ByTool, wantByTool)
	}
	if s.Prompts != 2 {
		t.Errorf("prompts = %d", s.Prompts)
	}
	if s.CallsPerPrompt != 1.5 {
		t.Errorf("calls_per_prompt = %v", s.CallsPerPrompt)
	}
}

func TestSummarizeHandlesMissingPromptID(t *testing.T) {
	events := []map[string]any{{"tool": "Bash"}, {"tool": "Bash"}}
	s := Summarize(events)
	if s.Total != 2 {
		t.Errorf("total = %d", s.Total)
	}
	if s.Prompts != 0 {
		t.Errorf("prompts = %d", s.Prompts)
	}
	if s.HasCallsPerPrompt {
		t.Errorf("calls_per_prompt should be absent")
	}
}

func TestSummarizeMissingToolIsQuestionMark(t *testing.T) {
	events := []map[string]any{{"prompt_id": "p1"}}
	s := Summarize(events)
	if len(s.ByTool) != 1 || s.ByTool[0].Tool != "?" {
		t.Errorf("by_tool = %v", s.ByTool)
	}
}

func TestFormatSummaryOmitsPromptLineWhenNoPrompts(t *testing.T) {
	text := FormatSummary(Summarize([]map[string]any{{"tool": "Bash"}}))
	if !strings.Contains(text, "Total tool calls: 1") {
		t.Errorf("missing total: %q", text)
	}
	if !strings.Contains(text, "Bash") {
		t.Errorf("missing tool: %q", text)
	}
	if strings.Contains(text, "Prompts") {
		t.Errorf("prompt line should be absent: %q", text)
	}
}

func TestFormatSummaryIncludesPromptLineWhenAvailable(t *testing.T) {
	events := []map[string]any{{"tool": "Bash", "prompt_id": "p1"}, {"tool": "Bash", "prompt_id": "p1"}}
	text := FormatSummary(Summarize(events))
	if !strings.Contains(text, "Prompts (proxy for turns, not exact") {
		t.Errorf("missing prompt line: %q", text)
	}
	if !strings.Contains(text, "2.0 calls/prompt") {
		t.Errorf("missing calls/prompt: %q", text)
	}
}
