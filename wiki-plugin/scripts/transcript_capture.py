"""Transcript capture: JSONL session transcript -> vault-ready raw markdown.

The reusable capability behind the /save-conversation skill: parsing a
Claude Code transcript, filtering it down to the real user/assistant
back-and-forth, rendering it to markdown, sanitising an agent-authored
slug, and binding a stable filename in the vault's raw inbox. All pure or
filesystem-local - no CLI concerns live here (see save-session-to-vault.py
for the argv-parsing adapter).

The name is bound once at first save: a re-save finds the existing file by
its short session id and rewrites it in place rather than renaming it, so
inbound raw_source links never break.
"""
import glob
import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from session_state import read_transcript_path, sessions_dir


# ---------------------------------------------------------------------------
# Pure seam: JSONL -> (filename, markdown)
# ---------------------------------------------------------------------------


SLUG_MAX_LENGTH = 60


def sanitize_slug(phrase, *, max_length: int = SLUG_MAX_LENGTH) -> str:
    """Reduce a free-text phrase to a filesystem-safe kebab-case slug.

    The phrase is authored by a language model, so this sanitizes rather
    than trusts: NFKD-fold to ASCII, lowercase, collapse everything
    outside ``[a-z0-9]`` to a single ``-``, strip the ends, and cap the
    length on a word boundary where there is one.

    Returns ``""`` when nothing survives (empty input, pure punctuation,
    non-transliterable script) - the caller falls back to the bare
    ``<date>-<short_id>`` name. The result is ``[a-z0-9-]`` only, so it
    needs no percent-encoding when it appears in a link destination.
    """
    if not phrase:
        return ""
    folded = unicodedata.normalize("NFKD", str(phrase))
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-")
    if len(slug) <= max_length:
        return slug
    # Look one character past the cap: if the cut lands on a separator,
    # the word before it is whole. Otherwise fall back to the last
    # boundary inside the window, and to a hard truncation when the slug
    # is one long word with no boundary at all.
    window = slug[: max_length + 1]
    head = window.rsplit("-", 1)[0] if "-" in window else ""
    return (head or slug[:max_length]).strip("-")


