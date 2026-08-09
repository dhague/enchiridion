"""TDD for save-session-opencode.py — the OpenCode /save-conversation adapter.

Covers the OpenCode-specific surface only: session_id resolution from the
session-tracker state, `opencode export` invocation, and the normalizer that
maps the export format (``info`` + ``messages[{info:{role}, parts[...]}]``)
onto the existing pure seam ``transcript_to_page()`` / ``write_capture()``
(#93). Those seams stay unchanged and are tested in test_transcript_capture.py.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from transcript_capture import CaptureError

# The production script lives at scripts/save-session-opencode.py — a
# hyphen, not an underscore, so the import system can't pick it up
# directly. Load it by file path, mirroring test_save_session_to_vault_cli.py.
_HYPHEN_PATH = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "save-session-opencode.py",
)
_spec = importlib.util.spec_from_file_location(
    "save_session_opencode", os.path.abspath(_HYPHEN_PATH),
)
assert _spec is not None and _spec.loader is not None  # the file above always exists
save_session_opencode = importlib.util.module_from_spec(_spec)
sys.modules["save_session_opencode"] = save_session_opencode
_spec.loader.exec_module(save_session_opencode)


# ---------------------------------------------------------------------------
# helpers — building an OpenCode-shaped export
# ---------------------------------------------------------------------------


def _text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def _message(role: str, parts: list[dict]) -> dict:
    return {"info": {"role": role}, "parts": parts}


def _export(*messages: dict) -> dict:
    return {"info": {"id": "ses_abc123"}, "messages": list(messages)}


def _two_turn_export() -> dict:
    return _export(
        _message("user", [_text_part("hi there")]),
        _message("assistant", [_text_part("hello back")]),
    )


# ---------------------------------------------------------------------------
# normalize_export — the pure seam: OpenCode messages -> (role, text) turns
# ---------------------------------------------------------------------------


def test_normalize_export_extracts_text_parts_in_order():
    turns = save_session_opencode.normalize_export(_two_turn_export())
    assert turns == [("user", "hi there"), ("assistant", "hello back")]


def test_normalize_export_keeps_only_text_parts():
    export = _export(
        _message("assistant", [
            {"type": "step-start"},
            {"type": "reasoning", "text": "thinking out loud"},
            {"type": "tool", "tool": "bash", "state": {"input": "ls"}},
            _text_part("the answer"),
            {"type": "step-finish"},
        ]),
    )
    turns = save_session_opencode.normalize_export(export)
    assert turns == [("assistant", "the answer")]


def test_normalize_export_skips_non_user_assistant_roles():
    export = _export(
        _message("system", [_text_part("system preamble")]),
        _message("tool", [_text_part("tool result text")]),
        _message("user", [_text_part("real question")]),
    )
    turns = save_session_opencode.normalize_export(export)
    assert turns == [("user", "real question")]


def test_normalize_export_skips_messages_with_no_text_parts():
    export = _export(
        _message("assistant", [{"type": "tool", "tool": "bash"}]),
        _message("user", [_text_part("still here")]),
    )
    turns = save_session_opencode.normalize_export(export)
    assert turns == [("user", "still here")]


def test_normalize_export_joins_multiple_text_parts_with_blank_line():
    export = _export(_message("assistant", [_text_part("first"), _text_part("second")]))
    turns = save_session_opencode.normalize_export(export)
    assert turns == [("assistant", "first\n\nsecond")]


def test_normalize_export_skips_blank_text_parts():
    export = _export(
        _message("user", [_text_part("   "), _text_part("the question")]),
        _message("assistant", [_text_part("")]),
    )
    turns = save_session_opencode.normalize_export(export)
    assert turns == [("user", "the question")]


def test_normalize_export_handles_malformed_messages():
    export = {
        "messages": [
            {},
            {"info": {"role": "user"}},  # no parts
            {"parts": [_text_part("no info")]},
        ]
    }
    assert save_session_opencode.normalize_export(export) == []


def test_normalize_export_handles_missing_messages_key():
    assert save_session_opencode.normalize_export({"info": {}}) == []


# ---------------------------------------------------------------------------
# encode_turns — (role, text) turns -> the CC JSONL shape transcript_to_page reads
# ---------------------------------------------------------------------------


def test_encode_turns_produces_cc_shaped_jsonl():
    lines = save_session_opencode.encode_turns(
        [("user", "question"), ("assistant", "answer")],
    )
    assert [json.loads(line) for line in lines] == [
        {
            "type": "user", "isMeta": False, "isSidechain": False,
            "message": {"role": "user", "content": "question"},
        },
        {
            "type": "assistant", "isMeta": False, "isSidechain": False,
            "message": {"role": "assistant", "content": "answer"},
        },
    ]


def test_encode_turns_feeds_transcript_to_page_unchanged():
    lines = save_session_opencode.encode_turns(
        [("user", "question"), ("assistant", "answer")],
    )
    filename, markdown = save_session_opencode.transcript_to_page(
        lines, session_id="ses_abc123", now=datetime(2026, 8, 9, 15, 30),
    )
    assert filename.endswith("-ses_abc123.md")
    assert "question" in markdown and "answer" in markdown


# ---------------------------------------------------------------------------
# find_session_id — resolve the current session from $OPENCODE_SESSION_ID
# ---------------------------------------------------------------------------


def _state_file(tmp_path, session_id):
    state_dir = tmp_path / ".opencode" / "wiki-knowledge" / "sessions"
    state_dir.mkdir(parents=True)
    (state_dir / f"{session_id}.json").write_text(
        json.dumps({"session_id": session_id}), encoding="utf-8",
    )
    return state_dir


def test_find_session_id_missing_env_var(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENCODE_SESSION_ID", raising=False)
    monkeypatch.chdir(tmp_path)
    session_id, error = save_session_opencode.find_session_id()
    assert session_id is None
    assert error is not None
    assert "OPENCODE_SESSION_ID" in error


def test_find_session_id_state_dir_not_located(tmp_path, monkeypatch):
    # Env var present but no .opencode/ ancestor of cwd — the plugin has
    # never recorded state in this project. Distinct from "no entry".
    monkeypatch.setenv("OPENCODE_SESSION_ID", "ses_abc123")
    monkeypatch.delenv("OPENCODE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    session_id, error = save_session_opencode.find_session_id()
    assert session_id is None
    assert error is not None
    assert "state" in error.lower()
    assert "session-tracker" in error


def test_find_session_id_state_dir_but_no_entry(tmp_path, monkeypatch):
    state_dir = tmp_path / ".opencode" / "wiki-knowledge" / "sessions"
    state_dir.mkdir(parents=True)
    monkeypatch.setenv("OPENCODE_SESSION_ID", "ses_abc123")
    monkeypatch.delenv("OPENCODE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    session_id, error = save_session_opencode.find_session_id()
    assert session_id is None
    assert error is not None
    assert "ses_abc123" in error
    assert str(state_dir) in error


def test_find_session_id_returns_session_id_when_state_present(tmp_path, monkeypatch):
    _state_file(tmp_path, "ses_abc123")
    monkeypatch.setenv("OPENCODE_SESSION_ID", "ses_abc123")
    monkeypatch.delenv("OPENCODE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    session_id, error = save_session_opencode.find_session_id()
    assert error is None, f"unexpected error: {error}"
    assert session_id == "ses_abc123"


def test_find_session_id_walks_up_for_opencode_ancestor(tmp_path, monkeypatch):
    # A session in a subdirectory: the .opencode/ marker is at the project
    # root, and cwd must walk up to find it (mirrors the CC walk-up).
    _state_file(tmp_path, "ses_abc123")
    subdir = tmp_path / "src" / "deep"
    subdir.mkdir(parents=True)
    monkeypatch.setenv("OPENCODE_SESSION_ID", "ses_abc123")
    monkeypatch.delenv("OPENCODE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(subdir)
    session_id, error = save_session_opencode.find_session_id()
    assert error is None, f"unexpected error: {error}"
    assert session_id == "ses_abc123"


def test_find_session_id_corrupt_state_file_is_missing(tmp_path, monkeypatch):
    state_dir = tmp_path / ".opencode" / "wiki-knowledge" / "sessions"
    state_dir.mkdir(parents=True)
    (state_dir / "ses_abc123.json").write_text("not json", encoding="utf-8")
    monkeypatch.setenv("OPENCODE_SESSION_ID", "ses_abc123")
    monkeypatch.delenv("OPENCODE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    session_id, error = save_session_opencode.find_session_id()
    assert session_id is None
    assert error is not None


# ---------------------------------------------------------------------------
# export_transcript — shell out to `opencode export <session_id>`
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_run(
    monkeypatch,
    *,
    stdout="{}",
    returncode=0,
    stderr="",
    which="opencode",
    records=None,
):
    """Fake the opencode subprocess: writes ``stdout`` into whatever real file
    the adapter passed as the subprocess's stdout (the adapter must shell out
    to a file, not a pipe — see export_transcript's docstring)."""
    monkeypatch.setattr(
        "save_session_opencode.shutil.which", lambda _name: which,
    )
    calls = []

    def _run(argv, **kwargs):
        calls.append(argv)
        out = kwargs.get("stdout")
        if out is not None:
            out.write(stdout)
            out.flush()
        return _FakeResult(returncode=returncode, stdout="", stderr=stderr)

    monkeypatch.setattr("save_session_opencode.subprocess.run", _run)
    if records is not None:
        records.append(calls)
    return calls


