"""Per-session state: maps a Claude Code session_id to its transcript_path.

The `SessionStart` hook (``hooks/store_transcript_path.py``) writes;
``skills/save-conversation`` reads by ``$CLAUDE_CODE_SESSION_ID``, so it never
guesses which concurrently running session is "current".

State lives under the *project's* ``.claude/wiki-knowledge/sessions/``
(gitignored), not the vault — in query-from-anywhere mode the vault is
somewhere else entirely. One JSON file per session_id, so parallel sessions
sharing a project don't clobber each other.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def sessions_dir(root: Path | str | None = None, env: dict | None = None) -> Path:
    """The sessions directory for this project.

    Resolution order, highest priority first:

    1. ``root`` if given — caller-injected (tests, the hook).
    2. ``$CLAUDE_PROJECT_DIR`` — exported to every process Claude Code
       launches, so the most reliable statement of the current project.
    3. The nearest ancestor of cwd containing ``.claude/`` — writer and
       reader must agree on a root even when cwd is a subdirectory.
    4. cwd, so a path is always returned. It may not exist yet.
    """
    if root is not None:
        base = Path(root)
    else:
        env = env if env is not None else os.environ
        project_dir = env.get("CLAUDE_PROJECT_DIR")
        if project_dir:
            base = Path(project_dir)
        else:
            base = Path.cwd()
            for ancestor in (base, *base.parents):
                if (ancestor / ".claude").is_dir():
                    base = ancestor
                    break
    return base / ".claude" / "wiki-knowledge" / "sessions"


def _state_path(session_id: str, state_dir: Path | None = None) -> Path:
    return (state_dir if state_dir is not None else sessions_dir()) / f"{session_id}.json"


def write_transcript_path(session_id: str, transcript_path: str, state_dir: Path | None = None) -> None:
    path = _state_path(session_id, state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"transcript_path": transcript_path}), encoding="utf-8")


def read_transcript_path(session_id: str, state_dir: Path | None = None) -> str | None:
    path = _state_path(session_id, state_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    transcript_path = data.get("transcript_path")
    return transcript_path if isinstance(transcript_path, str) else None
