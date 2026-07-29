"""Save the current session's conversation as a raw markdown artifact in the
enchiridion-vault inbox (raw/conversations/), for later wiki-ingest.

Run manually (via the save-conversation skill), not as a hook, so there is
no hook JSON payload here to read transcript_path from directly. Instead,
the plugin's `SessionStart` hook (hooks/store_transcript_path.py) records
transcript_path per session_id, under this project's
`.claude/wiki-knowledge/sessions/` (gitignored), as each session starts;
this script looks itself up by the $CLAUDE_CODE_SESSION_ID env var (which
Claude Code exports to every process it launches, including this one) and
its own cwd, which must match the project the hook recorded it under.

Prints the vault-relative path of the raw file it wrote, so the calling
skill can pass it straight to wiki-ingest.

The JSONL filter + markdown render + filename scheme live in
``transcript_to_page`` (pure), so the rules can be tested without
filesystem or env; vault-root resolution happens in ``main()`` so it can
be injected.

The filename carries an optional ``--slug`` naming what the session
covered, supplied by the calling agent. Identity lives in the short
session id at the *end* of the name, so the name is bound once at first
save: a re-save finds the existing file by that id and rewrites it in
place rather than renaming it.
"""
import argparse
import glob
import json
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from session_state import read_transcript_path, sessions_dir  # noqa: E402
from vault import resolve_vault_root  # noqa: E402


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


# ---------------------------------------------------------------------------
# Adapter: resolve session state, dispatch to the pure seam, write the file
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


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Save this session's transcript into the vault's raw inbox.",
    )
    parser.add_argument(
        "--slug",
        default=None,
        help=(
            "Short phrase naming what this session covered, used in the "
            "filename. Sanitized to [a-z0-9-]; ignored if nothing survives. "
            "Only used on the first save - a re-save keeps the bound name."
        ),
    )
    args = parser.parse_args(argv)

    transcript_path, error = find_transcript_path()
    if error:
        print(error, file=sys.stderr)
        sys.exit(1)

    session_id = os.path.splitext(os.path.basename(transcript_path))[0]

    with open(transcript_path, encoding="utf-8") as f:
        jsonl_lines = f.readlines()

    try:
        filename, markdown = transcript_to_page(
            jsonl_lines, session_id=session_id, now=datetime.now(), slug=args.slug,
        )
    except ValueError as exc:
        print(f"Not enough conversation to save: {exc}", file=sys.stderr)
        sys.exit(1)

    # Vault root is resolved at call time, not at import, so the module
    # is importable in a test environment that has no vault on disk.
    wiki_root = resolve_vault_root()
    print(
        write_capture(
            wiki_root, filename, markdown, short_id=session_id.split("-")[0],
        )
    )


if __name__ == "__main__":
    main()
