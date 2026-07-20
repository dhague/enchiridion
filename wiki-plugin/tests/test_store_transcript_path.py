"""TDD for hooks/store_transcript_path.py — the SessionStart hook (#23)."""
import io
import json
import os
import sys

import session_state

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
import store_transcript_path  # noqa: E402


def _run(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(session_state, "DEFAULT_STATE_DIR", tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    store_transcript_path.main()


def test_records_transcript_path_for_session(monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, {"session_id": "abc123", "transcript_path": "/x/abc123.jsonl"})
    assert session_state.read_transcript_path("abc123", state_dir=tmp_path) == "/x/abc123.jsonl"


def test_missing_session_id_is_a_silent_noop(monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, {"transcript_path": "/x/abc123.jsonl"})
    assert list(tmp_path.iterdir()) == []


def test_missing_transcript_path_is_a_silent_noop(monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, {"session_id": "abc123"})
    assert list(tmp_path.iterdir()) == []


def test_malformed_json_on_stdin_does_not_raise(monkeypatch, tmp_path):
    monkeypatch.setattr(session_state, "DEFAULT_STATE_DIR", tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    store_transcript_path.main_swallowing_errors()
    assert list(tmp_path.iterdir()) == []
