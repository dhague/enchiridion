# enchiridion

Personal knowledge management powered by LLM agents. Turns raw documents into a structured, searchable, git-backed markdown wiki vault — then answers questions over it with typed-edge graph traversal and cited synthesis.

Follows the [Karpathy LLM-wiki pattern](https://github.com/karpathy/llm-wiki).

## What's inside

- **wiki-knowledge** — a Claude Code plugin that provides ingestion and retrieval over a markdown wiki vault
- **Agent pipeline** — Claude Sonnet for semantic ingestion (chunking, overlap classification, edge typing); Claude Haiku for retrieval (query expansion, BM25 search, frontier traversal, synthesis)
- **Deterministic script layer** — a single static Go binary for vault I/O, placement, FTS5 search indexing, and commit construction (no model calls, no runtime to install)
- **Full-text search** — SQLite FTS5 via stdlib, zero extra search dependencies

## Install

1. Clone the repo — there is nothing to install, no runtime and no dependencies.
   The plugin lazy-fetches its binary on first use.
   ```bash
   git clone https://github.com/dhague/enchiridion.git
   ```

2. Register the plugin in Claude Code from `wiki-plugin/.claude-plugin/marketplace.json`.

3. Create a vault — either:
   - **Local**: `/wiki-init .` inside a project to keep the vault alongside your codebase.
   - **Remote**: `/wiki-init /some/remote/path` then set `WIKI_ROOT` to query it from anywhere. Useful when a wiki spans multiple projects or lives on a shared drive.

## Design principles

**Cost-optimised by design.** Ingestion and retrieval run as subagents with model selection tuned to task. Sonnet handles the expensive judgment work (semantic chunking, edge typing); Haiku handles high-volume retrieval at a fraction of the cost. Each query only explores the frontier it needs — no expensive vector re-ranking, no full-graph traversal.

**Predictability through scripts, not prompts.** Everything that can be deterministic *is*. Page placement, frontmatter parsing, link rewriting, search indexing, and commit construction run as subcommands of a single static binary — no model in the loop. The agents call it for side effects and read its output; they never generate file paths, YAML, or git operations from a prompt.

**No new infrastructure.** SQLite FTS5 search runs in-process with zero extra dependencies. No language runtime to install, no vector database, no MCP server, no background daemons. The vault is just a git repo of markdown files — portable, diffable, and backup-friendly.

**Trust and provenance.** Every derived page traces back to its raw source through a chain of evidence. Bitemporal metadata (when the knowledge is *from* vs. when it was *written*) and explicit volatility annotations make staleness visible, not hidden.

**Agent-native, not API-native.** Ingestion and retrieval are skills that Claude Code agents execute by reading instructions and running scripts. This means the full context window, tool use, and reasoning of frontier models are available — not limited by a fixed RAG pipeline or a hardcoded prompt template.

## Commands

| Command | Purpose |
|---|---|
| `/wiki-init [path]` | Scaffold a new vault (folders, git repo, index) |
| `/wiki-ingest <path>` | Ingest one file, a folder, or sweep `raw/` |
| `/wiki-watch` | Long-running auto-ingest watcher for `raw/` |
| `/wiki-retrieval <question>` | Grounded, cited answer from the vault |
| `/save-conversation` | Capture and ingest the current session |

## Vault structure

```
raw/           # Inbox — drop documents here for ingestion
wiki/
  concepts/    # Abstract ideas, frameworks, definitions
  entities/    # Concrete people, tools, projects, organizations
  sources/     # Provenance stubs (one per raw artifact)
  synthesis/   # Cross-cutting analysis and summaries
```

Every page has YAML frontmatter with a typed edge graph (`refines`, `contradicts`, `example-of`, `source`, `related`, `supersedes`) and bitemporal metadata (`source_date`, `volatility`).

## Development

The script layer is a single static Go binary that needs no Python
([ADR-0011](docs/adr/0011-go-rewrite-scope-sequencing-toolchain.md)). The
plugin lazy-fetches it on first use via `wiki-plugin/bin/enchiridion`;
`ENCHIRIDION_BIN` points that entrypoint at a local build instead.

```bash
cd enchiridion-go
go test ./...
go vet ./...
gofmt -l .

# Run any subcommand against a locally built binary
go build -o /tmp/enchiridion ./cmd/enchiridion
WIKI_ROOT=<path_to_vault> /tmp/enchiridion search "connection pooling" --limit 10
WIKI_ROOT=<path_to_vault> /tmp/enchiridion ingest-scan --json
```

`wiki-plugin/scripts/` holds only OpenCode install-time tooling now
(`generate-opencode.py`, `install-opencode.py`) — see
[README-opencode.md](README-opencode.md). It has its own small test suite:

```bash
cd wiki-plugin
python3 -m venv .venv && source .venv/bin/activate
pip install ruamel.yaml pytest
python -m pytest
```

## Architecture

Key decisions are documented in [docs/adr/](docs/adr/):
- No MCP server — everything runs as skills + agents + Bash-invoked scripts
- No embeddings — lexical FTS5 search + agent comprehension
- Bitemporal data model (valid time + transaction time)
- Chain of evidence from every derived page back to its raw source

See [CONTEXT.md](CONTEXT.md) for the domain glossary.
