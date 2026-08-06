"""TDD for scripts/tool_call_stats.py (#100)."""
import json

import session_state
import tool_call_stats as stats


def _write_log(tmp_path, session_id, events):
    path = stats.log_path(session_id, session_state.sessions_dir(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def test_read_log_returns_empty_list_when_no_log_exists(tmp_path):
    assert stats.read_log("missing", session_state.sessions_dir(tmp_path)) == []


def test_read_log_skips_malformed_lines(tmp_path):
    state_dir = session_state.sessions_dir(tmp_path)
    path = stats.log_path("abc", state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"tool": "Bash"}\nnot json\n{"tool": "Read"}\n', encoding="utf-8")
    events = stats.read_log("abc", state_dir)
    assert [e["tool"] for e in events] == ["Bash", "Read"]


def test_summarize_totals_and_histogram():
    events = [
        {"tool": "Bash", "prompt_id": "p1"},
        {"tool": "Bash", "prompt_id": "p1"},
        {"tool": "Read", "prompt_id": "p2"},
    ]
    result = stats.summarize(events)
    assert result["total"] == 3
    assert dict(result["by_tool"]) == {"Bash": 2, "Read": 1}
    assert result["prompts"] == 2
    assert result["calls_per_prompt"] == 1.5


def test_summarize_handles_missing_prompt_id():
    events = [{"tool": "Bash"}, {"tool": "Bash"}]
    result = stats.summarize(events)
    assert result["total"] == 2
    assert result["prompts"] == 0
    assert result["calls_per_prompt"] is None


def test_format_summary_omits_prompt_line_when_no_prompts():
    text = stats.format_summary(stats.summarize([{"tool": "Bash"}]))
    assert "Total tool calls: 1" in text
    assert "Bash" in text
    assert "Prompts" not in text


def test_format_summary_includes_prompt_line_when_available():
    events = [{"tool": "Bash", "prompt_id": "p1"}, {"tool": "Bash", "prompt_id": "p1"}]
    text = stats.format_summary(stats.summarize(events))
    assert "Prompts (proxy for turns, not exact" in text
    assert "2.0 calls/prompt" in text


def test_end_to_end_reads_log_written_by_hook(tmp_path):
    _write_log(tmp_path, "sess1", [
        {"tool": "Bash", "prompt_id": "p1"},
        {"tool": "Write", "prompt_id": "p1"},
    ])
    state_dir = session_state.sessions_dir(tmp_path)
    events = stats.read_log("sess1", state_dir)
    text = stats.format_summary(stats.summarize(events))
    assert "Total tool calls: 2" in text
