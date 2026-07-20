"""TDD for session_state.py — per-session transcript_path storage (#23)."""
import session_state


def test_write_then_read_round_trips(tmp_path):
    session_state.write_transcript_path("session-a", "/path/to/a.jsonl", state_dir=tmp_path)
    assert session_state.read_transcript_path("session-a", state_dir=tmp_path) == "/path/to/a.jsonl"


def test_read_returns_none_for_unknown_session(tmp_path):
    assert session_state.read_transcript_path("never-written", state_dir=tmp_path) is None


def test_sessions_are_isolated(tmp_path):
    # Two sessions running in parallel must not clobber or shadow each other -
    # the exact bug (#23) this module exists to fix.
    session_state.write_transcript_path("session-a", "/path/to/a.jsonl", state_dir=tmp_path)
    session_state.write_transcript_path("session-b", "/path/to/b.jsonl", state_dir=tmp_path)
    assert session_state.read_transcript_path("session-a", state_dir=tmp_path) == "/path/to/a.jsonl"
    assert session_state.read_transcript_path("session-b", state_dir=tmp_path) == "/path/to/b.jsonl"


def test_write_overwrites_previous_value_for_same_session(tmp_path):
    # SessionStart fires again on /clear and /compact (source field), so the
    # same session_id gets re-recorded - the latest transcript_path must win.
    session_state.write_transcript_path("session-a", "/path/to/old.jsonl", state_dir=tmp_path)
    session_state.write_transcript_path("session-a", "/path/to/new.jsonl", state_dir=tmp_path)
    assert session_state.read_transcript_path("session-a", state_dir=tmp_path) == "/path/to/new.jsonl"


def test_read_returns_none_for_corrupted_state_file(tmp_path):
    state_dir = tmp_path
    state_dir.mkdir(exist_ok=True)
    (state_dir / "session-a.json").write_text("not json", encoding="utf-8")
    assert session_state.read_transcript_path("session-a", state_dir=state_dir) is None


def test_write_creates_state_dir_if_missing(tmp_path):
    nested = tmp_path / "nested" / "sessions"
    session_state.write_transcript_path("session-a", "/path/to/a.jsonl", state_dir=nested)
    assert session_state.read_transcript_path("session-a", state_dir=nested) == "/path/to/a.jsonl"