def _extract_text(content):
    """Pull the user's prose out of a transcript entry's ``content`` field.

    Two shapes: a plain string, or a list of content blocks. Only
    ``text`` blocks count; tool_use / tool_result / image blocks are
    dropped, since they are not part of the conversation the user
    re-reads later.
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = (block.get("text") or "").strip()
                if text:
                    parts.append(text)
        return "\n\n".join(parts).strip()
    return ""


def transcript_to_page(
    jsonl_lines,
    *,
    session_id: str,
    now: datetime,
    slug: str | None = None,
    user_label: str = "User",
    assistant_label: str = "Claude",
    min_turns: int = 2,
):
    """Render a session transcript into a vault-ready page.

    Pure: no I/O, no env, no filesystem. ``jsonl_lines`` is an iterable
    of raw lines from the Claude Code transcript (anything ``str.splitlines``
    yields works). Returns ``(filename, markdown)``.

    ``slug`` is a free-text phrase naming what the session covered; it is
    sanitized here, not trusted, and a phrase that sanitizes to nothing
    degrades to the bare ``<date>-<short_id>`` name.

    The pure form is what the /save-conversation script tests against; the
    same function is also reachable from a future ad-hoc REPL use, so the
    user / assistant speaker labels are parameters rather than baked in.
    The defaults are deliberately neutral - the calling code is free to
    pass ``user_label="Darren"`` to match the dogfooding vault's existing
    captures.

    Raises ``ValueError`` when the transcript yields fewer than
    ``min_turns`` non-empty exchanges - the calling script translates
    that into a "not enough conversation to save" exit.
    """
    turns = []
    for line in jsonl_lines:
        line = line.strip() if isinstance(line, str) else ""
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Skip synthetic entries (skill/tool-injected content, sub-agent
        # sidechains) - only keep the real back-and-forth.
        if entry.get("type") not in ("user", "assistant"):
            continue
        if entry.get("isMeta") or entry.get("isSidechain"):
            continue
        message = entry.get("message") or {}
        role = message.get("role")
        text = _extract_text(message.get("content"))
        if text:
            turns.append((role, text))

    if len(turns) < min_turns:
        raise ValueError(
            f"Transcript has {len(turns)} non-empty turn(s); need at least {min_turns}."
        )

    # The short id goes last: it is the session's identity, and putting
    # it at the end is what lets a re-save find the already-bound file
    # with a single '*-<short_id>.md' glob whether or not a slug is set.
    short_id = session_id.split("-")[0]
    safe_slug = sanitize_slug(slug)
    middle = f"{safe_slug}-" if safe_slug else ""
    filename = f"{now:%Y-%m-%d-%H%M}-{middle}{short_id}.md"

    lines = [
        f"# Session {session_id}",
        "",
        f"**Saved:** {now:%Y-%m-%d %H:%M}  ",
        "**Source:** Claude Code session transcript (save-conversation skill, enchiridion repo)",
        "",
        "---",
        "",
    ]
    for role, text in turns:
        label = user_label if role == "user" else assistant_label
        lines.append(f"## {label}")
        lines.append("")
        lines.append(text)
        lines.append("")

    return filename, "\n".join(lines)


class CaptureError(Exception):
    """A capture could not proceed; message is user-facing (CLI prints it as-is)."""


# ---------------------------------------------------------------------------
# Filesystem-local: resolve session state, write the capture
# ---------------------------------------------------------------------------


def find_transcript_path(env=None, cwd=None):
    """Return ``(transcript_path, error_message)``; exactly one is ``None``.

    Error messages are deliberately distinct so the user can tell apart:

    - ``$CLAUDE_CODE_SESSION_ID`` not set in env
    - state directory not located (no ``$CLAUDE_PROJECT_DIR`` and no
      ``.claude/`` ancestor of cwd) - the *resolution* failed
    - state directory located, but no entry for this session - the
      *SessionStart hook* didn't record this one
    - entry points to a transcript file that no longer exists
    """
    env = env if env is not None else os.environ
    # `cwd` is only used for the error message; the actual resolution
    # delegates to sessions_dir so the walk-up / $CLAUDE_PROJECT_DIR order
    # applies. Tests monkeypatch chdir, so Path.cwd() picks up the change.
    cwd_path = Path(cwd) if cwd is not None else Path.cwd()

    session_id = env.get("CLAUDE_CODE_SESSION_ID")
    if not session_id:
        return None, "$CLAUDE_CODE_SESSION_ID is not set in this environment."

    state_dir = sessions_dir(env=env)
    if not state_dir.is_dir():
        return None, (
            "Could not locate a session state directory. Searched "
            "$CLAUDE_PROJECT_DIR, then walked up from "
            f"{cwd_path.resolve()} for a '.claude/' ancestor, and did not "
            "find one. (Has the SessionStart hook ever run in this project? "
            "Start a new session in the project root and try again.)"
        )

    transcript_path = read_transcript_path(session_id, state_dir=state_dir)
    if not transcript_path:
        return None, (
            f"No state recorded for session {session_id} under {state_dir}. "
            "(If this session was started before the SessionStart hook was "
            "installed, its transcript was never recorded; start a new "
            "session and try again.)"
        )

    if not os.path.isfile(transcript_path):
        return None, f"Recorded transcript file does not exist: {transcript_path}"

    return transcript_path, None


def write_capture(wiki_root, filename: str, markdown: str, *, short_id: str) -> str:
    """Write the capture into ``raw/conversations/``; return its vault-relative path.

    One file per session, and **the name is bound at first save**. An
    earlier capture of the same session is found by globbing
    ``*-<short_id>.md`` and its path is reused *verbatim* - same
    timestamp, same slug - with the contents rewritten in place. A fresh
    name is composed only when nothing is found.

    So no raw file is ever renamed: the no-raw-renames rule holds, and
    inbound ``raw_source`` links to an already-ingested capture stay
    valid without any link rewriting.
    """
    conversations_dir = os.path.join(str(wiki_root), "raw", "conversations")
    os.makedirs(conversations_dir, exist_ok=True)

    already_bound = sorted(
        glob.glob(os.path.join(conversations_dir, f"*-{short_id}.md"))
    )
    out_path = already_bound[0] if already_bound else os.path.join(
        conversations_dir, filename
    )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    return os.path.relpath(out_path, str(wiki_root)).replace("\\", "/")


def capture_session(*, wiki_root, slug=None, env=None, cwd=None, now=None) -> str:
    """Find, render, and write this session's transcript; return its vault-relative path.

    The whole pipeline (find_transcript_path -> transcript_to_page ->
    write_capture) as one call, for a caller that just wants "save the
    current session" - the CLI adapter, and any future auto-save hook or
    batch importer. Raises CaptureError with a user-facing message on any
    failure; callers that want the individual steps still have them.
    """
    transcript_path, error = find_transcript_path(env=env, cwd=cwd)
    if error:
        raise CaptureError(error)

    session_id = os.path.splitext(os.path.basename(transcript_path))[0]
    with open(transcript_path, encoding="utf-8") as f:
        jsonl_lines = f.readlines()

    try:
        filename, markdown = transcript_to_page(
            jsonl_lines, session_id=session_id, now=now or datetime.now(), slug=slug,
        )
    except ValueError as exc:
        raise CaptureError(f"Not enough conversation to save: {exc}") from exc

    return write_capture(
        wiki_root, filename, markdown, short_id=session_id.split("-")[0],
    )
