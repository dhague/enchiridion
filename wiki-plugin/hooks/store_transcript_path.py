"""SessionStart hook: records this session's transcript_path so
/save-conversation can retrieve it later by session_id, instead of guessing
"most recently modified transcript" - which breaks when multiple sessions
run in parallel (#23).

Reads the SessionStart hook JSON payload on stdin. Never raises or blocks -
any failure is swallowed so a broken hook can't interrupt session start.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from session_state import write_transcript_path  # noqa: E402


def main():
    payload = json.load(sys.stdin)
    session_id = payload.get("session_id")
    transcript_path = payload.get("transcript_path")
    if not session_id or not transcript_path:
        return
    write_transcript_path(session_id, transcript_path)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
