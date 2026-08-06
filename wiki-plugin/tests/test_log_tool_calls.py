"""TDD for hooks/log_tool_calls.py — the PostToolUse hook (#100)."""
import io
import json
import os
import sys

import session_state

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import log_tool_calls  # noqa: E402


def _run(monkeypatch, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    log_tool_calls.main()


def _log_path(tmp_path, session_id):
    return session_state.sessions_dir(tmp_path) / f"{session_id}-tool-calls.jsonl"


def test_appends_one_json_line_under_payload_cwd(monkeypatch, tmp_path):
    _run(monkeypatch, {
        "session_id": "abc123",
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_use_id": "tu_1",
        "prompt_id": "pr_1",
        "duration_ms": 42,
    })
    lines = _log_path(tmp_path, "abc123").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event == {
        "tool": "Bash",
        "tool_use_id": "tu_1",
        "prompt_id": "pr_1",
        "agent_id": None,
        "agent_type": None,
        "duration_ms": 42,
    }


def test_second_call_appends_rather_than_overwrites(monkeypatch, tmp_path):
    _run(monkeypatch, {"session_id": "abc123", "cwd": str(tmp_path), "tool_name": "Bash"})
    _run(monkeypatch, {"session_id": "abc123", "cwd": str(tmp_path), "tool_name": "Read"})
    lines = _log_path(tmp_path, "abc123").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["tool"] for line in lines] == ["Bash", "Read"]


def test_subagent_fields_are_recorded(monkeypatch, tmp_path):
    _run(monkeypatch, {
        "session_id": "abc123",
        "cwd": str(tmp_path),
        "tool_name": "Read",
        "agent_id": "agent_1",
        "agent_type": "general-purpose",
    })
    event = json.loads(_log_path(tmp_path, "abc123").read_text(encoding="utf-8").splitlines()[0])
    assert event["agent_id"] == "agent_1"
    assert event["agent_type"] == "general-purpose"


def test_missing_session_id_is_a_silent_noop(monkeypatch, tmp_path):
    _run(monkeypatch, {"tool_name": "Bash", "cwd": str(tmp_path)})
    assert not session_state.sessions_dir(tmp_path).exists()


def test_malformed_json_on_stdin_does_not_raise(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    log_tool_calls.main_swallowing_errors()
    assert not session_state.sessions_dir(tmp_path).exists()
