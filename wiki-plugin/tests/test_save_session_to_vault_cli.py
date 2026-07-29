"""TDD for save-session-to-vault.py — the CLI adapter over transcript_capture.

Covers only the argv-parsing / wiring surface (main()); the pure pipeline
it calls into (transcript_to_page, sanitize_slug, find_transcript_path,
write_capture) is tested directly in test_transcript_capture.py (#61).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

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


def _entry(role: str, text: str, **flags) -> str:
    return json.dumps({
        "type": role,
        "isMeta": flags.get("isMeta", False),
        "isSidechain": flags.get("isSidechain", False),
        "message": {"role": role, "content": text},
    })


# ---------------------------------------------------------------------------
# main — argv is parsed, so a stray token can't become a silent write (#52)
# ---------------------------------------------------------------------------


@pytest.fixture
def runnable_session(tmp_path, monkeypatch):
    """A vault + recorded session + transcript, ready for main() to save.

    Deliberately complete: a test asserting "no file was written" only
    means something if the save would otherwise have succeeded.
    """
    project = tmp_path / "proj"
    state_dir = project / ".claude" / "wiki-knowledge" / "sessions"
    state_dir.mkdir(parents=True)
    transcript = tmp_path / "abc123-rest-of-uuid.jsonl"
    transcript.write_text(
        "\n".join([_entry("user", "hi"), _entry("assistant", "hello")]),
        encoding="utf-8",
    )
    (state_dir / "abc123-rest-of-uuid.json").write_text(
        json.dumps({"transcript_path": str(transcript)}), encoding="utf-8",
    )
    vault = tmp_path / "vault"
    (vault / "wiki").mkdir(parents=True)
    (vault / "raw").mkdir()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc123-rest-of-uuid")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.setenv("WIKI_ROOT", str(vault))
    return vault


def _captures(vault):
    conversations = vault / "raw" / "conversations"
    return sorted(p.name for p in conversations.iterdir()) if conversations.is_dir() else []


def test_main_bare_invocation_still_saves(runnable_session, capsys):
    save_session_to_vault.main([])
    printed = capsys.readouterr().out.strip()
    assert printed.startswith("raw/conversations/")
    assert printed.endswith("-abc123.md")
    assert len(_captures(runnable_session)) == 1


def test_main_with_slug_saves_under_the_slugged_name(runnable_session, capsys):
    save_session_to_vault.main(["--slug", "some topic"])
    printed = capsys.readouterr().out.strip()
    assert printed.endswith("-some-topic-abc123.md")


def test_main_rejects_an_unrecognised_argument_without_writing(runnable_session):
    # The #52 bug: argv was ignored entirely, so a mistyped flag ran a
    # full, destructive save whose only output was a path. Exit code
    # alone would not have caught it - the inbox has to stay untouched.
    with pytest.raises(SystemExit) as exc:
        save_session_to_vault.main(["--slugg", "typo"])
    assert exc.value.code != 0
    assert _captures(runnable_session) == []


def test_main_help_exits_zero_without_writing(runnable_session):
    with pytest.raises(SystemExit) as exc:
        save_session_to_vault.main(["--help"])
    assert exc.value.code == 0
    assert _captures(runnable_session) == []


def test_main_rejects_a_stray_positional_without_writing(runnable_session):
    # Not just flags: a bare token (a path someone assumed the script
    # took) must not become a save either.
    with pytest.raises(SystemExit) as exc:
        save_session_to_vault.main(["some/path.md"])
    assert exc.value.code != 0
    assert _captures(runnable_session) == []
