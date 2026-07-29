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

## Folder hints: `INGESTION.md`

A raw folder may carry an optional `INGESTION.md` holding freeform, human-authored instructions for ingesting *that folder's* documents — e.g. `raw/emails/INGESTION.md` saying "take `source_date` from the message's `Date:` header, list the recipients in the body, prefer the `correspondence` tag". Read it like a `SKILL.md`: plain prose to interpret, not a schema to parse.

- **Lookup is the document's own folder, and only that.** Ingesting `raw/emails/foo.eml` looks for `raw/emails/INGESTION.md`. There is deliberately **no ancestor walk** — `raw/INGESTION.md` and a vault-root one are *not* consulted, so there is never a precedence question to resolve.
- **Hints win on conflict.** They are an explicit override for that folder, not a tiebreaker. If a folder's `INGESTION.md` says "file these as `entity/` pages, one per person", it beats the default placement algorithm's answer.
- **They cannot extend the frontmatter schema, or waive the chain of evidence.** The `wiki-conventions` schema is the fixed contract retrieval relies on; a hint steers judgment *already inside* this procedure — how to chunk, which tags to prefer, how to derive `source_date`, which subject-defined kind fits, which typed edges are likely — and never adds a frontmatter key, a kind, or a folder. So "list the recipients" puts recipients in the page **body**; it does not mint a `recipients:` key. The mandatory `source/` stub and its back-edges are likewise not a default a hint can turn off — `ingest.py` rejects a plan without them either way.
- **An `INGESTION.md` is never itself ingested.** It is instructions, not content — if you are handed one as `<path>`, or sweep a folder containing one, skip it.

## Procedure

Given one document at `<path>`:

