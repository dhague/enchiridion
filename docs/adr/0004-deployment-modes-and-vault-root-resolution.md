# Two deployment modes, resolved by a fixed vault-root order

The plugin supports two deployment modes rather than picking one: **dedicated** (installed project-scope inside the vault; launch Claude Code from the vault root) and **query-from-anywhere** (installed user-scope so its skills/agents are available in any repo, with `$WIKI_ROOT` pointing at the vault). Both are real use cases — querying the wiki from inside an unrelated code repo is a first-class scenario, not an edge case — so `vault.py`'s `resolve_vault_root()` (`wiki-plugin/scripts/vault.py`) checks, in order: `$WIKI_ROOT` (wins always) → the nearest ancestor of `cwd` containing a vault marker (`wiki/` or `.wiki-root`) → `cwd` itself. Never hard-code a path; every script resolves the root through this one function.

## Consequences

Vault-root resolution and plugin *loading* are separate problems that must both be satisfied: a project-scope skills-dir plugin loads only from the launch directory's `.claude/skills/` and does not walk up, so dedicated mode requires launching from the vault root, and query-from-anywhere requires installing the plugin user-scope — setting `$WIKI_ROOT` alone does not make a project-scope install visible from another directory. A `/reload-plugins` is needed after `cd` for project-scope installs.
