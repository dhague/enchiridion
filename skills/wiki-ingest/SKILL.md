---
name: wiki-ingest
description: Turn a raw document into one or more schema-valid wiki pages — chunked, placed, tagged, linked, and committed per the wiki-conventions contract, gated by the script layer's mechanical checks. Invoke whenever a document needs filing into the wiki vault. Runs on the bundled node scripts in this skill's scripts/ directory; no MCP tools, no network.
compatibility: requires node on PATH; the vault is a git repository reachable on local disk
metadata:
  author: dhague
  license: Apache-2.0
---

# Wiki Ingest (host-neutral)

Reads `wiki-conventions` for anything this procedure doesn't spell out — folder structure, frontmatter schema, link format, typed-edge vocabulary, chain of evidence. That skill is the contract this procedure writes to. (On a host where `wiki-conventions` is a sibling installed skill, consult it the same way; where only this skill is installed, the vocabulary and schema it needs are condensed inline in [Wiki-conventions contract (condensed)](#wiki-conventions-contract-condensed).)

Single-file procedure only — **one raw artifact per plan**. A folder of documents means running this procedure once per file. Ingestion is a **write**: it creates pages and (when dedup says so) updates existing ones. The script layer backstops the *structure* of every write — missing `source/` stub, unresolvable edge, existing create target — while the *semantics* (kind, chunking, edge-typing, volatility, `source_date`) are your judgment. A bad plan is rejected before anything is written; nothing needs rolling back.

## Invocation

When a document should be filed into the vault, run the procedure below with your own tools. You hold the conversation: you can ask the user for the text of a non-text artifact, and for consent to seed a vault, so those gates stay with you rather than being deferred to an outer session.

Every script below is the bundled `enchiridion` entry point inside this skill's `scripts/` directory, invoked **directly** as:

```bash
node <scripts>/enchiridion.cjs <subcommand> …
```

where `<scripts>` is this skill's `scripts/` subdirectory (the installed `enchiridion.cjs` + `node-sqlite3-wasm.wasm` supporting files). If `node` is not on PATH, say so plainly and stop; this skill cannot run without it.

**Vault root.** The scripts resolve the vault root themselves: `$WIKI_ROOT`, else nearest ancestor holding a `wiki/` directory or `.wiki-root` marker, else the working directory. If the user has not yet pointed you at a vault, ask on first use — a one-line prompt, e.g. *"Which folder is your vault? (I'll file documents into its `wiki/` pages.)"* — then set `WIKI_ROOT=<dir>` inline on every subsequent invocation. Prefer running with the working directory already inside the vault when that is possible.

**The artifact must already sit inside the vault.** `read-page` is vault-relative and is your only read. If the file the user wants ingested is outside the vault, ask them to drop it in (under `raw/`) first, then read it from there. Files outside the vault are out of scope for this procedure.

## `INGESTION.md` folder hint

A `raw/` folder may carry `raw/<folder>/INGESTION.md`: freeform human instructions for ingesting that folder's documents — e.g. "take `source_date` from `Date:` header, list recipients in body, prefer `correspondence` tag". Read it like prose to interpret, not schema to parse; read it with `read-page` like any other vault file.

- **Lookup is the document's own folder only.** Ingesting `raw/emails/foo.eml` looks for `raw/emails/INGESTION.md`. **No ancestor walk** — `raw/INGESTION.md` and vault root are not consulted. No precedence question.
- **Hints win on conflict.** Explicit override, not tiebreaker. The folder's `INGESTION.md` "file as `entities/` pages, one per person" beats the default placement algorithm.
- **Cannot extend the frontmatter schema or waive chain of evidence.** The schema is a fixed contract; a hint steers judgment inside the procedure — chunking, tag preference, `source_date` derivation, kind, typed edges — never adds a frontmatter key, kind, or folder. "List recipients" puts recipients in the page **body**; it does not mint a `recipients:` key. The `source/` stub and back-edges are not optional — `enchiridion ingest` rejects a plan without them regardless.
- **An `INGESTION.md` is never itself ingested.** Instructions, not content — if handed one as the file, skip it.

