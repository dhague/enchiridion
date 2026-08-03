# enchiridion

Personal knowledge management powered by LLM agents. Turns raw documents into a structured, searchable, git-backed markdown wiki vault — then answers questions over it with typed-edge graph traversal and cited synthesis.

Follows the [Karpathy LLM-wiki pattern](https://github.com/karpathy/llm-wiki).

## What's inside

- **wiki-knowledge** — a Claude Code plugin that provides ingestion and retrieval over a markdown wiki vault
- **Agent pipeline** — Claude Sonnet for semantic ingestion (chunking, overlap classification, edge typing); Claude Haiku for retrieval (query expansion, BM25 search, frontier traversal, synthesis)
- **Deterministic script layer** — pure Python scripts for vault I/O, placement, FTS5 search indexing, and commit construction (no model calls)
- **Full-text search** — SQLite FTS5 via stdlib, zero extra search dependencies

## Install

1. Clone the repo and set up dependencies:
   ```bash
   git clone https://github.com/dhague/enchiridion.git
   cd enchiridion/wiki-plugin
   python3 -m venv .venv && source .venv/bin/activate
   pip install .
   ```

2. Register the plugin in Claude Code from `wiki-plugin/.claude-plugin/marketplace.json`.

3. Create a vault — either:
   - **Local**: `/wiki-init .` inside a project to keep the vault alongside your codebase.
   - **Remote**: `/wiki-init /some/remote/path` then set `WIKI_ROOT` to query it from anywhere. Useful when a wiki spans multiple projects or lives on a shared drive.

## Design principles

**Cost-optimised by design.** Ingestion and retrieval run as subagents with model selection tuned to task. Sonnet handles the expensive judgment work (semantic chunking, edge typing); Haiku handles high-volume retrieval at a fraction of the cost. Each query only explores the frontier it needs — no expensive vector re-ranking, no full-graph traversal.

**Predictability through scripts, not prompts.** Everything that can be deterministic *is*. Page placement, frontmatter parsing, link rewriting, search indexing, and commit construction run as pure Python scripts — no model in the loop. The agents call scripts for side effects and read their output; they never generate file paths, YAML, or git operations from a prompt.

**No new infrastructure.** SQLite FTS5 search runs in-process with zero extra dependencies. No vector database, no MCP server, no background daemons. The vault is just a git repo of markdown files — portable, diffable, and backup-friendly.

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
  concept/     # Abstract ideas, frameworks, definitions
  entity/      # Concrete people, tools, projects, organizations
  source/      # Provenance stubs (one per raw artifact)
  synthesis/   # Cross-cutting analysis and summaries
```

Every page has YAML frontmatter with a typed edge graph (`refines`, `contradicts`, `example-of`, `source`, `related`, `supersedes`) and bitemporal metadata (`source_date`, `volatility`).

## Development

```bash
cd wiki-plugin
source .venv/bin/activate

# Run tests
python -m pytest

# Type-check
pyright

# Run scripts standalone (set WIKI_ROOT or cd into a vault)
WIKI_ROOT=<path_to_vault> python scripts/search.py "connection pooling" --limit 10
```

## Architecture

Key decisions are documented in [docs/adr/](docs/adr/):
- No MCP server — everything runs as skills + agents + Bash-invoked scripts
- No embeddings — lexical FTS5 search + agent comprehension
- Bitemporal data model (valid time + transaction time)
- Chain of evidence from every derived page back to its raw source

See [CONTEXT.md](CONTEXT.md) for the domain glossary.
