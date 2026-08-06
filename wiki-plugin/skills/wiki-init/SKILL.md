---
name: wiki-init
description: Scaffold a brand-new wiki vault — folder structure, git repo, and (optionally) query-from-anywhere plugin registration. Invoke via /wiki-init [path] when standing up a vault that doesn't exist yet, as opposed to ingesting into one that already does.
---

# Wiki Init

Creates a new, empty vault at the target directory. This is a one-time scaffolding step, distinct from `wiki-ingest` (which fills an existing vault) — do not run it against a directory that is already a vault.

All the mechanics — folder layout, `.gitignore`, git init, `settings.json`, the scaffold commit — are handled by `scripts/init_wiki.py` (see its docstring / `tests/test_init_wiki.py` for exact behavior). The only judgment call left to this procedure is picking a deployment mode.

## Procedure

Given a target directory `<vault>` (the path argument, or `cwd` if omitted):

1. **Ask the user which deployment mode** this vault is for, per `docs/adr/0004-deployment-modes-and-vault-root-resolution.md`, unless they already said in their request:
   - **query-from-anywhere** — the common case for a personal/dogfooding vault: this plugin stays installed user-scope elsewhere, and the new vault just needs to be registered against it.
   - **dedicated** — the vault *is* a Claude Code project with this plugin installed project-scope inside it. `init_wiki.py` will not attempt that install itself (it isn't this script's job to install a plugin into a directory it doesn't own) — it only skips writing `settings.json`; tell the user to install the plugin into `<vault>` and launch Claude Code from `<vault>` root afterward.

2. **Run the script.** `${CLAUDE_PLUGIN_ROOT}` is substituted into this text before you read it, so pass it straight through as `--plugin-root`:
   ```
   # query-from-anywhere:
   python "${CLAUDE_PLUGIN_ROOT}/scripts/init_wiki.py" "<vault>" --mode query-from-anywhere --plugin-root "${CLAUDE_PLUGIN_ROOT}"

   # dedicated:
   python "${CLAUDE_PLUGIN_ROOT}/scripts/init_wiki.py" "<vault>" --mode dedicated
   ```
   If it exits non-zero (e.g. `<vault>` already looks like a vault), report the stderr message and stop — do not attempt to scaffold over an existing vault by hand.

3. **Report** the vault path (printed on the script's first stdout line), the deployment mode used, and the next step: run `wiki-ingest` (or `/save-conversation`) against it.
