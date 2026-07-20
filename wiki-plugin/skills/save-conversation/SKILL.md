---
name: save-conversation
description: Save the current conversation to $WIKI_ROOT as a raw markdown artifact, then ingest it into the wiki. Invoke via /save-conversation when the user wants this session's conversation captured.
---

# Save Conversation

Manually captures the current session's transcript into `$WIKI_ROOT/raw/conversations/` and files it into the wiki.

## Procedure

1. Run the capture script, with `WIKI_ROOT` set to the target vault (per the deployment-mode resolution in `scripts/vault.py` — the script does not run inside the vault, so it can't rely on marker-directory discovery from cwd):
   ```
   WIKI_ROOT="<path to vault>" python wiki-plugin/skills/save-conversation/save-session-to-vault.py
   ```
   It locates the current session's transcript by convention (the most recently modified `*.jsonl` in `~/.claude/projects/<encoded-cwd>/`), writes a markdown transcript to the vault's `raw/conversations/` inbox, and prints the vault-relative path of the file it wrote (e.g. `raw/conversations/2026-07-20-1530-abcd1234-session.md`).
   - If it exits non-zero (nothing to save yet, or no transcript found), report that and stop.
2. Ingest the file it just wrote: delegate to the `wiki-ingest` agent (`Task` with `subagent_type: "wiki-ingest"`) with a prompt of `Ingest <path> into the vault.`, using the exact path printed in step 1.
3. Relay the ingest manifest (pages created/updated) back to the user.