1. **Read** the document in full, and — alongside it — `<path>`'s own folder's `INGESTION.md` if one exists (see [Folder hints](#folder-hints-ingestionmd) above). Anything it says overrides the defaults in the steps below.
2. **Semantic-chunk.** Decide whether it holds one page-worthy idea or several. Default to one page; split only when the document genuinely covers multiple independent ideas that would each deserve their own future citation.
3. **Check for overlap.** Find existing pages covering the same subject as any candidate chunk from step 2 by running `python "${CLAUDE_PLUGIN_ROOT}/scripts/overlap.py" --title "<chunk title>" --summary "<chunk summary>" --body-file <chunk body scratch file>`. It fronts the same BM25-ranked lexical index search.py uses, but classifies each hit's relationship for you (`duplicate`/`refines`/`related`/`distinct`) instead of leaving that judgment call to be re-derived from a raw score every run — and this is the step where a miss creates a *duplicate page*, so the ranking earns its keep here. The hint is a starting point, not the final word — confirm or override it against what you actually read:
   - **`distinct` (or no candidates at all).** The candidate is a new subject. Proceed to create a new page below; still consider any surfaced page(s) as typed-edge targets in step 4.
   - **`related`.** Worth a typed edge (usually `related`, sometimes `example-of`) from the new page in step 4, but not the same subject — proceed to create a new page.
   - **`duplicate` or `refines`, no conflict.** The candidate adds to, refines, or restates knowledge the existing page already carries, without contradicting it. **Update that page in place** rather than creating a duplicate: `Edit` its body and, via `wikipage.py set`, whichever of `summary`/`tags`/`source_date`/`volatility` the new material actually changes. Record it as `updated`, not `created`, in the manifest and commit.
     - `wikipage.py set` **overwrites** the key's whole value — it does not append. Use `wikipage.py merge <page> <key> <json-list>` instead of `set` for `tags` or any edge-list key (`refines`/`contradicts`/`example-of`/`source`/`related`/`supersedes`) on a page that already has one — it unions the existing entries with the new ones internally, so nothing has to get-then-set by hand.
   - **Contradiction.** The candidate's claim conflicts with an existing page's claim — a semantic judgment the hint can't make (it only measures lexical overlap), so check this regardless of hint. **Never overwrite the existing page.** Create a new page as usual (step 4 onward), and on the *new* page set `contradicts` (and, since this same ingestion pass resolves the conflict by replacement) `supersedes` — both pointing at the superseded page. The superseded page's content is left untouched; only the new page carries these edges.
   - When a candidate touches more than one existing page, judge each pairing independently — a document can update one page while contradicting another.
4. **Assemble the `IngestPlan`.** Everything downstream of the step 1-3 judgment — placement mechanics, frontmatter writes, body writes, the index, and the commit — is one call: `python "${CLAUDE_PLUGIN_ROOT}/scripts/ingest.py" --plan <plan.json>`. Write `<plan.json>` yourself (a scratch file, not a vault page) with this shape:

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
           "raw_source": "[<filename>](<relative/path/into/raw/>)"   // required here, omitted on every other kind — see wiki-conventions; the path is relative to THIS page once placed, and points at the artifact's real, unchanged filename
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
           "source": ["[<the stub's title>](../source/<stub-slug>.md)"],  // mandatory back-edge to the stub above
           "related": ["[<title>](<relative/path.md>)"],
           "supersedes": ["[<title>](<relative/path.md>)"]           // include on the new page when step 3 found a contradiction to resolve
         }
       },
       {
         "op": "update",
         "rel": "wiki/concept/existing-page.md",   // the page step 3 classified as substantive-overlap
         "title": "<unchanged or corrected title>",
         "frontmatter": { "volatility": "evolving", "tags": ["new-tag"] },   // scalar keys overwrite; a list-valued key like tags is unioned with what the page already has, never overwritten
         "edges": {
           "source": ["[<the stub's title>](../source/<stub-slug>.md)"],  // an updated page needs it too
           "related": ["[<title>](<relative/path.md>)"]                   // edge-list keys union with what the page already has
         }
         // omit "body" entirely when nothing in the body changes
       }
     ]
   }
   ```

   A few judgment calls stay yours when filling this in, mirroring the old steps 4-7 — and a folder's `INGESTION.md` may override any of them outright, except where noted below that it can't:
   - **Kind** (create pages only): `source/` (stand-in for the raw artifact) → `synthesis/` (saved query result) → `entity/` (named, repeatedly-linked thing) → `concept/` (default), first match wins. `ingest.py` computes the exact kebab-slug path from `kind`+`title` — never hand-slugify.
   - **The `source/` stub is not optional** (see [The chain of evidence](../wiki-conventions/SKILL.md#the-chain-of-evidence)). Whenever the plan names a `raw` artifact, one page in it is that artifact's `source/` stand-in, carrying `raw_source`. When the artifact's value is really the knowledge inside it and that lands in `concept/`/`entity/` pages, the stub is still there — just **thin**: `title`, a one-paragraph `summary`, the `raw_source` link, and a body that says what the artifact is rather than restating what was distilled out of it. If a previous pass already filed the stub, target it with an `op: "update"` instead of creating a second one.
   - **Typed edges** (`refines`/`contradicts`/`example-of`/`source`/`related`) — judge these for **every new or updated page**, against every existing page surfaced in step 3. Assign the most specific type that's true (`related` only as a fallback); `contradicts`/`supersedes` are already decided by step 3 where applicable, and belong on the *new* page only — never on the superseded page.
     - The one edge that is *not* judgment: **every page in the plan except the stub itself carries a `source` edge to the stub** — each page of a multi-chunk split, and an `op: "update"` page as much as a `create` one. The plan's edges are merged into an updated page, so restating it is safe; omit it only when that page already carries it from an earlier pass.
   - **Body**, for an `update` page: write the *complete* new body text (not a diff) when the material actually changes something in it; omit the `body` key entirely to leave the existing body untouched. For a `create` page, `body` is always required.
   - **`raw_source`**'s destination points at the raw artifact exactly where it sits (matching the plan's own `raw` field). **Ingestion never renames a raw file** — a file that came from outside the plugin keeps its name verbatim, forever, so don't add a `YYYY-MM-DD-hhmm-` prefix or expect one to appear (that prefix is bound at creation, and only for files the plugin itself creates). Percent-encode the destination — but not the label — where the filename needs it (`%` `space` `(` `)` `#` `<` `>`; leave unicode literal): `"[My Notes (draft).md](../../raw/My%20Notes%20%28draft%29.md)"`.
5. **Run it.** `python "${CLAUDE_PLUGIN_ROOT}/scripts/ingest.py" --plan <plan.json>` validates the whole plan up front (every required field, every `update`'s `rel` exists, every `create`'s target doesn't yet, every edge/`raw_source` link resolves to a real page — including a sibling page this same plan is about to create — and, when `raw` is set, the chain of evidence: the stub exists and every other page links back to it) before writing anything, then executes place → frontmatter → body → index → commit in one pass and prints the commit SHA. If it raises, nothing was committed but any pages it did get to are left on disk uncommitted (writes are idempotent, so fix the plan and rerun rather than hand-repairing).
6. **Report.** Reply with only a short manifest — pages created vs. updated, edges added, any `supersedes` pairs recorded — never page-content dumps.