def test_export_transcript_raises_when_opencode_absent(monkeypatch):
    monkeypatch.setattr("save_session_opencode.shutil.which", lambda _name: None)
    with pytest.raises(CaptureError, match="opencode"):
        save_session_opencode.export_transcript("ses_abc123")


def test_export_transcript_invokes_opencode_export_with_session_id(monkeypatch):
    calls = _fake_run(monkeypatch, stdout=json.dumps(_two_turn_export()))
    data = save_session_opencode.export_transcript("ses_abc123")
    assert calls == [["opencode", "export", "ses_abc123"]]
    assert data["messages"][0]["info"]["role"] == "user"


def test_export_transcript_writes_stdout_to_a_real_file(monkeypatch):
    # Regression guard for the observed 1.18.15 behaviour: `opencode export`
    # truncates its JSON when stdout is a pipe, so the adapter must hand the
    # subprocess a real file it can read back in full.
    seen = {}
    monkeypatch.setattr("save_session_opencode.shutil.which", lambda _name: "opencode")

    def _run(argv, **kwargs):
        out = kwargs.get("stdout")
        seen["is_real_file"] = bool(
            out is not None
            and hasattr(out, "name")
            and os.path.isfile(out.name)
        )
        seen["stdout_is_pipe"] = out is subprocess.PIPE
        if out is not None:
            out.write("{}")
            out.flush()
        return _FakeResult()

    monkeypatch.setattr("save_session_opencode.subprocess.run", _run)
    save_session_opencode.export_transcript("ses_abc123")
    assert seen["is_real_file"] is True
    assert seen["stdout_is_pipe"] is False


