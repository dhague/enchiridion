---
name: wiki-ingest
description: Turn a raw document into one or more schema-valid wiki pages — chunked, placed, tagged, linked, and committed per the wiki-conventions contract. Invoke via /wiki-ingest <path>, or whenever a document needs filing into the wiki vault.
---

# Wiki Ingest

Reads `wiki-conventions` for anything this procedure doesn't spell out — folder placement, frontmatter schema, link format, typed-edge vocabulary. This file is preloaded into the `wiki-ingest` agent's context at startup, and is also what `/wiki-ingest <path>` loads when invoked directly.

## Invocation

- **If you are not already running as the `wiki-ingest` agent** (your own system prompt doesn't identify you as it — e.g. you were invoked directly via `/wiki-ingest <path>` in an ordinary session), your only action is to delegate: call `Task` with `subagent_type: "wiki-ingest"` and a prompt containing the document path, then relay the manifest it returns back to the user verbatim. This keeps the judgment-heavy steps below running on the `wiki-ingest` agent's Sonnet model regardless of what model the invoking session happens to be running.
- **If you are the `wiki-ingest` agent**, continue directly with the procedure below using your own tools.

## Vault root and script location

Every script in `scripts/` resolves the vault root itself (`$WIKI_ROOT`, else the nearest ancestor `wiki/` directory, else cwd — see `vault.py`). Make sure your shell's working directory is already inside the target vault (or export `WIKI_ROOT`) before invoking any of them.

The scripts themselves live in *this plugin's own* install directory, not the vault — invoke them via `${CLAUDE_PLUGIN_ROOT}/scripts/<name>.py` (the placeholder is substituted before you ever see this text, so the commands below are already the resolved absolute path). This works identically whether cwd is inside the plugin's own repo (dedicated mode) or a separate vault repo (query-from-anywhere mode).

## Procedure

Given one document at `<path>`:

1. **Read** the document in full.
2. **Semantic-chunk.** Decide whether it holds one page-worthy idea or several. Default to one page; split only when the document genuinely covers multiple independent ideas that would each deserve their own future citation.
3. **Check for overlap.** Read `wiki/_index.md` and grep the vault for existing pages covering the same subject as any candidate chunk from step 2. For every existing page that surfaces, classify the relationship before deciding what to write:
   - **No overlap.** The candidate is a new subject. Proceed to create a new page below; still consider the surfaced page(s) as typed-edge targets in step 4.
   - **Substantive overlap, no conflict.** The candidate adds to, refines, or restates knowledge the existing page already carries, without contradicting it. **Update that page in place** rather than creating a duplicate: `Edit` its body and, via `wikipage.py set`, whichever of `summary`/`tags`/`source_date`/`volatility` the new material actually changes. Record it as `updated`, not `created`, in the manifest and commit.
     - `wikipage.py set` **overwrites** the key's whole value — it does not append. Use `wikipage.py merge <page> <key> <json-list>` instead of `set` for `tags` or any edge-list key (`refines`/`contradicts`/`example-of`/`source`/`related`/`supersedes`) on a page that already has one — it unions the existing entries with the new ones internally, so nothing has to get-then-set by hand.
   - **Contradiction.** The candidate's claim conflicts with an existing page's claim. **Never overwrite the existing page.** Create a new page as usual (step 4 onward), and on the *new* page set `contradicts` (and, since this same ingestion pass resolves the conflict by replacement) `supersedes` — both pointing at the superseded page. The superseded page's content is left untouched; only the new page carries these edges.
   - When a candidate touches more than one existing page, judge each pairing independently — a document can update one page while contradicting another.
4. **Place each new page.** Judge the kind using the fixed placement algorithm from `wiki-conventions`, first match wins: `source/` (stand-in for the raw artifact) → `synthesis/` (saved query result) → `entity/` (named, repeatedly-linked thing) → `concept/` (default). Once the kind is decided, run `python "${CLAUDE_PLUGIN_ROOT}/scripts/place.py" <kind> "<title>"` to get the exact target path — it computes the kebab-slug and folder deterministically, so don't hand-slugify the title. (A page updated in place in step 3 keeps its existing location — this step only applies to genuinely new pages; the next bullet applies to new *and* updated pages alike.)
   - Not every ingested artifact needs a `source/` stand-in. Create one when the raw artifact itself is the citable reference — a runbook, a spec, a document worth linking to directly. When the artifact's value is really the knowledge inside it (a meeting note, an email), distill straight into `concept/`/`entity/` pages and skip `source/` — the raw file still lives on, normalized, under `raw/`, just without its own wiki page.
   - **Typed edges** (`refines`/`contradicts`/`example-of`/`source`/`related`) connect a page to *other* pages it relates to — judge these for **every new or updated page**, against every existing page surfaced in step 3 (overlapping or merely related). Per `wiki-conventions`, assign the most specific type that's true (`related` only as a fallback); `contradicts`/`supersedes` are already decided by step 3 where applicable.
5. **Normalize the raw artifact.** Run `python "${CLAUDE_PLUGIN_ROOT}/scripts/normalize_raw.py" <raw/relative/path>` (add `--when <ISO datetime>` if you've judged the artifact's true date from its content — better than the file's mtime, which the script otherwise falls back to). Note the returned, possibly-renamed `raw/` path.
6. **Write frontmatter** for each new or updated page via `python "${CLAUDE_PLUGIN_ROOT}/scripts/wikipage.py" set <page> <key> <value>` (never hand-edit the YAML block; on an *updated* page, use `wikipage.py merge` instead of `set` for `tags` and edge-list keys per step 3):
   - `title` — human-readable name.
   - `summary` — one line, ≤~20 words; write it well, retrieval reads this first.
   - `tags` — reuse an existing tag where one fits (check the index/existing pages first), mint a new one only when nothing does.
   - `source_date` — the document's own date (valid time), not today's date.
   - `volatility` — `stable` | `evolving` | `volatile`, your judgment of how likely this fact is to age.
   - `raw_source` — **only on a `source/` page**: a single quoted link `"[<filename>](<relative/path/into/raw/>)"` to the normalized artifact from step 5.
   - `supersedes` / typed-edge keys (`refines`/`contradicts`/`example-of`/`source`/`related`) — from step 4's judgment, each a list of quoted markdown links (`["[<title>](<relative/path.md>)"]`) via `wikipage.py set <page> <key> <value> --json` on a new page, or `wikipage.py merge <page> <key> <value>` on an updated page that may already carry entries for that key. Set these on the new/updated page only — never touch a superseded page's own frontmatter.
7. **Write each page's body.** For a page updated in step 3, edit only the parts the new material actually changes — leave the rest of the existing body as-is.
8. **Regenerate the index.** Run `python "${CLAUDE_PLUGIN_ROOT}/scripts/build_index.py"`.
9. **Commit.** Build a manifest (`title`, `action: "ingest"`, `created`/`updated` vault-relative paths, `superseded` as `[old, new]` pairs for every contradiction resolved this pass, `source_date`, `extra_paths: ["wiki/_index.md"]`) as a JSON file. If step 5's normalized artifact path is under `raw/`, also set the manifest's `raw_source` field to it — `commit.py` stages it automatically alongside the pages, so the source document always lands in the same commit as the pages it produced. Then run `python "${CLAUDE_PLUGIN_ROOT}/scripts/commit.py" --manifest <manifest.json>`.
10. **Report.** Reply with only a short manifest — pages created vs. updated, edges added, any `supersedes` pairs recorded — never page-content dumps.
