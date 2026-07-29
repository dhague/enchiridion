---
name: save-conversation
description: Save the current conversation to $WIKI_ROOT as a raw markdown artifact, then ingest it into the wiki. Invoke via /save-conversation when the user wants this session's conversation captured.
---

# Save Conversation

Manually captures the current session's transcript into `$WIKI_ROOT/raw/conversations/` and files it into the wiki.

## Procedure

1. Run the capture script, with `WIKI_ROOT` set to the target vault (per the deployment-mode resolution in `scripts/vault.py` — the script does not run inside the vault, so it can't rely on marker-directory discovery from cwd). The script itself lives in this plugin's own install directory, not the vault, so invoke it via `${CLAUDE_PLUGIN_ROOT}` (substituted before you see this text — resolves correctly regardless of cwd or deployment mode):
   ```
   WIKI_ROOT="<path to vault>" python "${CLAUDE_PLUGIN_ROOT}/scripts/save-session-to-vault.py" --slug "<short phrase>"
   ```
   Pass `--slug` as a short phrase naming what this session actually **covered**, judged from the whole conversation you have in context — not how it opened. A session that started "look at issue 33" and became a design argument about filenames is `wayfinder-33-raw-filename-slugs`, not `look-at-issue-33`. A few words is right; the script caps it.
   - The script **sanitizes rather than trusts** the phrase (lowercased, `[^a-z0-9]+` → `-`, capped), so expect the printed path to differ from what you passed. Read the path from stdout; never reconstruct it from the phrase.
   - The name is **bound at first save**. On a re-save of the same session the script reuses the existing file and rewrites it in place, ignoring any new `--slug` — raw files are never renamed. If the session has genuinely changed topic, that's the signal to start a new session, not to rename this capture.

   It looks up the current session's transcript by `$CLAUDE_CODE_SESSION_ID`, using the path the plugin's `SessionStart` hook (`hooks/store_transcript_path.py`) recorded for that session_id when this session started. It writes a markdown transcript to the vault's `raw/conversations/` inbox and prints the vault-relative path of the file it wrote (e.g. `raw/conversations/2026-07-28-1430-charting-wayfinder-33-1dc3e094.md`).
   - If it exits non-zero (nothing to save yet, no transcript recorded for this session, or not enough conversation to save), report that and stop.
2. Ingest the file it just wrote: delegate to the `wiki-ingest` agent (`Task` with `subagent_type: "wiki-ingest"`) with a prompt of `Ingest <path> into the vault.`, using the exact path printed in step 1.
3. Relay the ingest manifest (pages created/updated) back to the user.
