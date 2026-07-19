---
name: wiki-ingest
description: Turn a raw document into one or more schema-valid wiki pages — chunked, placed, tagged, linked, and committed per the wiki-conventions contract. Invoke via /wiki-ingest <path>, or whenever a document needs filing into the wiki vault.
---

# Wiki Ingest

Reads `wiki-conventions` for anything this procedure doesn't spell out — folder placement, frontmatter schema, link format, typed-edge vocabulary. This file is preloaded into the `wiki-ingest` agent's context at startup, and is also what `/wiki-ingest <path>` loads when invoked directly.

## Invocation

- **If you are not already running as the `wiki-ingest` agent** (your own system prompt doesn't identify you as it — e.g. you were invoked directly via `/wiki-ingest <path>` in an ordinary session), your only action is to delegate: call `Task` with `subagent_type: "wiki-ingest"` and a prompt containing the document path, then relay the manifest it returns back to the user verbatim. This keeps the judgment-heavy steps below running on the `wiki-ingest` agent's Sonnet model regardless of what model the invoking session happens to be running.
- **If you are the `wiki-ingest` agent**, continue directly with the procedure below using your own tools.

## Vault root

Every script in `scripts/` resolves the vault root itself (`$WIKI_ROOT`, else the nearest ancestor `wiki/` directory, else cwd — see `vault.py`). Make sure your shell's working directory is already inside the target vault (or export `WIKI_ROOT`) before invoking any of them.

## Procedure

Given one document at `<path>`:

1. **Read** the document in full.
2. **Semantic-chunk.** Decide whether it holds one page-worthy idea or several. Default to one page; split only when the document genuinely covers multiple independent ideas that would each deserve their own future citation.
3. **Check for overlap.** Read `wiki/_index.md` and grep the vault for existing pages covering the same subject. If nothing overlaps (the common case for a fresh vault), proceed to create new pages below.
   - This procedure does not yet judge *what kind* of overlap it found — updating an existing page in place, or a genuine contradiction that should append a new page and record `supersedes` per `wiki-conventions` (never overwrite either way) — that comparison-and-classification judgment is a later extension of this same procedure. Until then, treat any overlap conservatively: still create the new page rather than editing the existing one, and call out the overlap in the manifest so a human can confirm the right relationship.
4. **Place each page** using the fixed placement algorithm from `wiki-conventions`, first match wins: `source/` (stand-in for the raw artifact) → `synthesis/` (saved query result) → `entity/` (named, repeatedly-linked thing) → `concept/` (default). Filename is the lowercase kebab-slug of the title, no date prefix.
   - Not every ingested artifact needs a `source/` stand-in. Create one when the raw artifact itself is the citable reference — a runbook, a spec, a document worth linking to directly. When the artifact's value is really the knowledge inside it (a meeting note, an email), distill straight into `concept/`/`entity/` pages and skip `source/` — the raw file still lives on, normalized, under `raw/`, just without its own wiki page.
   - **Typed edges** (`refines`/`contradicts`/`example-of`/`source`/`related`) connect a page to *other* pages it relates to. This pass doesn't yet assign them — judging the most-specific-true relationship against existing content is a later extension of this same procedure, once the vault has related pages to link. Leave edge keys unset for now rather than guessing.
5. **Normalize the raw artifact.** Run `python scripts/normalize_raw.py <raw/relative/path>` (add `--when <ISO datetime>` if you've judged the artifact's true date from its content — better than the file's mtime, which the script otherwise falls back to). Note the returned, possibly-renamed `raw/` path.
6. **Write frontmatter** for each new page via `python scripts/frontmatter.py set <page> <key> <value>` (never hand-edit the YAML block):
   - `title` — human-readable name.
   - `summary` — one line, ≤~20 words; write it well, retrieval reads this first.
   - `tags` — reuse an existing tag where one fits (check the index/existing pages first), mint a new one only when nothing does.
   - `source_date` — the document's own date (valid time), not today's date.
   - `volatility` — `stable` | `evolving` | `volatile`, your judgment of how likely this fact is to age.
   - `raw_source` — **only on a `source/` page**: a single quoted link `"[<filename>](<relative/path/into/raw/>)"` to the normalized artifact from step 5.
7. **Write each page's body.**
8. **Regenerate the index.** Run `python scripts/build_index.py`.
9. **Commit.** Build a manifest (`title`, `action: "ingest"`, `created`/`updated` vault-relative paths, `source_date`, `extra_paths: ["wiki/_index.md"]`) as a JSON file, then run `python scripts/commit.py --manifest <manifest.json>`.
10. **Report.** Reply with only a short manifest — pages created/updated, edges added, any overlap flagged in step 3 — never page-content dumps.
