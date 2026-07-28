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
3. **Check for overlap.** Find existing pages covering the same subject as any candidate chunk from step 2 by querying the lexical index: `python "${CLAUDE_PLUGIN_ROOT}/scripts/search.py" "<term1> <term2> ..." --limit 20 --json`. The BM25-ranked title+summary+body match is a much better overlap detector than substring grep — and this is the step where a miss creates a *duplicate page*, so the ranking earns its keep here. (`raw=False`, the default, is correct: hyphenated tags would otherwise crash the FTS5 `MATCH`.) For every existing page that surfaces, classify the relationship before deciding what to write:
   - **No overlap.** The candidate is a new subject. Proceed to create a new page below; still consider the surfaced page(s) as typed-edge targets in step 4.
   - **Substantive overlap, no conflict.** The candidate adds to, refines, or restates knowledge the existing page already carries, without contradicting it. **Update that page in place** rather than creating a duplicate: `Edit` its body and, via `wikipage.py set`, whichever of `summary`/`tags`/`source_date`/`volatility` the new material actually changes. Record it as `updated`, not `created`, in the manifest and commit.
     - `wikipage.py set` **overwrites** the key's whole value — it does not append. Use `wikipage.py merge <page> <key> <json-list>` instead of `set` for `tags` or any edge-list key (`refines`/`contradicts`/`example-of`/`source`/`related`/`supersedes`) on a page that already has one — it unions the existing entries with the new ones internally, so nothing has to get-then-set by hand.
   - **Contradiction.** The candidate's claim conflicts with an existing page's claim. **Never overwrite the existing page.** Create a new page as usual (step 4 onward), and on the *new* page set `contradicts` (and, since this same ingestion pass resolves the conflict by replacement) `supersedes` — both pointing at the superseded page. The superseded page's content is left untouched; only the new page carries these edges.
   - When a candidate touches more than one existing page, judge each pairing independently — a document can update one page while contradicting another.
4. **Assemble the `IngestPlan`.** Everything downstream of the step 1-3 judgment — placement mechanics, frontmatter writes, body writes, the index, and the commit — is one call: `python "${CLAUDE_PLUGIN_ROOT}/scripts/ingest.py" --plan <plan.json>`. Write `<plan.json>` yourself (a scratch file, not a vault page) with this shape:

   ```jsonc
   {
     "title": "<source document's title>",
     "source_date": "<the document's own date, not today's>",
     "raw": "raw/<artifact's path, exactly as it sits on disk>",   // omit if nothing came from raw/
     "pages": [
       {
         "op": "create",
         "kind": "concept",            // source | synthesis | entity | concept — your step-4-equivalent judgment, first match wins per wiki-conventions
         "title": "<page title>",
         "body": "<full markdown body>",
         "frontmatter": {
           "summary": "<one line, ≤~20 words>",
           "tags": ["<reuse an existing tag where one fits, mint only when nothing does>"],
           "source_date": "<same as above, or this page's own if it differs>",
           "volatility": "stable | evolving | volatile",
           "raw_source": "[<filename>](<relative/path/into/raw/>)"   // only on a source/ page — see wiki-conventions; the path is relative to THIS page once placed, and points at the artifact's real, unchanged filename
         },
         "edges": {
           "related": ["[<title>](<relative/path.md>)"],
           "supersedes": ["[<title>](<relative/path.md>)"]           // include on the new page when step 3 found a contradiction to resolve
         }
       },
       {
         "op": "update",
         "rel": "wiki/concept/existing-page.md",   // the page step 3 classified as substantive-overlap
         "title": "<unchanged or corrected title>",
         "frontmatter": { "volatility": "evolving", "tags": ["new-tag"] },   // scalar keys overwrite; a list-valued key like tags is unioned with what the page already has, never overwritten
         "edges": { "related": ["[<title>](<relative/path.md>)"] }          // edge-list keys union the same way
         // omit "body" entirely when nothing in the body changes
       }
     ]
   }
   ```

   A few judgment calls stay yours when filling this in, mirroring the old steps 4-7:
   - **Kind** (create pages only): `source/` (stand-in for the raw artifact) → `synthesis/` (saved query result) → `entity/` (named, repeatedly-linked thing) → `concept/` (default), first match wins. Not every ingested artifact needs a `source/` stand-in — create one only when the raw artifact itself is the citable reference; when the artifact's value is really the knowledge inside it, distill straight into `concept/`/`entity/` and skip `source/`. `ingest.py` computes the exact kebab-slug path from `kind`+`title` — never hand-slugify.
   - **Typed edges** (`refines`/`contradicts`/`example-of`/`source`/`related`) — judge these for **every new or updated page**, against every existing page surfaced in step 3. Assign the most specific type that's true (`related` only as a fallback); `contradicts`/`supersedes` are already decided by step 3 where applicable, and belong on the *new* page only — never on the superseded page.
   - **Body**, for an `update` page: write the *complete* new body text (not a diff) when the material actually changes something in it; omit the `body` key entirely to leave the existing body untouched. For a `create` page, `body` is always required.
   - **`raw_source`**'s destination points at the raw artifact exactly where it sits (matching the plan's own `raw` field). **Ingestion never renames a raw file** — a file that came from outside the plugin keeps its name verbatim, forever, so don't add a `YYYY-MM-DD-hhmm-` prefix or expect one to appear (that prefix is bound at creation, and only for files the plugin itself creates). Percent-encode the destination — but not the label — where the filename needs it (`%` `space` `(` `)` `#` `<` `>`; leave unicode literal): `"[My Notes (draft).md](../../raw/My%20Notes%20%28draft%29.md)"`.
5. **Run it.** `python "${CLAUDE_PLUGIN_ROOT}/scripts/ingest.py" --plan <plan.json>` validates the whole plan up front (every required field, every `update`'s `rel` exists, every `create`'s target doesn't yet, every edge/`raw_source` link resolves to a real page — including a sibling page this same plan is about to create) before writing anything, then executes place → frontmatter → body → index → commit in one pass and prints the commit SHA. If it raises, nothing was committed but any pages it did get to are left on disk uncommitted (writes are idempotent, so fix the plan and rerun rather than hand-repairing).
6. **Report.** Reply with only a short manifest — pages created vs. updated, edges added, any `supersedes` pairs recorded — never page-content dumps.