## Procedure

Given one document already inside the vault.

1. **Read the artifact.** Read the document in full with `read-page`, alongside its folder's `INGESTION.md` if one exists (see [`INGESTION.md` folder hint](#ingestionmd-folder-hint)). Hints override the defaults below.

   ```bash
   node <scripts>/enchiridion.cjs read-page raw/emails/foo.eml
   ```

   **Non-text artifacts** (PDF, image, audio): `read-page` reads bytes as markdown, so a PDF or image cannot be read through it. Extract the text with **your own document-reading capability**; if you cannot, **ask the user to supply the text**. Extraction is host-side only: the binary artifact stays untouched and remains the `source/` stub's `raw_source`; no extraction output lands in the vault.

2. **Semantic-chunk.** One page or several? Default one; split when the document covers multiple independent ideas deserving their own future citation.

3. **Draft the plan, then discover, then classify.** Compose the full plan JSON now — the same plan step 4 completes and step 5 runs, so author it in full rather than sketching it. Give every candidate chunk from step 2 a `pages` entry with `title`, `frontmatter.summary`, and `body` filled in (full shape in step 4); leave `edges` and unjudged frontmatter for step 4. Then run `discover` **once against the whole draft**, feeding the draft over **stdin**:

   ```bash
   node <scripts>/enchiridion.cjs discover --plan - \
       --tags-containing "<candidate tags, comma list>" \
       --tag-count "<candidate tags, comma list>"
   ```

   Derive both comma lists from this draft's own candidate tags (the tags step 2's chunks are likely to want); always pass both, never leave either optional. It uses the same BM25 index as `search`; it returns candidates classified `duplicate`/`refines`/`related`/`distinct` per page, each carrying `summary`, `tags`, `volatility`, `superseded_by` — plus, in place of the full tag-vocabulary dump, the plain-text matches for `--tags-containing` and per-tag counts for `--tag-count` (0 means safe to mint) for step 4's tag-minting.

   The hint is a starting point — confirm or override against the candidate's own `summary`; record only which **op** each plan entry gets. Nothing here writes.

   - **`distinct` (or no candidates).** New subject. Keep as `op: "create"`; consider surfaced pages as typed-edge targets in step 4.
   - **`related`.** Worth a typed edge (usually `related`, sometimes `example-of`) from the new page in step 4, not the same subject — keep as `op: "create"`.
   - **`duplicate` or `refines`, no conflict.** The candidate adds to or restates an existing page without contradicting it. Set the plan entry to `op: "update"` targeting `page_ref` — step 4 fills whichever of `summary`/`tags`/`source_date`/`volatility`/`body` changes. Record as `updated`, not `created`, in the manifest and commit. List-valued keys (`tags`, edge lists: `refines`/`contradicts`/`example-of`/`source`/`related`/`supersedes`) are **unioned** with existing values when `enchiridion ingest` applies the plan — never diff, always full intended membership.
   - **Contradiction.** The candidate conflicts with an existing page's claim — the semantic hint cannot make this call (it only measures lexical overlap), so check regardless. **Never overwrite the existing page.** Keep the new page as `op: "create"`, set `contradicts` and `supersedes` on it pointing at the superseded `page_ref`. The superseded page's content is untouched; only the new page carries these edges.
   - A candidate touching multiple existing pages: judge each pairing independently — a document can update one while contradicting another.

