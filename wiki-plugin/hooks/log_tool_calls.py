"""PostToolUse hook: appends one JSON line per tool call to a per-session log.

Written to .claude/wiki-knowledge/sessions/<session_id>-tool-calls.jsonl so
tool_call_stats.py (and ingest.py, after committing) can summarise the run's
cost. Follows store_transcript_path.py exactly: reads the payload on stdin,
resolves the directory via session_state.sessions_dir(payload.get("cwd")),
and never raises — a broken hook must not interrupt a session.

Per #99's spike: the payload carries no per-assistant-message identifier and
no timestamp, so tool-call count (not exact turn count) is the recoverable
metric. `prompt_id` is logged anyway — it's the closest available grouping
key (scoped to a whole user-prompt turn, not a single assistant message) —
and `agent_id`/`agent_type` cleanly separate subagent calls from the
top-level agent's own.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from session_state import sessions_dir  # noqa: E402


def main():
    payload = json.load(sys.stdin)
    session_id = payload.get("session_id")
    if not session_id:
        return
    log_path = sessions_dir(payload.get("cwd")) / f"{session_id}-tool-calls.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "tool": payload.get("tool_name"),
            "tool_use_id": payload.get("tool_use_id"),
            "prompt_id": payload.get("prompt_id"),
            "agent_id": payload.get("agent_id"),
            "agent_type": payload.get("agent_type"),
            "duration_ms": payload.get("duration_ms"),
        }) + "\n")


def main_swallowing_errors():
    try:
        main()
    except Exception:
        pass


if __name__ == "__main__":
    main_swallowing_errors()
