"""TDD for save-session-to-vault.py — the /save-conversation script.

Splitting it at a pure seam (transcript_to_page) so the JSONL filter +
markdown render can be tested without filesystem or env (#45). And
regression tests for the cwd-sensitive session-state lookup that bug
report hit on 2026-07-28.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

# The production script lives at scripts/save-session-to-vault.py — a
# hyphen, not an underscore, so the import system can't pick it up
# directly. Load it by file path; the module name used in tests is the
# underscored form for readability.
_HYPHEN_PATH = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "save-session-to-vault.py",
)
_spec = importlib.util.spec_from_file_location(
    "save_session_to_vault", os.path.abspath(_HYPHEN_PATH),
)
save_session_to_vault = importlib.util.module_from_spec(_spec)
sys.modules["save_session_to_vault"] = save_session_to_vault
_spec.loader.exec_module(save_session_to_vault)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import session_state  # noqa: E402


# ---------------------------------------------------------------------------
# transcript_to_page — the pure seam
# ---------------------------------------------------------------------------


def _entry(role: str, text: str, **flags) -> str:
    return json.dumps({
        "type": role,
        "isMeta": flags.get("isMeta", False),
        "isSidechain": flags.get("isSidechain", False),
        "message": {"role": role, "content": text},
    })


def test_transcript_to_page_returns_filename_and_markdown():
    lines = [
        _entry("user", "Hello"),
        _entry("assistant", "Hi there"),
    ]
    now = datetime(2026, 7, 28, 10, 26)
    filename, markdown = save_session_to_vault.transcript_to_page(
        lines, session_id="abc123-uuid", now=now,
    )
    assert filename == "2026-07-28-1026-abc123-session.md"
    assert isinstance(markdown, str)


def test_transcript_to_page_filename_uses_short_id_and_now():
    lines = [_entry("user", "a"), _entry("assistant", "b")]
    now = datetime(2026, 1, 2, 3, 4)
    filename, _ = save_session_to_vault.transcript_to_page(
        lines, session_id="shortid-rest-of-uuid", now=now,
    )
    assert filename == "2026-01-02-0304-shortid-session.md"


def test_transcript_to_page_filters_is_meta_entries():
    lines = [
        _entry("user", "real question", isMeta=False),
        _entry("assistant", "real answer", isMeta=False),
        _entry("user", "synthetic", isMeta=True),
    ]
    _, markdown = save_session_to_vault.transcript_to_page(
        lines, session_id="sid", now=datetime(2026, 1, 1, 0, 0),
    )
    assert "real question" in markdown
    assert "real answer" in markdown
    assert "synthetic" not in markdown


def test_transcript_to_page_filters_is_sidechain_entries():
    # isSidechain flags sub-agent traffic, which is not the user's
    # back-and-forth with the main thread.
    lines = [
        _entry("user", "main", isSidechain=False),
        _entry("assistant", "main reply", isSidechain=False),
        _entry("user", "sub-agent scratch", isSidechain=True),
    ]
    _, markdown = save_session_to_vault.transcript_to_page(
        lines, session_id="sid", now=datetime(2026, 1, 1, 0, 0),
    )
    assert "sub-agent scratch" not in markdown


def test_transcript_to_page_filters_non_user_assistant_types():
    lines = [
        _entry("user", "u1"),
        _entry("assistant", "a1"),
        json.dumps({"type": "file-history-snapshot", "message": {"role": "system", "content": "x"}}),
        json.dumps({"type": "system", "message": {"role": "system", "content": "y"}}),
    ]
    _, markdown = save_session_to_vault.transcript_to_page(
        lines, session_id="sid", now=datetime(2026, 1, 1, 0, 0),
    )
    assert "u1" in markdown and "a1" in markdown
    assert "x" not in markdown
    assert "y" not in markdown


def test_transcript_to_page_extracts_text_from_string_content():
    lines = [
        _entry("user", "plain string"),
        _entry("assistant", "another plain string"),
    ]
    _, markdown = save_session_to_vault.transcript_to_page(
        lines, session_id="sid", now=datetime(2026, 1, 1, 0, 0),
    )
    assert "plain string" in markdown
    assert "another plain string" in markdown


def test_transcript_to_page_extracts_text_from_list_of_dicts():
    entry = {
        "type": "user",
        "isMeta": False,
        "isSidechain": False,
        "message": {
            "role": "user",
            "content": [
                {"type": "text", "text": "first block"},
                {"type": "text", "text": "second block"},
            ],
        },
    }
    reply = {
        "type": "assistant",
        "isMeta": False,
        "isSidechain": False,
        "message": {"role": "assistant", "content": "ack"},
    }
    _, markdown = save_session_to_vault.transcript_to_page(
        [json.dumps(entry), json.dumps(reply)],
        session_id="sid", now=datetime(2026, 1, 1, 0, 0),
    )
    assert "first block" in markdown
    assert "second block" in markdown


def test_transcript_to_page_skips_empty_text_blocks():
    # Content can arrive as an empty list, or with empty text blocks; both
    # must not produce a turn.
    empty_list = json.dumps({
        "type": "user", "isMeta": False, "isSidechain": False,
        "message": {"role": "user", "content": []},
    })
    empty_str = json.dumps({
        "type": "user", "isMeta": False, "isSidechain": False,
        "message": {"role": "user", "content": "   "},
    })
    real = [_entry("assistant", "the reply")]
    with pytest.raises(ValueError):
        save_session_to_vault.transcript_to_page(
            [empty_list, empty_str, *real],
            session_id="sid", now=datetime(2026, 1, 1, 0, 0),
        )


def test_transcript_to_page_user_label_is_parameterizable():
    # The win in #45: the speaker label is no longer hardcoded to one
    # user; the function takes it as a parameter.
    lines = [_entry("user", "hi"), _entry("assistant", "hello")]
    _, markdown = save_session_to_vault.transcript_to_page(
        lines, session_id="sid", now=datetime(2026, 1, 1, 0, 0),
        user_label="Alex",
    )
    assert "## Alex" in markdown


def test_transcript_to_page_assistant_role_renders_as_claude():
    lines = [_entry("user", "hi"), _entry("assistant", "hello")]
    _, markdown = save_session_to_vault.transcript_to_page(
        lines, session_id="sid", now=datetime(2026, 1, 1, 0, 0),
    )
    assert "## Claude" in markdown


def test_transcript_to_page_user_label_default_is_neutral():
    # The default must not be a hardcoded name. ("Darren" used to be
    # baked in; that's the bug.)
    lines = [_entry("user", "hi"), _entry("assistant", "hello")]
    _, markdown = save_session_to_vault.transcript_to_page(
        lines, session_id="sid", now=datetime(2026, 1, 1, 0, 0),
    )
    assert "## Darren" not in markdown


def test_transcript_to_page_raises_for_too_few_turns():
    # A single user message with no reply is "not enough conversation to
    # save" - the original script's exit-1 path.
    lines = [_entry("user", "just me")]
    with pytest.raises(ValueError):
        save_session_to_vault.transcript_to_page(
            lines, session_id="sid", now=datetime(2026, 1, 1, 0, 0),
        )


def test_transcript_to_page_markdown_includes_session_id_in_header():
    lines = [_entry("user", "hi"), _entry("assistant", "hello")]
    _, markdown = save_session_to_vault.transcript_to_page(
        lines, session_id="abc-123", now=datetime(2026, 1, 1, 0, 0),
    )
    assert markdown.startswith("# Session abc-123")


def test_transcript_to_page_survives_garbled_jsonl_lines():
    # JSONL files in the wild have been seen with non-JSON preamble /
    # tail; those lines must be skipped, not crash the parse.
    lines = [
        "not json",
        _entry("user", "hi"),
        "{malformed",
        _entry("assistant", "hello"),
    ]
    _, markdown = save_session_to_vault.transcript_to_page(
        lines, session_id="sid", now=datetime(2026, 1, 1, 0, 0),
    )
    assert "hi" in markdown and "hello" in markdown


# ---------------------------------------------------------------------------
# find_transcript_path — the resolution bug
# ---------------------------------------------------------------------------


def test_find_transcript_path_missing_session_id(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.chdir(tmp_path)
    transcript_path, error = save_session_to_vault.find_transcript_path()
    assert transcript_path is None
    assert "CLAUDE_CODE_SESSION_ID" in error


def test_find_transcript_path_state_dir_not_located(tmp_path, monkeypatch):
    # No $CLAUDE_PROJECT_DIR, no .claude/ ancestor of tmp_path - the
    # resolution must report a clear "no state dir located" error,
    # distinct from "no entry for this session".
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-abc")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    transcript_path, error = save_session_to_vault.find_transcript_path()
    assert transcript_path is None
    assert "state" in error.lower()
    # Distinct from the "no entry" message: must not blame the SessionStart hook.
    assert "SessionStart hook may not have run" not in error


def test_find_transcript_path_state_dir_located_but_no_entry(tmp_path, monkeypatch):
    # State dir exists, but no file for this session_id - the message
    # must be distinct from "state dir not located".
    state_dir = tmp_path / ".claude" / "wiki-knowledge" / "sessions"
    state_dir.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-abc")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    transcript_path, error = save_session_to_vault.find_transcript_path()
    assert transcript_path is None
    assert "sid-abc" in error
    # Must mention the state dir location so the user can see where it looked.
    assert str(state_dir) in error
    # Must not say "could not locate" - that's a different failure.
    assert "could not locate" not in error.lower()


def test_find_transcript_path_from_subdirectory_finds_state_in_ancestor(tmp_path, monkeypatch):
    # Regression: the 2026-07-28 bug. /save-conversation was launched from
    # a subdirectory; the reader used Path.cwd() and looked there, missing
    # the state file in the project root. After the fix, the resolution
    # walks up for a .claude/ ancestor and finds the state.
    state_dir = tmp_path / ".claude" / "wiki-knowledge" / "sessions"
    state_dir.mkdir(parents=True)
    transcript = tmp_path / "transcripts" / "sid-abc.jsonl"
    transcript.parent.mkdir()
    transcript.write_text("{}", encoding="utf-8")
    (state_dir / "sid-abc.json").write_text(
        json.dumps({"transcript_path": str(transcript)}), encoding="utf-8",
    )
    subdir = tmp_path / "src" / "deep" / "nested"
    subdir.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-abc")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(subdir)
    transcript_path, error = save_session_to_vault.find_transcript_path()
    assert error is None, f"unexpected error: {error}"
    assert transcript_path == str(transcript)


def test_find_transcript_path_honors_claim_project_dir_env(tmp_path, monkeypatch):
    # $CLAUDE_PROJECT_DIR wins over the walk-up - it's a direct
    # statement of which project this session belongs to. The state
    # lives at <project_root>/.claude/wiki-knowledge/sessions/ per the
    # convention in session_state.py.
    state_dir = tmp_path / ".claude" / "wiki-knowledge" / "sessions"
    state_dir.mkdir(parents=True)
    transcript = tmp_path / "x.jsonl"
    transcript.write_text("{}", encoding="utf-8")
    (state_dir / "sid-abc.json").write_text(
        json.dumps({"transcript_path": str(transcript)}), encoding="utf-8",
    )
    # cwd has no .claude/ ancestor at all - only the env var can win.
    other_cwd = tmp_path / "scratch"
    other_cwd.mkdir()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-abc")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.chdir(other_cwd)
    transcript_path, error = save_session_to_vault.find_transcript_path()
    assert error is None, f"unexpected error: {error}"
    assert transcript_path == str(transcript)


def test_find_transcript_path_recorded_transcript_missing(tmp_path, monkeypatch):
    state_dir = tmp_path / ".claude" / "wiki-knowledge" / "sessions"
    state_dir.mkdir(parents=True)
    (state_dir / "sid-abc.json").write_text(
        json.dumps({"transcript_path": str(tmp_path / "ghost.jsonl")}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-abc")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    transcript_path, error = save_session_to_vault.find_transcript_path()
    assert transcript_path is None
    assert "ghost.jsonl" in error
    assert "does not exist" in error


# ---------------------------------------------------------------------------
# session_state.sessions_dir — the resolution order
# ---------------------------------------------------------------------------


def test_sessions_dir_walks_up_for_claude_ancestor(tmp_path, monkeypatch):
    # The bug fix: when there's no $CLAUDE_PROJECT_DIR and no injected
    # root, sessions_dir() must walk up from cwd to find a .claude/
    # marker, not blindly return cwd.
    (tmp_path / ".claude").mkdir()  # the marker that makes tmp_path the project root
    subdir = tmp_path / "src" / "deep"
    subdir.mkdir(parents=True)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(subdir)
    assert session_state.sessions_dir() == (
        tmp_path / ".claude" / "wiki-knowledge" / "sessions"
    )


def test_sessions_dir_claim_project_dir_overrides_walkup(tmp_path, monkeypatch):
    # $CLAUDE_PROJECT_DIR wins over both walk-up and cwd fallback.
    state_dir_root = tmp_path / "elsewhere"
    state_dir_root.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(state_dir_root))
    monkeypatch.chdir(tmp_path)  # cwd has no .claude/ ancestor
    assert session_state.sessions_dir() == (
        state_dir_root / ".claude" / "wiki-knowledge" / "sessions"
    )


def test_sessions_dir_root_injection_skips_env_and_cwd(tmp_path, monkeypatch):
    # When `root` is given, the env var and cwd are bypassed entirely -
    # callers (tests, the hook) use this to point at a specific project.
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/should/be/ignored")
    monkeypatch.chdir(tmp_path)
    assert session_state.sessions_dir("/explicit") == (
        Path("/explicit") / ".claude" / "wiki-knowledge" / "sessions"
    )


def test_sessions_dir_falls_back_to_cwd_when_no_marker(tmp_path, monkeypatch):
    # If neither the env var nor a .claude/ ancestor exists, fall back to
    # cwd - the original behaviour, preserved as a last resort.
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert session_state.sessions_dir() == (
        tmp_path / ".claude" / "wiki-knowledge" / "sessions"
    )
