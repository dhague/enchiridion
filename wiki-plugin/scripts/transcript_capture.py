"""Transcript capture: JSONL session transcript -> vault-ready raw markdown.

The capability behind the /save-conversation skill — parse a Claude Code
transcript, filter to the real user/assistant back-and-forth, render to
markdown, sanitise an agent-authored slug, bind a filename in the vault's raw
inbox. Pure or filesystem-local only; argv parsing lives in
save-session-to-vault.py.

**The name is bound once, at first save.** A re-save finds the existing file
by its short session id and rewrites it in place rather than renaming, so
inbound raw_source links never break.
"""
import glob
import json
import os
import re
import unicodedata
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from session_state import read_transcript_path, sessions_dir


# ---------------------------------------------------------------------------
# Pure seam: JSONL -> (filename, markdown)
# ---------------------------------------------------------------------------


SLUG_MAX_LENGTH = 60


def sanitize_slug(phrase, *, max_length: int = SLUG_MAX_LENGTH) -> str:
    """Reduce a free-text phrase to a filesystem-safe kebab-case slug.

    The phrase is model-authored, so this sanitizes rather than trusts:
    NFKD-fold to ASCII, lowercase, collapse non-``[a-z0-9]`` runs to one
    ``-``, strip the ends, cap on a word boundary where there is one. The
    result is ``[a-z0-9-]`` only, so it never needs percent-encoding in a
    link destination.

    ``""`` when nothing survives (empty, pure punctuation,
    non-transliterable script) — the caller then falls back to the bare
    ``<date>-<short_id>`` name.
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
    """Pull the prose out of a transcript entry's ``content`` field.

    Two shapes: a plain string, or a list of blocks. Only ``text`` blocks
    count — tool_use / tool_result / image aren't part of the conversation
    anyone re-reads later.
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

    Speaker labels are parameters, not baked in, so a caller can match an
    existing vault's captures.

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
        # Drop synthetic entries (skill/tool-injected content, sub-agent
        # sidechains); keep only the real back-and-forth.
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

    # The short id goes last so a re-save can find the already-bound file
    # with one '*-<short_id>.md' glob, slug present or not.
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


def find_transcript_path(
    env: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
) -> tuple[str | None, str | None]:
    """Return ``(transcript_path, error_message)``; exactly one is ``None``.

    Four distinct failures, kept distinct so the user can tell them apart:
    no ``$CLAUDE_CODE_SESSION_ID``; state directory not located (resolution
    failed); located but no entry for this session (the SessionStart hook
    never ran); entry pointing at a transcript that no longer exists.
    """
    env = env if env is not None else os.environ
    # `cwd` only feeds the error message — resolution delegates to
    # sessions_dir for the walk-up / $CLAUDE_PROJECT_DIR order.
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

    One file per session. An earlier capture is found by globbing
    ``*-<short_id>.md`` and its path reused *verbatim* — same timestamp, same
    slug — with contents rewritten in place; ``filename`` is used only when
    nothing is found. So no raw file is ever renamed, and inbound
    ``raw_source`` links stay valid with no link rewriting.
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


def capture_session(
    *,
    wiki_root: Path | str,
    slug: str | None = None,
    env: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
    now: datetime | None = None,
) -> str:
    """Find, render, and write this session's transcript; return its vault-relative path.

    The whole pipeline (find_transcript_path -> transcript_to_page ->
    write_capture) in one call. Raises CaptureError with a user-facing
    message on any failure; the individual steps remain available.
    """
    transcript_path, error = find_transcript_path(env=env, cwd=cwd)
    if error:
        raise CaptureError(error)
    assert transcript_path is not None  # exactly one of the pair is None

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
