"""TDD for hooks/store_transcript_path.py — the SessionStart hook (#23)."""
import io
import json
import os
import sys

import session_state

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import store_transcript_path  # noqa: E402


def _run(monkeypatch, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    store_transcript_path.main()


def test_records_transcript_path_under_payload_cwd(monkeypatch, tmp_path):
    _run(monkeypatch, {
        "session_id": "abc123",
        "transcript_path": "/x/abc123.jsonl",
        "cwd": str(tmp_path),
    })
    assert session_state.read_transcript_path("abc123", state_dir=session_state.sessions_dir(tmp_path)) == "/x/abc123.jsonl"


def test_missing_session_id_is_a_silent_noop(monkeypatch, tmp_path):
    _run(monkeypatch, {"transcript_path": "/x/abc123.jsonl", "cwd": str(tmp_path)})
    assert not session_state.sessions_dir(tmp_path).exists()


def test_missing_transcript_path_is_a_silent_noop(monkeypatch, tmp_path):
    _run(monkeypatch, {"session_id": "abc123", "cwd": str(tmp_path)})
    assert not session_state.sessions_dir(tmp_path).exists()


def test_malformed_json_on_stdin_does_not_raise(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    store_transcript_path.main_swallowing_errors()
    assert not session_state.sessions_dir(tmp_path).exists()