def test_export_transcript_raises_on_nonzero_exit(monkeypatch):
    _fake_run(monkeypatch, returncode=1, stderr="boom")
    with pytest.raises(CaptureError, match="boom"):
        save_session_opencode.export_transcript("ses_abc123")


def test_export_transcript_raises_on_invalid_json(monkeypatch):
    _fake_run(monkeypatch, stdout="not json")
    with pytest.raises(CaptureError):
        save_session_opencode.export_transcript("ses_abc123")


# ---------------------------------------------------------------------------
# capture_session + main — the wiring
# ---------------------------------------------------------------------------


@pytest.fixture
def runnable_session(tmp_path, monkeypatch):
    """Vault + session-tracker state + a fake opencode export, ready to save.

    Deliberately complete: a test asserting "no file was written" only means
    something if the save would otherwise have succeeded.
    """
    _state_file(tmp_path, "ses_abc123")
    monkeypatch.setenv("OPENCODE_SESSION_ID", "ses_abc123")
    monkeypatch.delenv("OPENCODE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    vault = tmp_path / "vault"
    (vault / "wiki").mkdir(parents=True)
    (vault / "raw").mkdir()
    monkeypatch.setenv("WIKI_ROOT", str(vault))
    _fake_run(monkeypatch, stdout=json.dumps(_two_turn_export()))
    return vault


def _captures(vault):
    conversations = vault / "raw" / "conversations"
    return sorted(p.name for p in conversations.iterdir()) if conversations.is_dir() else []


def test_capture_session_writes_markdown_to_raw_conversations(runnable_session):
    rel = save_session_opencode.capture_session(wiki_root=runnable_session)
    assert rel.startswith("raw/conversations/")
    assert rel.endswith("-ses_abc123.md")
    written = runnable_session / rel
    assert "hi there" in written.read_text(encoding="utf-8")
    assert "hello back" in written.read_text(encoding="utf-8")


def test_capture_session_carries_the_slug(runnable_session):
    rel = save_session_opencode.capture_session(
        wiki_root=runnable_session, slug="opencode port",
    )
    assert rel.endswith("-opencode-port-ses_abc123.md")


def test_capture_session_not_enough_turns_raises(tmp_path, monkeypatch):
    _state_file(tmp_path, "ses_abc123")
    monkeypatch.setenv("OPENCODE_SESSION_ID", "ses_abc123")
    monkeypatch.delenv("OPENCODE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    vault = tmp_path / "vault"
    (vault / "wiki").mkdir(parents=True)
    (vault / "raw").mkdir()
    monkeypatch.setenv("WIKI_ROOT", str(vault))
    _fake_run(
        monkeypatch,
        stdout=json.dumps(
            _export(_message("user", [_text_part("just me")])),
        ),
    )
    with pytest.raises(CaptureError, match="[Nn]ot enough conversation"):
        save_session_opencode.capture_session(wiki_root=vault)


def test_main_rejects_an_unrecognised_argument_without_writing(runnable_session):
    with pytest.raises(SystemExit) as exc:
        save_session_opencode.main(["--slugg", "typo"])
    assert exc.value.code != 0
    assert _captures(runnable_session) == []


def test_main_help_exits_zero_without_writing(runnable_session):
    with pytest.raises(SystemExit) as exc:
        save_session_opencode.main(["--help"])
    assert exc.value.code == 0
    assert _captures(runnable_session) == []


def test_main_prints_the_written_path(runnable_session, capsys):
    save_session_opencode.main([])
    printed = capsys.readouterr().out.strip()
    assert printed.startswith("raw/conversations/")
    assert printed.endswith("-ses_abc123.md")
    assert len(_captures(runnable_session)) == 1
