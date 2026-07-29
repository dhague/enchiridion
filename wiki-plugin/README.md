# wiki-knowledge

A Claude Code plugin for clean-room ingestion and retrieval over a git-backed markdown wiki vault, following the Karpathy LLM-wiki pattern. See `../CONTEXT.md` for the domain glossary and `../docs/adr/` for the architectural decisions behind it.

## Skills

- **`/wiki-init [path]`** — scaffold a brand-new vault (folder structure, empty index, git repo, optional query-from-anywhere registration).
- **`/wiki-ingest <path>`** — turn a raw document into one or more schema-valid wiki pages, chunked, placed, tagged, linked, and committed. `/wiki-ingest` (no path) or `/wiki-ingest <folder>` sweeps `raw/` (or a subfolder) instead of a single file.
- **`/wiki-watch`** — a long-running foreground watcher: auto-ingests new or changed files under `raw/` as they appear, without a manual sweep. User-started and user-stopped (Ctrl-C); not a daemon.
- **`/wiki-retrieval <question>`** — turn a question into a grounded, cited answer over the vault.
- **`/save-conversation`** — save the current session to the vault as a raw artifact, then ingest it.

`wiki-conventions` is not itself invoked directly — it's the shared schema/folder/link contract the ingestion and retrieval skills both read.
