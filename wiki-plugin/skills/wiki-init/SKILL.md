---
name: wiki-init
description: Scaffold a brand-new wiki vault — folder structure, git repo, and (optionally) query-from-anywhere plugin registration. Invoke via /wiki-init [path] when standing up a vault that doesn't exist yet, as opposed to ingesting into one that already does.
---
# Wiki Init

New empty vault at target dir. One-time scaffold. Not `wiki-ingest` (that fills existing vault) — don't run on existing vault.

Folder layout, `.gitignore`, git init, `settings.json`, scaffold commit — all handled by `bin/enchiridion init` (Go binary, no Python; see `enchiridion-go/internal/initwiki/`). Only decision left: deployment mode.

## Procedure

Given target dir `<vault>` (path arg, or `cwd` if omitted):

1. **Ask user which deployment mode** per `docs/adr/0004-deployment-modes-and-vault-root-resolution.md`, unless already stated:
   - **query-from-anywhere** — common for personal/dogfooding vault: plugin stays installed user-scope elsewhere, new vault just needs registration.
   - **dedicated** — vault *is* a Claude Code project with plugin installed project-scope inside it. `enchiridion init` won't attempt that install (not its job) — only skips writing `settings.json`; tell user to install plugin into `<vault>` and launch Claude Code from `<vault>` root after.

2. **Run the binary.** `<plugin-root>` is the plugin's install directory — on Claude Code, `${CLAUDE_PLUGIN_ROOT}` (substituted before you read this); on OpenCode, the `plugin_root` value from `.opencode/wiki-knowledge/config.json`. Pass it straight through as `--plugin-root`:
   ```
   # query-from-anywhere:
   "<plugin-root>/bin/enchiridion" init "<vault>" --mode query-from-anywhere --plugin-root "<plugin-root>"

   # dedicated:
   "<plugin-root>/bin/enchiridion" init "<vault>" --mode dedicated
   ```
   Non-zero exit (e.g. `<vault>` already a vault): report stderr, stop — don't scaffold over existing vault by hand.

3. **Report** vault path (the only stdout line), deployment mode used, next step: run `wiki-ingest` (or `/save-conversation`) against it.