4. **Finish the plan.** Fill `edges` and any frontmatter step 3 left open. Placement, frontmatter, body, and commit are all one downstream call (step 5). Full shape:

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
           "raw_source": true   // required here, omitted on every other kind — marks this page as the "raw" field's stub; enchiridion ingest composes the actual link
         },
         "edges": {}
       },
       {
         "op": "create",
         "kind": "concept",            // source | synthesis | entity | concept — your step-4-equivalent judgment, first match wins per the placement algorithm
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
           "supersedes": ["<vault-relative path.md>"]  // include on the new page when step 3 found a contradiction to resolve
         }
       },
       {
         "op": "update",
         "page_ref": "wiki/concepts/existing-page.md",  // the page step 3 classified as substantive-overlap
         "title": "<unchanged or corrected title>",
         "frontmatter": { "volatility": "evolving", "tags": ["new-tag"] },  // scalar keys overwrite; a list-valued key like tags is unioned with what the page already has, never overwritten
         "edges": {
           "source": ["wiki/sources/<stub-slug>.md"],  // an updated page needs it too
           "related": ["<vault-relative path.md>"]  // edge-list keys union with what the page already has
         }
         // omit "body" entirely when nothing in the body changes
       }
     ]
   }
   ```

   Every `edges` value and `raw_source: true` names its target by **vault-relative path only** (`"wiki/concepts/foo.md"`) — never a composed `[Title](../dest.md)` string. `enchiridion ingest` reads each target's title (on disk or from a sibling in the same plan), works out the `../` relativisation, percent-encodes the destination; never build a link string by hand. Exception: *body* links — write those as ordinary markdown (`[label](destination)`), encoded or not; `enchiridion ingest` re-encodes on write.

   Judgment calls when filling in (the folder's `INGESTION.md` may override any, except where noted):

   - **Kind** (create pages only): per the [Placement algorithm](#placement-algorithm), first match wins. `enchiridion ingest` computes the kebab-slug from `kind`+`title` — never hand-slugify.
   - **The `source/` stub is not optional** (see [Chain of evidence](#the-chain-of-evidence)) — thin is fine, absent is not. If a prior pass already filed the stub, target it with `op: "update"`, not a second create.
   - **Typed edges** ([vocabulary](#typed-edges)) — judge for **every new or updated page** against every page surfaced in step 3. Assign the most specific type that is true (`related` only as fallback); `contradicts`/`supersedes` are decided by step 3 and belong on the *new* page only — never on the superseded page.
     - Non-judgment edge: **every page except the stub carries a `source` edge to the stub** — each chunk of a multi-chunk split, `op: "update"` the same as `create`. Edges merge on update so restating is safe; omit only if the page already carries it from an earlier pass.
   - **Body** for an `update` page: write the *complete* new body (not a diff) when material changes; omit the `body` key entirely to leave the existing body untouched. For `create`, `body` is always required.
   - **`raw_source: true`** derives the link from the plan's `raw` field. **Ingestion never renames a raw file** — a file from outside the plugin keeps its name verbatim. Mechanics: a literal `#` separates an anchor from a path, so a `#` in a *filename* must be `%23`; an unbalanced `)` in a filename must be encoded (a destination ends at the first unbalanced `)`).

