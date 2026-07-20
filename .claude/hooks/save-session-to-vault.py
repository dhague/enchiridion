"""SessionEnd hook: save this session's conversation as a raw markdown artifact
in the enchiridion-vault inbox (raw/conversations/), for later wiki-ingest.

Reads the SessionEnd hook JSON payload on stdin. Never raises or blocks —
any failure is swallowed so a broken hook can't interrupt session exit.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

VAULT_CONVERSATIONS_DIR = (
    r"C:/Users/darre/Resilio Sync/Documents/enchiridion-vault/raw/conversations"
)
VAULT_ROOT = os.path.dirname(os.path.dirname(VAULT_CONVERSATIONS_DIR))

# Exactly the tools the wiki-ingest agent's own frontmatter declares
# (wiki-plugin/agents/wiki-ingest.md) - pre-authorized because a
# background/headless session can't pause mid-run to ask permission.
INGEST_ALLOWED_TOOLS = ["Read", "Write", "Edit", "Bash", "Grep", "Glob"]


def launch_ingest(raw_relative_path):
    """Fire-and-forget a background `claude` agent to ingest the raw file
    just written. Ingestion is judgment work that takes several minutes, far
    longer than a hook is allowed to block session exit for, so this spawns
    a fully detached OS process (`claude --bg` itself also returns as soon
    as the background service has started, well within the hook's timeout)
    and never waits on it.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return

    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )

    subprocess.Popen(
        [
            claude_bin,
            "--bg",
            "--agent",
            "wiki-knowledge:wiki-ingest",
            "--allowedTools",
            *INGEST_ALLOWED_TOOLS,
            "--",
            f"Ingest {raw_relative_path} into the vault.",
        ],
        cwd=VAULT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=True,
    )


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
    payload = json.load(sys.stdin)

    transcript_path = payload.get("transcript_path") or payload.get("transcriptPath")
    session_id = payload.get("session_id") or payload.get("sessionId") or "unknown-session"

    if not transcript_path or not os.path.isfile(transcript_path):
        return

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

    # Need at least one real exchange to be worth saving.
    if len(turns) < 2:
        return

    os.makedirs(VAULT_CONVERSATIONS_DIR, exist_ok=True)

    now = datetime.now()
    short_id = session_id.split("-")[0]
    filename = f"{now:%Y-%m-%d-%H%M}-{short_id}-session.md"
    out_path = os.path.join(VAULT_CONVERSATIONS_DIR, filename)

    # One file per session: remove any earlier capture of this same session
    # (from a prior SessionEnd during the same session, e.g. after /clear)
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
        "**Source:** Claude Code session transcript (SessionEnd hook, enchiridion repo)",
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

    raw_relative_path = os.path.relpath(out_path, VAULT_ROOT).replace("\\", "/")
    launch_ingest(raw_relative_path)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
