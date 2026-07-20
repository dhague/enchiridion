"""Per-session state: maps a Claude Code session_id to its transcript_path.

The `SessionStart` hook (``hooks/store_transcript_path.py``) writes here;
``skills/save-conversation`` reads by ``$CLAUDE_CODE_SESSION_ID`` so it never
has to guess which of several concurrently running sessions' transcripts is
"current" — see #23.

State lives under the user's home directory (not the vault, not the repo)
so it resolves the same way regardless of deployment mode or cwd, and one
JSON file per session_id so parallel sessions can't clobber each other.
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_STATE_DIR = Path.home() / ".claude" / "wiki-knowledge" / "sessions"


def _state_path(session_id: str, state_dir: Path | None = None) -> Path:
    return (state_dir if state_dir is not None else DEFAULT_STATE_DIR) / f"{session_id}.json"


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
