---
name: wiki-ingest
description: Turn a raw document into one or more schema-valid wiki pages — chunked, placed, tagged, linked, and committed per the wiki-conventions contract. Invoke via /wiki-ingest <path>, or whenever a document needs filing into the wiki vault.
---

# Wiki Ingest

Reads `wiki-conventions` for anything this procedure doesn't spell out — folder placement, frontmatter schema, link format, typed-edge vocabulary. This file is preloaded into the `wiki-ingest` agent's context at startup, and is also what `/wiki-ingest <path>` loads when invoked directly with a single file. It carries only what that agent executes — the single-file procedure. Sweeping a folder or all of `raw/` is a different consumer (the invoking session, never the agent) and lives in [`reference/sweep.md`](reference/sweep.md), read on demand rather than preloaded here.

Every script invoked below lives in this plugin's install directory and resolves the vault root itself — see the `## Scripts` section of `wiki-conventions` for the full reference (vault-root resolution, `${CLAUDE_PLUGIN_ROOT}`, common tasks, and the script catalogue).

## Invocation

- `/wiki-ingest <folder>` or `/wiki-ingest` (no path) — a **sweep**, not a single ingestion. `Read` [`reference/sweep.md`](reference/sweep.md) now and follow it instead of the rest of this file.
- `/wiki-ingest <file>` — ingest one document. This is the procedure below.
- **If you are not already running as the `wiki-ingest` agent** (your own system prompt doesn't identify you as it — e.g. you were invoked directly via `/wiki-ingest <path>` in an ordinary session) and `<path>` is a single file, your only action is to delegate: call `Task` with `subagent_type: "wiki-ingest"` and a prompt containing the document path, then relay the manifest it returns back to the user verbatim.
- **If you are the `wiki-ingest` agent**, continue directly with the procedure below using your own tools. (The agent only ever does single-file work — a sweep delegates to it one file at a time, per [`reference/sweep.md`](reference/sweep.md).)

## `INGESTION.md` folder hint

A raw folder may carry `raw/<folder>/INGESTION.md`: freeform, human-authored instructions for ingesting *that folder's* documents — e.g. "take `source_date` from the message's `Date:` header, list the recipients in the body, prefer the `correspondence` tag". Read it like a `SKILL.md`: plain prose to interpret, not a schema to parse.

- **Lookup is the document's own folder, and only that.** Ingesting `raw/emails/foo.eml` looks for `raw/emails/INGESTION.md`. There is deliberately **no ancestor walk** — `raw/INGESTION.md` and a vault-root one are *not* consulted, so there is never a precedence question to resolve.
- **Hints win on conflict.** They are an explicit override for that folder, not a tiebreaker. If a folder's `INGESTION.md` says "file these as `entities/` pages, one per person", it beats the default placement algorithm's answer.
- **They cannot extend the frontmatter schema, or waive the chain of evidence.** The `wiki-conventions` schema is the fixed contract retrieval relies on; a hint steers judgment *already inside* this procedure — how to chunk, which tags to prefer, how to derive `source_date`, which subject-defined kind fits, which typed edges are likely — and never adds a frontmatter key, a kind, or a folder. So "list the recipients" puts recipients in the page **body**; it does not mint a `recipients:` key. The mandatory `source/` stub and its back-edges are likewise not a default a hint can turn off — `ingest.py` rejects a plan without them either way.
- **An `INGESTION.md` is never itself ingested.** It is instructions, not content — if you are handed one as `<path>`, skip it.

A folder may also carry `.ingestignore`, a sweep-only policy file — see [`reference/sweep.md`](reference/sweep.md); it has no bearing on this procedure.

## Procedure

Given one document at `<path>`. Where a step below calls for more than one independent tool call — e.g. reading the document alongside its folder's `INGESTION.md` — issue them together in one assistant message rather than serially; each extra turn re-reads the whole context.

1. **Read** the document in full, and — alongside it, in the same message — `<path>`'s own folder's `INGESTION.md` if one exists (see [`INGESTION.md` folder hint](#ingestionmd-folder-hint) above). Anything it says overrides the defaults in the steps below.
2. **Semantic-chunk.** Decide whether it holds one page-worthy idea or several. Default to one page; split only when the document genuinely covers multiple independent ideas that would each deserve their own future citation.
3. **Draft the plan, then discover, then classify.** Write `<plan.json>` now — the same file step 4 finishes and step 5 runs, so nothing gets written twice. Give every candidate chunk from step 2 a `pages` entry with `title`, `frontmatter.summary`, and `body` filled in (the full shape is in step 4); leave `edges` and any not-yet-judged frontmatter for step 4. Then run `python "${CLAUDE_PLUGIN_ROOT}/scripts/discover.py" --plan <plan.json>` once against the whole draft — no per-chunk calls, no body scratch files. It fronts the same BM25-ranked lexical index `search.py` uses, and for every page in the plan returns candidates classified `duplicate`/`refines`/`related`/`distinct`, each carrying everything you'd otherwise open the page to read (`summary`, `tags`, `volatility`, `superseded_by`) — plus, once for the whole call, the vault's tag vocabulary with usage counts, for the tag-minting judgment in step 4.

   The hint is a starting point, not the final word — confirm or override it against the candidate's own `summary`, and record only which **op** each plan entry gets. Step 4 owns every write; nothing here calls `Edit` or `wikipage.py` directly.
   - **`distinct` (or no candidates at all).** The candidate is a new subject. Keep it as `op: "create"` in the plan; still consider any surfaced page(s) as typed-edge targets in step 4.
   - **`related`.** Worth a typed edge (usually `related`, sometimes `example-of`) from the new page in step 4, but not the same subject — keep it as `op: "create"`.
   - **`duplicate` or `refines`, no conflict.** The candidate adds to, refines, or restates knowledge the existing page already carries, without contradicting it. Set the plan entry to `op: "update"` targeting that page's `page_ref` — step 4 fills in whichever of `summary`/`tags`/`source_date`/`volatility`/`body` the new material actually changes. Record it as `updated`, not `created`, in the manifest and commit.
     - A list-valued key (`tags`, or any edge-list key: `refines`/`contradicts`/`example-of`/`source`/`related`/`supersedes`) is **unioned** with what the page already has when `ingest.py` applies the plan — never hand it a diff, always the full new membership you intend.
   - **Contradiction.** The candidate's claim conflicts with an existing page's claim — a semantic judgment the hint can't make (it only measures lexical overlap), so check this regardless of hint. **Never overwrite the existing page.** Keep the new page as `op: "create"`, and on it set `contradicts` (and, since this same ingestion pass resolves the conflict by replacement) `supersedes` — both pointing at the superseded page's `page_ref`. The superseded page's content is left untouched; only the new page carries these edges.
   - When a candidate touches more than one existing page, judge each pairing independently — a document can update one page while contradicting another.
4. **Finish the plan.** Fill in the `edges`, and any frontmatter step 3 left open, on the `<plan.json>` you already drafted — placement mechanics, frontmatter writes, body writes, the index, and the commit are all one downstream call: `python "${CLAUDE_PLUGIN_ROOT}/scripts/ingest.py" --plan <plan.json>` (step 5). The full shape:

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
           "raw_source": true   // required here, omitted on every other kind — marks this page as the "raw" field's stub; ingest.py composes the actual link
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

   Every `edges` value and `raw_source: true` names its target by **vault-relative path only** (`"wiki/concepts/foo.md"`, matching `place.KIND_FOLDERS`'s folders) — never a composed `[Title](../dest.md)` string. `ingest.py` reads each target's title (on disk, or from this same plan for a sibling it's about to create), works out the `../` relativisation from the writing page's own location, and percent-encodes the destination; you never build the link string by hand. The one exception is a *body* link — write it as ordinary markdown prose (`[label](destination)`), encoded or not; `ingest.py` re-encodes it on write regardless, from whatever you gave it.

   A few judgment calls stay yours when filling this in, mirroring the old steps 4-7 — and a folder's `INGESTION.md` may override any of them outright, except where noted below that it can't:
   - **Kind** (create pages only): per the [Placement algorithm](../wiki-conventions/SKILL.md#placement-algorithm), first match wins. `ingest.py` computes the exact kebab-slug path from `kind`+`title` — never hand-slugify.
   - **The `source/` stub is not optional** (see [The chain of evidence](../wiki-conventions/SKILL.md#the-chain-of-evidence)) — thin is fine, absent is not. If a previous pass already filed the stub, target it with an `op: "update"` instead of creating a second one.
   - **Typed edges** ([vocabulary](../wiki-conventions/SKILL.md#typed-edges)) — judge these for **every new or updated page**, against every existing page surfaced in step 3. Assign the most specific type that's true (`related` only as a fallback); `contradicts`/`supersedes` are already decided by step 3 where applicable, and belong on the *new* page only — never on the superseded page.
     - The one edge that is *not* judgment: **every page in the plan except the stub itself carries a `source` edge to the stub** — each page of a multi-chunk split, and an `op: "update"` page as much as a `create` one. The plan's edges are merged into an updated page, so restating it is safe; omit it only when that page already carries it from an earlier pass.
   - **Body**, for an `update` page: write the *complete* new body text (not a diff) when the material actually changes something in it; omit the `body` key entirely to leave the existing body untouched. For a `create` page, `body` is always required.
   - **`raw_source: true`** derives its link from the plan's own `raw` field — the raw artifact wherever it sits. **Ingestion never renames a raw file** — a file that came from outside the plugin keeps its name verbatim, forever, so don't add a `YYYY-MM-DD-hhmm-` prefix or expect one to appear (that prefix is bound at creation, and only for files the plugin itself creates). Two footnotes on the mechanics `ingest.py` handles for you: a literal `#` always separates an anchor from the path, so a `#` inside a *filename* must be written `%23`; an unbalanced `)` inside a filename must likewise be encoded, since a destination ends at the first unbalanced `)`.
5. **Run it.** `python "${CLAUDE_PLUGIN_ROOT}/scripts/ingest.py" --plan <plan.json>` validates the whole plan up front (every required field, every `update`'s `page_ref` exists, every `create`'s target doesn't yet, every edge/`raw_source` link resolves to a real page — including a sibling page this same plan is about to create — and, when `raw` is set, the chain of evidence: the stub exists and every other page links back to it) before writing anything, then executes place → frontmatter → body → index → commit in one pass and prints the commit SHA. If it raises, nothing was committed but any pages it did get to are left on disk uncommitted (writes are idempotent, so fix the plan and rerun rather than hand-repairing).
6. **Report.** Reply with only a short manifest — pages created vs. updated, edges added, any `supersedes` pairs recorded — never page-content dumps.
