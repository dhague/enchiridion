"""Save the current session's conversation as a raw markdown artifact in the
enchiridion-vault inbox (raw/conversations/), for later wiki-ingest.

Run manually (via the save-conversation skill), not as a hook, so there is
no hook JSON payload here to read transcript_path from directly. Instead,
the plugin's `SessionStart` hook (hooks/store_transcript_path.py) records
transcript_path per session_id, under this project's
`.claude/wiki-knowledge/sessions/` (gitignored), as each session starts;
this script looks itself up by the $CLAUDE_CODE_SESSION_ID env var (which
Claude Code exports to every process it launches, including this one) and
its own cwd, which must match the project the hook recorded it under. This
replaced a "most-recently-modified transcript in this project's directory"
heuristic that broke when more than one session was running against the
same project in parallel - see #23.

Prints the vault-relative path of the raw file it wrote, so the calling
skill can pass it straight to wiki-ingest.

#45 split the JSONL filter + markdown render + filename scheme into
``transcript_to_page`` (pure) so the rules can be tested without
filesystem or env, and moved the vault-root resolution from import time
to ``main()`` so it can be injected. The resolution bug from the
2026-07-28 report is fixed in ``session_state.sessions_dir``.
"""
import glob
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from session_state import read_transcript_path, sessions_dir  # noqa: E402
from vault import resolve_vault_root  # noqa: E402


# ---------------------------------------------------------------------------
# Pure seam: JSONL -> (filename, markdown)
# ---------------------------------------------------------------------------


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
    user_label: str = "User",
    assistant_label: str = "Claude",
    min_turns: int = 2,
):
    """Render a session transcript into a vault-ready page.

    Pure: no I/O, no env, no filesystem. ``jsonl_lines`` is an iterable
    of raw lines from the Claude Code transcript (anything ``str.splitlines``
    yields works). Returns ``(filename, markdown)``.

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

    short_id = session_id.split("-")[0]
    filename = f"{now:%Y-%m-%d-%H%M}-{short_id}-session.md"

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

    The first two were collapsed into one misleading "hook may not have
    run yet" message before #45; a not-found now distinguishes
    *location* from *identity* the same way #23 distinguished
    *identity* from *recency*.
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


def main():
    transcript_path, error = find_transcript_path()
    if error:
        print(error, file=sys.stderr)
        sys.exit(1)

    session_id = os.path.splitext(os.path.basename(transcript_path))[0]

    with open(transcript_path, encoding="utf-8") as f:
        jsonl_lines = f.readlines()

    try:
        filename, markdown = transcript_to_page(
            jsonl_lines, session_id=session_id, now=datetime.now(),
        )
    except ValueError as exc:
        print(f"Not enough conversation to save: {exc}", file=sys.stderr)
        sys.exit(1)

    # Vault root is resolved at call time, not at import, so the module
    # is importable in a test environment that has no vault on disk.
    wiki_root = resolve_vault_root()
    conversations_dir = os.path.join(str(wiki_root), "raw", "conversations")
    os.makedirs(conversations_dir, exist_ok=True)
    out_path = os.path.join(conversations_dir, filename)

    # One file per session: remove any earlier capture of this same
    # session so the timestamp in the filename stays current rather
    # than accumulating a new file per save.
    short_id = session_id.split("-")[0]
    for existing in glob.glob(
        os.path.join(conversations_dir, f"*-{short_id}-session.md")
    ):
        os.remove(existing)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    raw_relative_path = os.path.relpath(out_path, str(wiki_root)).replace("\\", "/")
    print(raw_relative_path)


if __name__ == "__main__":
    main()
