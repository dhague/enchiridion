"""SessionStart hook: records this session's transcript_path so
/save-conversation can retrieve it later by session_id, rather than guessing
"most recently modified transcript" — which breaks when multiple sessions
run in parallel.

Reads the SessionStart hook JSON payload on stdin. Never raises or blocks -
any failure is swallowed so a broken hook can't interrupt session start.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from session_state import sessions_dir, write_transcript_path  # noqa: E402


def main():
    payload = json.load(sys.stdin)
    session_id = payload.get("session_id")
    transcript_path = payload.get("transcript_path")
    if not session_id or not transcript_path:
        return
    # Use the hook payload's own cwd (the project root at session start)
    # rather than this process's cwd, so state always lands in the project
    # the session actually belongs to.
    write_transcript_path(session_id, transcript_path, state_dir=sessions_dir(payload.get("cwd")))


def main_swallowing_errors():
    try:
        main()
    except Exception:
        pass


if __name__ == "__main__":
    main_swallowing_errors()
