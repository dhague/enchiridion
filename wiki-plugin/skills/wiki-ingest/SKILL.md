---
name: wiki-ingest
description: Turn a raw document into one or more schema-valid wiki pages — chunked, placed, tagged, linked, and committed per the wiki-conventions contract. Invoke via /wiki-ingest <path>, or whenever a document needs filing into the wiki vault.
---

# Wiki Ingest

Reads `wiki-conventions` for anything this procedure doesn't spell out — folder placement, frontmatter schema, link format, typed-edge vocabulary. Preloaded into `wiki-ingest` agent context at startup; also what `/wiki-ingest <path>` loads when invoked on single file. Single-file procedure only. Folder/`raw/` sweeps belong to invoking session, not agent — lives in [`reference/sweep.md`](reference/sweep.md), read on demand.

Scripts live in plugin's install directory, resolve vault root itself — see `## Scripts` in `wiki-conventions` for full reference (vault-root resolution, locating the plugin root, common tasks, script catalogue).

## Invocation

- `/wiki-ingest <folder>` or `/wiki-ingest` (no path) — **sweep**, not single ingestion. `Read` [`reference/sweep.md`](reference/sweep.md) and follow it instead.
- `/wiki-ingest <file>` — ingest one document. Procedure below.
- **If not already running as `wiki-ingest` agent** (system prompt doesn't identify you — e.g. invoked via `/wiki-ingest <path>` in ordinary session) and `<path>` is single file: delegate only. Call `Task` with `subagent_type: "wiki-ingest"` and prompt containing document path, relay returned manifest verbatim.
- **If you are `wiki-ingest` agent**, continue with procedure using own tools. (Single-file work only — sweep delegates one file at a time, per [`reference/sweep.md`](reference/sweep.md).)

## `INGESTION.md` folder hint

Raw folder may carry `raw/<folder>/INGESTION.md`: freeform human instructions for ingesting that folder's documents — e.g. "take `source_date` from `Date:` header, list recipients in body, prefer `correspondence` tag". Read like `SKILL.md`: prose to interpret, not schema to parse.

- **Lookup is document's own folder only.** Ingesting `raw/emails/foo.eml` looks for `raw/emails/INGESTION.md`. **No ancestor walk** — `raw/INGESTION.md` and vault-root not consulted. No precedence question.
- **Hints win on conflict.** Explicit override, not tiebreaker. Folder's `INGESTION.md` "file as `entities/` pages, one per person" beats default placement algorithm.
- **Cannot extend frontmatter schema or waive chain of evidence.** `wiki-conventions` schema is fixed contract; hint steers judgment inside procedure — chunking, tag preference, `source_date` derivation, kind, typed edges — never adds frontmatter key, kind, or folder. "List recipients" puts recipients in page **body**; does not mint `recipients:` key. `source/` stub and back-edges not optional — `enchiridion ingest` rejects plan without them regardless.
- **An `INGESTION.md` is never itself ingested.** Instructions, not content — if handed as `<path>`, skip.

Folder may also carry `.ingestignore`, sweep-only policy — see [`reference/sweep.md`](reference/sweep.md); no bearing on this procedure.

## Procedure

Given one document at `<path>`. Where step calls for multiple independent tool calls — e.g. reading document alongside folder's `INGESTION.md` — issue together in one message, not serially; each extra turn re-reads full context.

1. **Read** document in full, alongside `<path>`'s folder's `INGESTION.md` if exists (see [`INGESTION.md` folder hint](#ingestionmd-folder-hint)). Hints override defaults below.
2. **Semantic-chunk.** One page or several? Default one; split when document covers multiple independent ideas deserving own future citation.
3. **Draft the plan, then discover, then classify.** Write `<plan.json>` now — same file step 4 finishes and step 5 runs; nothing written twice. Give every candidate chunk from step 2 a `pages` entry with `title`, `frontmatter.summary`, `body` filled in (full shape in step 4); leave `edges` and unjudged frontmatter for step 4. Run `python "<plugin-root>/scripts/discover.py" --plan <plan.json> --tags-containing "<candidate tags, comma list>" --tag-count "<candidate tags, comma list>"` once against whole draft — no per-chunk calls, no scratch files. Derive both comma lists from this draft's own candidate tags (the tags step 2's chunks are likely to want); always pass both, never left optional. Uses same BM25 index as `enchiridion search`; returns candidates classified `duplicate`/`refines`/`related`/`distinct` per page, each carrying `summary`, `tags`, `volatility`, `superseded_by` — plus, in place of the full tag-vocabulary dump, the plain-text matches for `--tags-containing` and per-tag counts for `--tag-count` (0 means safe to mint) for step 4 tag-minting.

   Hint is starting point — confirm or override against candidate's own `summary`; record only which **op** each plan entry gets. Step 4 owns every write; nothing here calls `Edit` or `wikipage.py`.
   - **`distinct` (or no candidates).** New subject. Keep as `op: "create"`; consider surfaced pages as typed-edge targets in step 4.
   - **`related`.** Worth typed edge (usually `related`, sometimes `example-of`) from new page in step 4, not same subject — keep as `op: "create"`.
   - **`duplicate` or `refines`, no conflict.** Candidate adds to or restates existing page without contradicting. Set plan entry to `op: "update"` targeting `page_ref` — step 4 fills whichever of `summary`/`tags`/`source_date`/`volatility`/`body` changes. Record as `updated`, not `created`, in manifest and commit.
     - List-valued keys (`tags`, edge-lists: `refines`/`contradicts`/`example-of`/`source`/`related`/`supersedes`) **unioned** with existing values when `enchiridion ingest` applies plan — never diff, always full intended membership.
   - **Contradiction.** Candidate conflicts with existing page's claim — semantic judgment hint can't make (only measures lexical overlap), check regardless. **Never overwrite existing page.** Keep new page as `op: "create"`, set `contradicts` and `supersedes` on it pointing at superseded `page_ref`. Superseded page content untouched; only new page carries these edges.
   - Candidate touching multiple existing pages: judge each pairing independently — document can update one while contradicting another.
4. **Finish the plan.** Fill `edges` and any frontmatter step 3 left open on `<plan.json>` — placement, frontmatter, body, commit one downstream call: `"<plugin-root>/bin/enchiridion" ingest --plan <plan.json>` (step 5). Full shape:

   ```jsonc
   {
     "title": "<source document's title>",
     "source_date": "<the document's own date, not today's>",
     "raw": "raw/<artifact's path, exactly as it sits on disk>",   // omit if nothing came from raw/
     "pages": [
       {
         "op": "create",                 // the artifact's source/ stub — mandatory whenever "raw" is set
         "kind": "source",
         "title": "<the artifact's own title>",
         "body": "<what this artifact is; thin when its content was distilled into the pages below>",
         "frontmatter": {
           "summary": "<one line, ≤~20 words>",
           "raw_source": true   // required here, omitted on every other kind — marks this page as the "raw" field's stub; `enchiridion ingest` composes the actual link
         },
         "edges": {}
       },
       {
         "op": "create",
         "kind": "concept",            // source | synthesis | entity | concept — your step-4-equivalent judgment, first match wins per wiki-conventions
         "title": "<page title>",
         "body": "<full markdown body>",
         "frontmatter": {
           "summary": "<one line, ≤~20 words>",
           "tags": ["<reuse an existing tag where one fits, mint only when nothing does>"],
           "source_date": "<same as above, or this page's own if it differs>",
           "volatility": "stable | evolving | volatile"
         },
         "edges": {
           "source": ["wiki/sources/<stub-slug>.md"],  // mandatory back-edge to the stub above
           "related": ["<vault-relative path.md>"],
           "supersedes": ["<vault-relative path.md>"]           // include on the new page when step 3 found a contradiction to resolve
         }
       },
        {
          "op": "update",
          "page_ref": "wiki/concepts/existing-page.md",   // the page step 3 classified as substantive-overlap
         "title": "<unchanged or corrected title>",
         "frontmatter": { "volatility": "evolving", "tags": ["new-tag"] },   // scalar keys overwrite; a list-valued key like tags is unioned with what the page already has, never overwritten
         "edges": {
           "source": ["wiki/sources/<stub-slug>.md"],  // an updated page needs it too
           "related": ["<vault-relative path.md>"]                   // edge-list keys union with what the page already has
         }
         // omit "body" entirely when nothing in the body changes
       }
     ]
   }
   ```

   Every `edges` value and `raw_source: true` names target by **vault-relative path only** (`"wiki/concepts/foo.md"`, matching the kind-folders in [Vault structure](../wiki-conventions/SKILL.md)) — never a composed `[Title](../dest.md)` string. `enchiridion ingest` reads each target's title (on disk or from sibling in plan), works out `../` relativisation, percent-encodes destination; never build link string by hand. Exception: *body* links — write as ordinary markdown (`[label](destination)`), encoded or not; `enchiridion ingest` re-encodes on write.

   Judgment calls when filling in (folder's `INGESTION.md` may override any, except where noted):
   - **Kind** (create pages only): per [Placement algorithm](../wiki-conventions/SKILL.md#placement-algorithm), first match wins. `enchiridion ingest` computes kebab-slug from `kind`+`title` — never hand-slugify.
   - **The `source/` stub is not optional** (see [The chain of evidence](../wiki-conventions/SKILL.md#the-chain-of-evidence)) — thin fine, absent not. Prior pass already filed stub: target with `op: "update"`, not second create.
   - **Typed edges** ([vocabulary](../wiki-conventions/SKILL.md#typed-edges)) — judge for **every new or updated page** against every page surfaced in step 3. Assign most specific type true (`related` only as fallback); `contradicts`/`supersedes` decided by step 3, belong on *new* page only — never on superseded page.
     - Non-judgment edge: **every page except stub carries `source` edge to stub** — each chunk of multi-chunk split, `op: "update"` same as `create`. Edges merge on update so restating safe; omit only if page already carries it from earlier pass.
   - **Body** for `update` page: write *complete* new body (not diff) when material changes; omit `body` key entirely to leave existing body untouched. For `create`, `body` always required.
   - **`raw_source: true`** derives link from plan's `raw` field. **Ingestion never renames raw file** — file from outside plugin keeps name verbatim; don't add `YYYY-MM-DD-hhmm-` prefix (bound at creation, plugin-created files only). `enchiridion ingest` mechanics: literal `#` separates anchor from path, so `#` in *filename* must be `%23`; unbalanced `)` in filename must be encoded (destination ends at first unbalanced `)`).
5. **Run it.** `"<plugin-root>/bin/enchiridion" ingest --plan <plan.json>` validates whole plan up front (required fields, `update` `page_ref` exists, `create` target doesn't yet, every edge/`raw_source` resolves — including siblings this plan creates — and when `raw` set, chain of evidence: stub exists and every page links back) before writing, then executes place → frontmatter → body → commit in one pass (index not touched — next search's staleness scan picks the pages up) and prints commit SHA. On error: nothing committed, written pages left on disk uncommitted (writes idempotent — fix plan and rerun, don't hand-repair).
6. **Report.** Short manifest only — pages created vs. updated, edges added, `supersedes` pairs recorded. No page-content dumps.
