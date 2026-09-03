---
name: save-conversation
description: Save the current conversation to $WIKI_ROOT as a raw markdown artifact, then ingest it into the wiki. Invoke via /save-conversation when the user wants this session's conversation captured.
---
# Save Conversation

Captures session transcript into `$WIKI_ROOT/raw/conversations/`, files it into wiki.

## Procedure

**On OpenCode:** replace every `Bash` + `"<plugin-root>/bin/enchiridion" <subcommand> <args...>` call in this procedure with `wiki(args=["<subcommand>", ...])` — same subcommand, same flags, no path to resolve. See `## Scripts` in `wiki-conventions` for detail.

`save-session` detects the host from which session-id var is set — `$CLAUDE_CODE_SESSION_ID` or `$OPENCODE_SESSION_ID` — and fetches transcript accordingly.

1. Run capture script with `WIKI_ROOT` set to target vault (per deployment-mode resolution — script runs outside vault, can't use marker-directory discovery from cwd). Script lives in plugin's install directory; invoke via `<plugin-root>`:
   ```
   WIKI_ROOT="<path to vault>" "<plugin-root>/bin/enchiridion" save-session --slug "<short phrase>"
   ```
   `--slug`: short phrase naming what session **covered**, judged from whole conversation — not how it opened. Session that started "look at issue 33" and became filename design argument is `wayfinder-33-raw-filename-slugs`, not `look-at-issue-33`. Few words right; script caps it.
   - Script **sanitizes rather than trusts** phrase (lowercased, `[^a-z0-9]+` → `-`, capped) — printed path differs from what you passed. Read path from stdout; never reconstruct from phrase.
   - Name **bound at first save**. Re-save reuses existing file, rewrites in place, ignores new `--slug` — raw files never renamed. Session changed topic: start new session, don't rename.

   Looks up session transcript per host: Claude Code by `$CLAUDE_CODE_SESSION_ID`, using path `SessionStart` hook (`enchiridion hook session-start`) recorded at session start; OpenCode by `$OPENCODE_SESSION_ID` (injected by session-tracker plugin), fetched via `opencode export`. Writes markdown transcript to vault's `raw/conversations/` inbox, prints vault-relative path (e.g. `raw/conversations/2026-07-28-1430-charting-wayfinder-33-1dc3e094.md`).
   - Non-zero exit (nothing to save, no transcript recorded, not enough conversation): report and stop.
2. Ingest file just written: delegate to `wiki-ingest` agent (`Task` with `subagent_type: "wiki-ingest"`) with prompt `Ingest <path> into the vault.`, using exact path from step 1.
3. Relay ingest manifest (pages created/updated) to user.