5. **Run it.** Feed the finished plan over **stdin**:

   ```bash
   node <scripts>/enchiridion.cjs ingest --plan -
   ```

   It validates the whole plan up front (required fields, an `update` `page_ref` exists, a `create` target doesn't yet, every edge/`raw_source` resolves — including siblings this plan creates — and when `raw` is set, the chain of evidence: a stub exists and every page links back) before writing anything, then executes place → frontmatter → body → commit in one pass (the index is not touched — the next `search`'s staleness scan picks the pages up) and prints the commit SHA. On error: nothing is committed and written pages are left on disk uncommitted (writes are idempotent — fix the plan and rerun, don't hand-repair). One `ingest` call per iteration is the taught path; `--dry-run` validates without writing and is an extra, not a step you need — the up-front validation already refuses to write on a bad plan.

   If the failure reads like *not a git worktree / no vault here*, see [Seeding a vault](#seeding-a-vault) — ask the user before deciding.

6. **Report.** Short manifest only — pages created vs. updated, edges added, `supersedes` pairs recorded. No page-content dumps.

## Seeding a vault

The vault is a **git repository** — that is what makes commits and the search index work. If an `ingest` (or `read-page`) run fails in a way that reads as *"this folder isn't a git worktree / no vault here"* — e.g. no `wiki/` directory, no `.wiki-root` marker, not a git worktree — **ask the user** whether to seed a vault in this folder before giving up. You hold the conversation, and seeding writes to disk, so the gate is a question, not a guess.

1. **Ask** — one line, naming what would be created: *"This folder isn't a wiki vault yet — seed one here (folders + git init + initial commit)? (y/n)"*
2. **On anything but an explicit yes, stop** and report the original error.
3. **On explicit yes, seed** in dedicated mode — no settings.json, no plugin-root; the vault works anyway because every invocation carries `WIKI_ROOT=<dir>` inline:

   ```bash
   node <scripts>/enchiridion.cjs init "<dir>" --mode dedicated
   ```

   An empty folder seeds cleanly today. Seeding a folder that already carries a `wiki/` tree but no git (e.g. a converted LLM-wiki — the existing pages swept into the initial commit, with LLM-wiki scaffolding like `log.md`/`index.md`/`_index.md` gitignored so it stays out of search) is part of the seeding build and may fail with "already looks like a vault" until that lands — if it does, report the error as-is.
4. **Rerun the step that failed.** If it fails again, report the new error — don't loop the seed.

## Wiki-conventions contract (condensed)

When a separate `wiki-conventions` skill isn't installed, this is the schema and vocabulary the procedure writes to. The fixed half (structure, schema, links, edges) never varies per vault; the emergent half (`tags`) is generated at ingestion, not enumerated here.

### Placement algorithm

**Top-to-bottom, first match wins** — placement is deterministic. Kinds split into *origin-defined* (`source`, `synthesis`) and *subject-defined* (`entity`, `concept`):

1. Stand-in for an ingested raw artifact? → **`sources/`** (must carry `raw_source:` → its `raw/` file).
2. Saved query result synthesized from other pages? → **`synthesis/`**.
3. Primarily a named thing linked repeatedly? → **`entities/`**.
4. If there are other Kind folders (e.g. `decisions/`, `escalations`, `projects`, etc.) and the page is clearly a member of one, → that folder.
5. Otherwise → **`concepts/`** (default).

Kind-folders pluralize (`concepts/`, `entities/`, `sources/`; `synthesis/` unchanged); **kind values stay singular** (`concept`, `entity`, `source`, `synthesis`). Page filenames are lowercase **kebab-slugs of the title, no date prefix**. `raw/` holds immutable originals, never edited, always git-tracked.

### Frontmatter schema

Every page opens with YAML frontmatter. Only fields requiring judgment live here — anything git can tell us is derived on demand.

```yaml
---
title: <human title>
summary: <one line, ≤ ~20 words>        # THE field retrieval reads first; write it well at ingestion
tags: [<emergent — reuse existing tags where sensible, mint new where needed; NOT a controlled vocabulary>]
source_date: <YYYY-MM-DD>               # when the knowledge is FROM (valid time) — judgment, not recoverable from git
raw_source: "[<filename>](<encoded relative/path into raw/>)"   # REQUIRED on sources/ pages; a single Markdown link to the ingested artifact. Omit on other kinds.
volatility: stable | evolving | volatile
# Relationships — each an optional list of relative-markdown links (quoted, so YAML doesn't read the [ as a flow sequence).
# Include only the keys that have links; omit the rest.
supersedes:                             # pages this page replaces (a recorded fact; see notes)
  - "[<title>](<relative/path.md>)"
refines:                                # typed edges: one key per edge type (see Typed edges)
  - "[<title>](<relative/path.md>)"
contradicts:
  - "[<title>](<relative/path.md>)"
example-of:
  - "[<title>](<relative/path.md>)"
source:
  - "[<title>](<relative/path.md>)"
related:
  - "[<title>](<relative/path.md>)"
---
```

Field notes:

- **`title`** — human-readable name; the filename is its kebab-slug.
- **`summary`** — the single most important field. One line, ≤ ~20 words, written well at ingestion.
- **`tags`** — emergent, not controlled. **Reuse an existing tag where one fits; mint a new one only where nothing does.** The `--tags-containing`/`--tag-count` output from step 3 is your vocabulary; a count of 0 means safe to mint.
- **`source_date`** — **valid time**: when the knowledge is *from* (the document's own date, the meeting's date). One canonical spelling: `YYYY-MM-DD`. Distinct from the commit date; never use today's date for the source's own.
- **`raw_source`** — **single markdown link into `raw/`** (title = the artifact's literal filename, destination = percent-encoded path), **required on `sources/` pages, omitted on every other kind**. Distinct from the `source`-type *edge*: this field points into `raw/`; the edge points at another `wiki/` page.
- **`volatility`** — `stable` | `evolving` | `volatile`. Authored, not inferred.
- **`supersedes`** — pages this page replaces. **Recorded fact**, stronger than any "newer wins" guess. On contradiction, ingestion **appends the new page and records `supersedes`; it does not overwrite** the old one. **Never author a `superseded_by` key** — the inverse is derived by inverting every page's `supersedes` edges.
- **typed-edge keys** (`refines`, `contradicts`, `example-of`, `source`, `related`) — each an optional list of markdown links to target pages; see [Typed edges](#typed-edges).
- **Deliberately absent**: `updated_at` and `ingested_at` — git's history is the authoritative record, derived on demand. Never add a frontmatter field for anything git already knows.

### The chain of evidence

**Every raw file a pass produces pages from gets a `sources/` stand-in, and every page produced carries a `source` edge back to it.** A reader can always walk *page → `sources/` stub → `raw/` artifact* — the one path that makes a citation checkable.

- **No exemption for distillation.** When the raw file's value lands in `concepts/`/`entities/` pages, the stub is still created — just a **thin stub**: `title`, one-paragraph `summary`, required `raw_source` link. Its job is to be the addressable link target.
- **The `source` back-edge is not judgment.** Unlike the other typed edges (weighed per page), this one is mandatory on every page of the pass — each page of a multi-chunk split, and a page **updated in place** as much as one newly created.
- **Enforced, not merely conventional.** `enchiridion ingest` validates both halves before writing: a plan naming a `raw` artifact must place a `sources/` page whose `raw_source` resolves to it, and every other page in that plan must carry a `source` edge to that stub. A plan that doesn't is rejected.

### Links

Links between pages are **relative markdown links — not wikilinks**: `[prepared statements](../concepts/prepared-statements.md)`, path relative to the linking file's location. Anchors append a heading fragment (GitHub-style slug of the target heading). Links into `raw/` are **percent-encoded** (space, `#`, `%`, `(`, `)`, `<`, `>`); everything else (unicode, `&`, `'`, `,`, `+`) stays literal. Frontmatter relationships use the same link form, always **quoted** (`"[…](…)"`) so YAML doesn't parse the leading `[` as a flow sequence. When the plan names targets by vault-relative path, `enchiridion ingest` composes all of this for you — you only hand-write *body* links.

### Typed edges

Each edge type is its own frontmatter key, holding a list of markdown links to target pages. The edge is **directional** — reads *this page → key → target*. Include only keys that have edges; omit the rest.

| Type | Reads as | Use when |
|---|---|---|
| `refines` | this page refines the target | Sharpens / extends / adds precision to the target's idea |
| `contradicts` | this page contradicts the target | A claim conflicts with the target's |
| `example-of` | this page is an example of the target | A concrete instance / case study of the general concept |
| `source` | this page is sourced from the target | Page draws content from target page (synthesis inputs; mandatory chain-of-evidence back-edge) |
| `related` | associatively related to the target | Real connection that is none of the above — the fallback |

`supersedes` (a page this page replaces) is a **recorded fact**, distinct from the typed edges; its inverse (`superseded_by`) is derived by inverting every page's `supersedes`, never authored directly.

**Ingestion guidance:** assign the most specific type that is true; reach for `related` only when no sharper type applies. The one exception to "judge per page" is the mandatory `source` back-edge. Under-assigning edges is silent quality loss — the graph is only as navigable as the edges recorded.
