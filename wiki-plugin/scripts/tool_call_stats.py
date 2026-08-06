"""Summarise the tool-call log written by hooks/log_tool_calls.py (#100).

Makes the cost of a run visible: total tool calls, a per-tool histogram,
and (as a proxy — see below) how many user-prompt turns those calls fell
into and the calls-per-turn ratio.

Per #99's spike, the PostToolUse payload carries no per-assistant-message
identifier and no timestamp, so exact assistant-turn count is not
recoverable from the log. ``prompt_id`` is the closest available grouping
key, but it's scoped to a whole user-prompt turn (which may itself span
several assistant turns), so "prompts" here is a coarser, honestly-labelled
proxy — not an exact turn count.

CLI::

    python tool_call_stats.py [--session-id <id>]

Defaults ``--session-id`` to ``$CLAUDE_CODE_SESSION_ID``, which Claude Code
exports to every Bash tool call it makes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Sequence

from session_state import sessions_dir


def log_path(session_id: str, state_dir: Path | None = None) -> Path:
    return (state_dir if state_dir is not None else sessions_dir()) / f"{session_id}-tool-calls.jsonl"


def read_log(session_id: str, state_dir: Path | None = None) -> list[dict]:
    """The logged events for ``session_id``, oldest first. ``[]`` if no log exists."""
    path = log_path(session_id, state_dir)
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def summarize(events: list[dict]) -> dict:
    """Aggregate ``events`` into totals, a per-tool histogram, and the prompt-count proxy."""
    total = len(events)
    by_tool = Counter(e.get("tool") or "?" for e in events)
    prompt_ids = {e.get("prompt_id") for e in events if e.get("prompt_id")}
    prompts = len(prompt_ids)
    return {
        "total": total,
        "by_tool": by_tool.most_common(),
        "prompts": prompts,
        "calls_per_prompt": (total / prompts) if prompts else None,
    }


def format_summary(stats: dict) -> str:
    lines = [f"Total tool calls: {stats['total']}"]
    for tool, n in stats["by_tool"]:
        lines.append(f"  {n:3}  {tool}")
    if stats["prompts"]:
        lines.append(
            f"Prompts (proxy for turns, not exact — see #99): {stats['prompts']}, "
            f"{stats['calls_per_prompt']:.1f} calls/prompt"
        )
    return "\n".join(lines)


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--session-id", default=os.environ.get("CLAUDE_CODE_SESSION_ID"),
        help="session to summarise (default: $CLAUDE_CODE_SESSION_ID)",
    )
    args = parser.parse_args(argv)
    if not args.session_id:
        sys.exit("No session_id — pass --session-id or set $CLAUDE_CODE_SESSION_ID")

    events = read_log(args.session_id)
    if not events:
        sys.exit(f"No log found at {log_path(args.session_id)}")

    print(format_summary(summarize(events)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
