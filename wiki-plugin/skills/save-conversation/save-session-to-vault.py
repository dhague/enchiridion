"""Save the current session's conversation as a raw markdown artifact in the
enchiridion-vault inbox (raw/conversations/), for later wiki-ingest.

Run manually (via the save-conversation skill), not as a hook. There is no
hook JSON payload to read session/transcript info from, so the transcript
file is located by convention: Claude Code stores it at
~/.claude/projects/<encoded-cwd>/<session-id>.jsonl, where <encoded-cwd> is
the lowercased cwd with every non-alphanumeric character replaced by '-'.
Among the top-level *.jsonl files in that directory (subagent transcripts
live in subdirectories, not at the top level), the most recently modified
one is the current session.

Prints the vault-relative path of the raw file it wrote, so the calling
skill can pass it straight to wiki-ingest.
"""
import glob
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from vault import resolve_vault_root  # noqa: E402

WIKI_ROOT = str(resolve_vault_root())
VAULT_CONVERSATIONS_DIR = os.path.join(WIKI_ROOT, "raw", "conversations")


def find_transcript_path():
    encoded_cwd = re.sub(r"[^a-zA-Z0-9]", "-", os.getcwd().lower())
    project_dir = os.path.expanduser(f"~/.claude/projects/{encoded_cwd}")
    candidates = glob.glob(os.path.join(project_dir, "*.jsonl"))
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def extract_text(content):
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


def main():
    transcript_path = find_transcript_path()
    if not transcript_path or not os.path.isfile(transcript_path):
        print("No transcript file found for the current session.", file=sys.stderr)
        sys.exit(1)

    session_id = os.path.splitext(os.path.basename(transcript_path))[0]

    turns = []
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") not in ("user", "assistant"):
                continue
            # Skip synthetic entries (skill/tool-injected content, sub-agent
            # sidechains) - only keep the real back-and-forth with Darren.
            if entry.get("isMeta") or entry.get("isSidechain"):
                continue
            message = entry.get("message") or {}
            role = message.get("role")
            text = extract_text(message.get("content"))
            if text:
                turns.append((role, text))

    if len(turns) < 2:
        print("Not enough conversation to save.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(VAULT_CONVERSATIONS_DIR, exist_ok=True)

    now = datetime.now()
    short_id = session_id.split("-")[0]
    filename = f"{now:%Y-%m-%d-%H%M}-{short_id}-session.md"
    out_path = os.path.join(VAULT_CONVERSATIONS_DIR, filename)

    # One file per session: remove any earlier capture of this same session
    # so the timestamp in the filename stays current rather than accumulating
    # a new file per save.
    for existing in glob.glob(
        os.path.join(VAULT_CONVERSATIONS_DIR, f"*-{short_id}-session.md")
    ):
        os.remove(existing)

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
        label = "Darren" if role == "user" else "Claude"
        lines.append(f"## {label}")
        lines.append("")
        lines.append(text)
        lines.append("")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    raw_relative_path = os.path.relpath(out_path, WIKI_ROOT).replace("\\", "/")
    print(raw_relative_path)


if __name__ == "__main__":
    main()
