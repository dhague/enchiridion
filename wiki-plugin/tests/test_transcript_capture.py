"""TDD for transcript_capture.py — the pure pipeline behind /save-conversation.

Splitting it at a pure seam (transcript_to_page) so the JSONL filter +
markdown render can be tested without filesystem or env (#45). And
regression tests for the cwd-sensitive session-state lookup that bug
report hit on 2026-07-28.

Plus the agent-authored slug in the raw filename (#46): sanitization is
pure and property-tested, and the write path pins the one real invariant
— a re-save of the same session never changes the filename.

Extracted from test_save_session_to_vault.py (#61) so the tests match the
concept, not the CLI script name; test_save_session_to_vault_cli.py covers
the argv-parsing adapter only.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import session_state
import transcript_capture


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
    filename, markdown = transcript_capture.transcript_to_page(
        lines, session_id="abc123-uuid", now=now,
    )
    assert filename == "2026-07-28-1026-abc123.md"
    assert isinstance(markdown, str)


def test_transcript_to_page_filename_uses_short_id_and_now():
    lines = [_entry("user", "a"), _entry("assistant", "b")]
    now = datetime(2026, 1, 2, 3, 4)
    filename, _ = transcript_capture.transcript_to_page(
        lines, session_id="shortid-rest-of-uuid", now=now,
    )
    assert filename == "2026-01-02-0304-shortid.md"


def test_transcript_to_page_filters_is_meta_entries():
    lines = [
        _entry("user", "real question", isMeta=False),
        _entry("assistant", "real answer", isMeta=False),
        _entry("user", "synthetic", isMeta=True),
    ]
    _, markdown = transcript_capture.transcript_to_page(
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
    _, markdown = transcript_capture.transcript_to_page(
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
    _, markdown = transcript_capture.transcript_to_page(
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
    _, markdown = transcript_capture.transcript_to_page(
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
    _, markdown = transcript_capture.transcript_to_page(
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
        transcript_capture.transcript_to_page(
            [empty_list, empty_str, *real],
            session_id="sid", now=datetime(2026, 1, 1, 0, 0),
        )


def test_transcript_to_page_user_label_is_parameterizable():
    # The win in #45: the speaker label is no longer hardcoded to one
    # user; the function takes it as a parameter.
    lines = [_entry("user", "hi"), _entry("assistant", "hello")]
    _, markdown = transcript_capture.transcript_to_page(
        lines, session_id="sid", now=datetime(2026, 1, 1, 0, 0),
        user_label="Alex",
    )
    assert "## Alex" in markdown


def test_transcript_to_page_assistant_role_renders_as_claude():
    lines = [_entry("user", "hi"), _entry("assistant", "hello")]
    _, markdown = transcript_capture.transcript_to_page(
        lines, session_id="sid", now=datetime(2026, 1, 1, 0, 0),
    )
    assert "## Claude" in markdown


def test_transcript_to_page_user_label_default_is_neutral():
    # The default must not be a hardcoded name. ("Darren" used to be
    # baked in; that's the bug.)
    lines = [_entry("user", "hi"), _entry("assistant", "hello")]
    _, markdown = transcript_capture.transcript_to_page(
        lines, session_id="sid", now=datetime(2026, 1, 1, 0, 0),
    )
    assert "## Darren" not in markdown


def test_transcript_to_page_raises_for_too_few_turns():
    # A single user message with no reply is "not enough conversation to
    # save" - the original script's exit-1 path.
    lines = [_entry("user", "just me")]
    with pytest.raises(ValueError):
        transcript_capture.transcript_to_page(
            lines, session_id="sid", now=datetime(2026, 1, 1, 0, 0),
        )


def test_transcript_to_page_markdown_includes_session_id_in_header():
    lines = [_entry("user", "hi"), _entry("assistant", "hello")]
    _, markdown = transcript_capture.transcript_to_page(
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
    _, markdown = transcript_capture.transcript_to_page(
        lines, session_id="sid", now=datetime(2026, 1, 1, 0, 0),
    )
    assert "hi" in markdown and "hello" in markdown


# ---------------------------------------------------------------------------
# find_transcript_path — the resolution bug
# ---------------------------------------------------------------------------


def test_find_transcript_path_missing_session_id(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.chdir(tmp_path)
    transcript_path, error = transcript_capture.find_transcript_path()
    assert transcript_path is None
    assert error is not None
    assert "CLAUDE_CODE_SESSION_ID" in error


def test_find_transcript_path_state_dir_not_located(tmp_path, monkeypatch):
    # No $CLAUDE_PROJECT_DIR, no .claude/ ancestor of tmp_path - the
    # resolution must report a clear "no state dir located" error,
    # distinct from "no entry for this session".
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-abc")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    transcript_path, error = transcript_capture.find_transcript_path()
    assert transcript_path is None
    assert error is not None
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
    transcript_path, error = transcript_capture.find_transcript_path()
    assert transcript_path is None
    assert error is not None
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
    transcript_path, error = transcript_capture.find_transcript_path()
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
    transcript_path, error = transcript_capture.find_transcript_path()
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
    transcript_path, error = transcript_capture.find_transcript_path()
    assert transcript_path is None
    assert error is not None
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


# ---------------------------------------------------------------------------
# sanitize_slug — a model-authored filename is untrusted filesystem input
# ---------------------------------------------------------------------------

SLUG_SHAPE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def test_sanitize_slug_kebabs_an_ordinary_phrase():
    assert transcript_capture.sanitize_slug(
        "Charting wayfinder 33"
    ) == "charting-wayfinder-33"


def test_sanitize_slug_collapses_runs_of_punctuation():
    assert transcript_capture.sanitize_slug(
        "Charting: wayfinder #33!!"
    ) == "charting-wayfinder-33"


def test_sanitize_slug_strips_leading_and_trailing_punctuation():
    assert transcript_capture.sanitize_slug("--- hello ---") == "hello"


def test_sanitize_slug_folds_unicode_to_ascii():
    # NFKD decomposition drops the combining accents; what survives is
    # the ASCII skeleton.
    assert transcript_capture.sanitize_slug("Café naïve") == "cafe-naive"


def test_sanitize_slug_drops_unrepresentable_unicode():
    # Nothing ASCII survives, so this is the empty-slug fallback case.
    assert transcript_capture.sanitize_slug("日本語") == ""


def test_sanitize_slug_defuses_path_separators():
    assert transcript_capture.sanitize_slug(
        "../../etc/passwd"
    ) == "etc-passwd"
    assert transcript_capture.sanitize_slug(
        r"C:\Windows\System32"
    ) == "c-windows-system32"


def test_sanitize_slug_all_punctuation_is_empty():
    for phrase in ("..", "///", "###", "   ", "-", "!@#$%^&*()"):
        assert transcript_capture.sanitize_slug(phrase) == "", phrase


def test_sanitize_slug_empty_and_none_are_empty():
    assert transcript_capture.sanitize_slug("") == ""
    assert transcript_capture.sanitize_slug(None) == ""


def test_sanitize_slug_caps_on_a_word_boundary():
    phrase = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda"
    slug = transcript_capture.sanitize_slug(phrase)
    assert len(slug) <= 60
    # Cut between words, so no half-word tail.
    assert slug == "alpha-beta-gamma-delta-epsilon-zeta-eta-theta-iota-kappa"


def test_sanitize_slug_hard_truncates_a_single_long_word():
    # No boundary to cut on, so the cap still has to hold.
    slug = transcript_capture.sanitize_slug("x" * 200)
    assert slug == "x" * 60


@given(st.text())
@settings(max_examples=300)
def test_sanitize_slug_shape_property(phrase):
    slug = transcript_capture.sanitize_slug(phrase)
    assert slug == "" or SLUG_SHAPE.match(slug), repr(slug)


@given(st.text(), st.integers(min_value=1, max_value=120))
@settings(max_examples=300)
def test_sanitize_slug_length_property(phrase, max_length):
    slug = transcript_capture.sanitize_slug(phrase, max_length=max_length)
    assert len(slug) <= max_length, repr(slug)


@given(st.text())
@settings(max_examples=300)
def test_sanitize_slug_is_idempotent(phrase):
    once = transcript_capture.sanitize_slug(phrase)
    assert transcript_capture.sanitize_slug(once) == once


# ---------------------------------------------------------------------------
# transcript_to_page — the slug in the filename
# ---------------------------------------------------------------------------


def _two_turns():
    return [_entry("user", "hi"), _entry("assistant", "hello")]


def test_transcript_to_page_filename_carries_the_slug():
    filename, _ = transcript_capture.transcript_to_page(
        _two_turns(),
        session_id="1dc3e094-rest-of-uuid",
        now=datetime(2026, 7, 28, 14, 30),
        slug="charting wayfinder 33",
    )
    assert filename == "2026-07-28-1430-charting-wayfinder-33-1dc3e094.md"


def test_transcript_to_page_sanitizes_the_slug_it_is_given():
    # The caller is a language model; the script is the enforcement point.
    filename, _ = transcript_capture.transcript_to_page(
        _two_turns(),
        session_id="1dc3e094-rest",
        now=datetime(2026, 7, 28, 14, 30),
        slug="../../Charting: Wayfinder #33!",
    )
    assert filename == "2026-07-28-1430-charting-wayfinder-33-1dc3e094.md"


def test_transcript_to_page_without_slug_keeps_the_bare_shape():
    filename, _ = transcript_capture.transcript_to_page(
        _two_turns(),
        session_id="1dc3e094-rest",
        now=datetime(2026, 7, 28, 14, 30),
    )
    assert filename == "2026-07-28-1430-1dc3e094.md"


def test_transcript_to_page_slug_sanitizing_to_empty_falls_back():
    # A slug of pure punctuation must not leave a doubled separator
    # behind - it degrades to the no-slug shape.
    filename, _ = transcript_capture.transcript_to_page(
        _two_turns(),
        session_id="1dc3e094-rest",
        now=datetime(2026, 7, 28, 14, 30),
        slug="///",
    )
    assert filename == "2026-07-28-1430-1dc3e094.md"


def test_transcript_to_page_short_id_is_always_the_filename_suffix():
    # This is what makes the '*-<short_id>.md' dedup glob work with and
    # without a slug: identity lives at the end of the name.
    with_slug, _ = transcript_capture.transcript_to_page(
        _two_turns(), session_id="sid1234-rest",
        now=datetime(2026, 1, 1, 0, 0), slug="anything at all",
    )
    without, _ = transcript_capture.transcript_to_page(
        _two_turns(), session_id="sid1234-rest",
        now=datetime(2026, 1, 1, 0, 0),
    )
    assert with_slug.endswith("-sid1234.md")
    assert without.endswith("-sid1234.md")


# ---------------------------------------------------------------------------
# write_capture — the name is bound once, at first save
# ---------------------------------------------------------------------------


def test_write_capture_writes_the_composed_name_on_first_save(tmp_path):
    rel = transcript_capture.write_capture(
        tmp_path, "2026-07-28-1430-a-slug-abc123.md", "body", short_id="abc123",
    )
    assert rel == "raw/conversations/2026-07-28-1430-a-slug-abc123.md"
    written = tmp_path / rel
    assert written.read_text(encoding="utf-8") == "body"


def test_write_capture_creates_the_conversations_dir(tmp_path):
    assert not (tmp_path / "raw" / "conversations").exists()
    transcript_capture.write_capture(
        tmp_path, "2026-01-01-0000-abc123.md", "body", short_id="abc123",
    )
    assert (tmp_path / "raw" / "conversations").is_dir()


def test_write_capture_resave_reuses_the_bound_name(tmp_path):
    # The invariant: a second save of the same session never changes the
    # filename, whatever slug is passed the second time. No rename ever
    # happens, so #28's no-raw-renames rule holds and inbound links to
    # the raw file stay valid.
    first = transcript_capture.write_capture(
        tmp_path, "2026-07-28-1430-first-slug-abc123.md", "v1", short_id="abc123",
    )
    second = transcript_capture.write_capture(
        tmp_path, "2026-07-29-0900-a-totally-different-slug-abc123.md", "v2",
        short_id="abc123",
    )
    assert second == first
    assert (tmp_path / first).read_text(encoding="utf-8") == "v2"
    # Exactly one file for this session - no duplicate, no leftover.
    assert sorted(p.name for p in (tmp_path / "raw" / "conversations").iterdir()) == [
        "2026-07-28-1430-first-slug-abc123.md"
    ]


def test_write_capture_resave_reuses_a_slugless_bound_name(tmp_path):
    # First save had no slug; a later save that supplies one must still
    # not rename the artifact.
    first = transcript_capture.write_capture(
        tmp_path, "2026-07-28-1430-abc123.md", "v1", short_id="abc123",
    )
    second = transcript_capture.write_capture(
        tmp_path, "2026-07-29-0900-now-with-a-slug-abc123.md", "v2",
        short_id="abc123",
    )
    assert second == first == "raw/conversations/2026-07-28-1430-abc123.md"


def test_write_capture_leaves_other_sessions_alone(tmp_path):
    other = tmp_path / "raw" / "conversations" / "2026-07-01-1200-zzz999.md"
    other.parent.mkdir(parents=True)
    other.write_text("someone else", encoding="utf-8")
    rel = transcript_capture.write_capture(
        tmp_path, "2026-07-28-1430-mine-abc123.md", "mine", short_id="abc123",
    )
    assert rel == "raw/conversations/2026-07-28-1430-mine-abc123.md"
    assert other.read_text(encoding="utf-8") == "someone else"


def test_write_capture_returns_a_posix_relative_path(tmp_path):
    rel = transcript_capture.write_capture(
        tmp_path, "2026-01-01-0000-abc123.md", "body", short_id="abc123",
    )
    assert "\\" not in rel
    assert rel.startswith("raw/conversations/")
