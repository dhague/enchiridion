---
name: wiki-conventions
description: The single source of truth for the wiki vault — folder structure, frontmatter schema, relative-markdown link rules, and the typed-edge vocabulary. Preloaded by both the ingestion and retrieval agents; it is the contract between them. Consult before creating, moving, linking, or reading any wiki page.
---

# Wiki conventions

This skill is the **shared contract** between ingestion (`wiki-ingest`, Sonnet) and retrieval (`wiki-researcher`, Haiku). Both agents preload it. Ingestion writes to these rules; retrieval reads assuming them. If the two ever disagree about where a page lives, what a field means, or what an edge type asserts, this file is the tiebreaker — change it here, once, and both sides move together.

Two halves make up the contract. The **fixed half** — folder structure, schema shape, link format, edge vocabulary — is enumerated below and never varies per vault. The **emergent half** — `tags` — is generated at ingestion and deliberately *not* enumerated here (see [Tags](#tags)).

## Terminology

The wiki's units are **pages**, not "notes". A page is one markdown file under `wiki/`, carrying frontmatter plus a body. This also keeps `raw/notes/` (a raw-capture type) unambiguous.

## Vault structure

The vault is a **git repository**. Its layout is opinionated and **plugin-fixed** — the same in every vault, never tuned per-vault:

```
<vault root>/
├── wiki/                     ← pages; the vault marker
│   ├── _index.md             ← generated (build_index.py); indexes wiki/** only, never raw/
│   ├── concepts/             ← an idea / technique / pattern / principle / how-it-works (the default; kind value `concept`)
│   ├── entities/             ← a named thing linked repeatedly (person / team / product / tool / service / project / org; kind value `entity`)
│   ├── sources/               ← a stand-in for a raw artifact; one per ingested raw file, REQUIRES `raw_source:` → ../../raw/… (kind value `source`)
│   └── synthesis/            ← a saved query result; links to its inputs via `source`-type edges (kind value `synthesis`, folder unchanged)
└── raw/                      ← immutable originals, git-tracked, sibling of wiki/
    └── <user-extensible>/    ← emails/ meetings/ notes/ clippings/ documents/ … an OPEN set
```

- The four **kind-folders** under `wiki/` are the fixed set. **Kind** is the only axis both decidable from a page's content *and* domain-independent — the two properties a plugin-fixed structure requires. Domain- or topic-axed trees fail decidability (a page fits two sibling folders equally) and are not used. Kind-folders pluralize (`concepts/`, `entities/`, `sources/`); the kind **value** stored in frontmatter stays singular (`concept`, `entity`, `source`) — see [ADR-0008](../../../docs/adr/0008-kind-folders-plural-kind-values-singular.md).
- **Multi-membership never spawns a second folder.** A page that touches several subjects is filed once, by primary function; every other facet rides on **tags + typed edges**. The `_index.md` of summaries and the typed-edge graph are the real retrieval surface — the folder tree is only a thin, decidable filing handle.
- `raw/` is a **sibling** of `wiki/`, not a child — the immutable-originals-vs-generated split. `_index.md` indexes `wiki/**` only; it never lists `raw/`.
- `raw/` is an **inbox** that a deterministic script scans for new files, so its subfolders are **user-extensible** — the five above are typical defaults, not a closed set, and there is no mandated catch-all.

### Placement algorithm

Ingestion runs this **top-to-bottom, first match wins**, so placement is deterministic. The kinds split into *origin-defined* (`source`, `synthesis` — where the page came from, mutually exclusive) and *subject-defined* (`entity`, `concept` — what it's about):

1. Is it a stand-in for an ingested raw artifact? → **`sources/`** (must carry a `raw_source:` field → its `raw/` file).
2. Is it a saved query result synthesized from other pages? → **`synthesis/`**.
3. Is it primarily a named thing linked repeatedly? → **`entities/`**.
4. Otherwise → **`concepts/`** (the default).

### The chain of evidence

**Every raw file an ingestion produces pages from gets a `sources/` stand-in, and every page that pass produces or updates carries a `source` edge back to it.** So a reader can always walk *page → `sources/` stub → `raw/` artifact* and reach the thing a claim actually came from — the one path that has to exist for a citation to be checkable.

- **No exemption for distillation.** When a raw file's value is entirely the knowledge inside it, and that knowledge lands in `concepts/`/`entities/` pages, the stub is still created. It just becomes a **thin stub**: `title`, a one-paragraph `summary`, and the required `raw_source` link — nothing more. It does not duplicate the distilled content; its whole job is to be the addressable link target. A raw file that *is* the citable reference (a runbook, a spec) still gets a fuller `sources/` page. Same schema either way; only the body's substance differs.
- **The `source` back-edge is not judgment.** Unlike `refines`/`contradicts`/`example-of`/`related`, which are weighed per page, this edge is mandatory on every page of the pass — each page of a multi-chunk split, and a page **updated in place** as much as a newly created one.
- **Enforced, not merely conventional.** `ingest.py` validates both halves before it writes anything: a plan naming a `raw` artifact must place a `sources/` page whose `raw_source` resolves to it, and every other page in that plan must carry a `source` edge to that stub. A plan that doesn't is rejected, so a violation can never reach a commit. The stub may equally be one an earlier pass already left on disk.
- **A raw file ingestion declines outright** — spam, an exact duplicate, junk — produces no pages, so there is nothing for this rule to govern.

**Decidability bar:** given only a page's title + summary, the correct folder is the same every time, with no tie-break needed. If two folders are ever a genuine toss-up, the axis is wrong — the fix is to merge them and push the distinction to tags, never to add a folder.

**Subject tie-break:** a page plausibly *about* two subjects is filed by its primary function; the other subject becomes a tag or a typed edge.

### The `raw/` layer

`raw/` holds **content-immutable** originals. Ingestion **never edits a raw file's contents**. Raw filenames are preserved as-is, preserving their external identity. Plugin-authored raw files carry a `YYYY-MM-DD-hhmm-` prefix at creation. Links into `raw/` are percent-encoded (see [Links](#links)) so any filename is linkable. (Repairing links after *external* renames is deferred linter work — out of scope for the core build.)

### Naming

- **Kind-folders pluralize** (`concepts/`, `entities/`, `sources/`; `synthesis/` has no distinct plural, so it's unchanged) — **kind values stay singular** (`concept`, `entity`, `source`, `synthesis`), so the folder name no longer matches the value 1:1 ([ADR-0008](../../../docs/adr/0008-kind-folders-plural-kind-values-singular.md)). **Raw sub-folders are plural** (`emails/`, `meetings/`) and user-extensible, same as always.
- **Page filenames** are the lowercase **kebab-slug of the title, with no date prefix** — `concepts/prepared-statements.md`. Git carries the ingestion date and `source_date` carries the valid-time; a filename date would be a third, drifting clock.
- **Raw filenames** preserve their external identity unchanged. Plugin-authored raw files (created by ingestion, not sourced from outside) carry a `YYYY-MM-DD-hhmm-` prefix at creation, so their date is known at source. External raw files renamed outside the tool are repaired by the deferred linter; the core build never renames an existing raw file.

## Frontmatter schema

Every page opens with a YAML frontmatter block. **Only fields that require judgment live here** — anything git can tell us is derived on demand, never authored (see [Derived from git](#derived-from-git)).

```yaml
---
title: <human title>
summary: <one line, ≤ ~20 words>        # THE field retrieval reads first; write it well at ingestion
tags: [<emergent — reuse existing tags where sensible, mint new where needed; NOT a controlled vocabulary>]
source_date: <YYYY-MM-DD>               # when the knowledge is FROM (valid time) — judgment, not recoverable from git
raw_source: "[<filename>](<encoded relative/path into raw/>)"   # REQUIRED on sources/ pages; a single link (title = literal filename, dest = percent-encoded path) to the ingested artifact. Omit on other kinds.
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

- **`title`** — the human-readable name; the filename is its kebab-slug.
- **`summary`** — the single most important field. Retrieval judges a candidate page by its `summary` before ever reading the body, and `build_index.py` lifts it verbatim into `_index.md`. One line, ≤ ~20 words, written well at ingestion.
- **`tags`** — emergent, not controlled. See [Tags](#tags).
- **`source_date`** — the **valid time**: when the knowledge is *from* (the document's own date, the meeting's date). This is a judgment git cannot reconstruct, and it is what temporal queries key off. Distinct from when the page was committed.
- **`raw_source`** — a **single markdown link into `raw/`** (title = the artifact's literal filename, destination = the percent-encoded path), **required on `sources/` pages and omitted on every other kind**. It points at the immutable artifact this page stands in for — one link, not a list, since a `sources/` page stands in for exactly one artifact. Distinct from the `source`-type *edge* (see [Typed edges](#typed-edges)): this field points into `raw/`; the edge points at another `wiki/` page. The two were split onto different keys (`raw_source:` vs `source:`) precisely so nothing has to guess which is meant. Example: `"[my file.txt](../../raw/notes/my%20file.txt)"`.
- **`volatility`** — `stable` | `evolving` | `volatile`. Drives conditional decay at retrieval: `stable` facts do not age out, `volatile` ones are flagged as possibly current-only. A blanket recency prior is wrong on exactly the facts that were `stable`, which is why this is authored, not inferred.
- **`supersedes`** — optional list of markdown links to the pages this page replaces. A **recorded fact**, stronger than any "newer wins" guess: retrieval prefers a `supersedes` relationship over recency. On a contradiction, ingestion **appends a new page and records `supersedes`; it does not overwrite** the old one.
- **typed-edge keys** (`refines`, `contradicts`, `example-of`, `source`, `related`) — each an optional list of markdown links to the target pages; see [Typed edges](#typed-edges).

### Derived from git

**Deliberately absent from the schema:** `updated_at` and `ingested_at`. Git's commit history is the authoritative, trust-free record of when a page was first added and last touched — a hand-maintained timestamp an agent forgets to bump is worse than none. Derive both from `git log` on demand.

This gives a clean **bitemporal** model with no field able to lie: `source_date` is **valid time** (authored), the git commit date is **transaction time** (derived). Never add a frontmatter field for anything git already knows.

## Tags

Tags are the **emergent half** of the contract — generated at ingestion, not conformed to a fixed list. When ingesting, **reuse an existing tag** where one fits and **mint a new one** only where nothing fits. `discover.py --plan` hands back the vault's whole tag vocabulary with usage counts alongside every candidate, so reuse-first is something the discovery call gives you, not a discipline to go grep pages for. There is no controlled vocabulary to enumerate here and no lint rule rejecting "off-vocabulary" tags; consistency comes from reuse-first discipline, not from a closed set. This is why the folder structure is spelled out above and the tag set is not.

## Links

Links between pages are **relative markdown links — not wikilinks.**

- **Standard link:** `[prepared statements](../concepts/prepared-statements.md)`. The path is relative to the linking file's own location, so a link from `entities/` to `concepts/` climbs one level (`../concepts/…`).
- **Anchors:** append a heading fragment — `[the budget rule](../wiki-retrieval/SKILL.md#termination-budget)` / `[…](../concepts/caching.md#ttl)`. The fragment is the GitHub-style slug of the target heading.
- **Image embeds:** the leading-bang form — `![cache diagram](../raw/diagrams/2026-03-01-cache.png)`. Embeds may point into `raw/` (e.g. an extracted figure); ordinary links between pages stay within `wiki/`.

All links are **position-spliced** on move/rename by `vault.py move` (both inbound links across the vault and outbound links inside a moved page — the splicing itself lives in `wikipage.py`), so a page can be re-filed without hand-editing references. Links into `raw/` are **percent-encoded** to handle special characters in filenames: encode space, `#`, `%`, `(`, `)`, `<`, `>`; everything else (unicode, `&`, `'`, `,`, `+`) stays literal. This keeps any filename linkable without restricting the set of permitted characters. Obsidian cannot follow a destination containing a literal space, so encoding is essential for interoperability.

**Frontmatter relationships use the same link form.** The `raw_source` field, the `supersedes` key, and every typed-edge key hold this identical `[title](relative/path.md)` markdown, always **quoted** (`"[…](…)"`) so YAML doesn't parse the leading `[` as a flow sequence. `raw_source` holds a **single** such link; `supersedes` and the typed-edge keys hold a **list** (one link per item, `- "[…](…)"`). Writing them as real markdown links keeps every relationship clickable in plain markdown viewers and in Obsidian's Properties panel with no loss of semantics, and lets `wikipage.py` rewrite frontmatter and body links by the same rule.

## Typed edges

Typed edges are the **highest-leverage output of ingestion** — retrieval cannot recover an edge type that was never recorded. **Each edge type is its own frontmatter key**, holding a list of markdown links to the target pages:

```yaml
refines:
  - "[Prepared statements](../concepts/prepared-statements.md)"
source:
  - "[Deploy runbook](../sources/deploy-github-actions.md)"
```

The edge is **directional** — it reads *this page* → *key* → *target*. Include only the keys that have edges; omit the rest.

| Type | Reads as | Use when |
|---|---|---|
| **`refines`** | *this page refines the target* | This page sharpens, extends, or adds precision to the target's idea. The target is the broader/earlier statement; this page is the finer one. |
| **`contradicts`** | *this page contradicts the target* | This page's claim conflicts with the target's. Record the edge even before the conflict is resolved; when it *is* resolved by replacement, also set `supersedes`. |
| **`example-of`** | *this page is an example of the target* | This page is a concrete instance / case study of the general concept the target describes. |
| **`source`** | *this page is sourced from the target* | This page draws its content from the target **page**. Two uses: a `synthesis/` page lists under `source:` each `wiki/` page it was synthesized from, and — **mandatorily**, see [The chain of evidence](#the-chain-of-evidence) — every page an ingestion produces points at that raw file's `sources/` stub. **Not** the same as the `raw_source:` frontmatter field, which points a `sources/` page into `raw/`. |
| **`related`** | *this page is associatively related to the target* | A real connection that is none of the above. The catch-all — prefer a sharper type whenever one fits, because retrieval can follow a specific type purposefully and can only wander a `related` one. |

**Guidance for ingestion:** assign the most specific type that is true; reach for `related` only when no sharper type applies. The one exception to "judge it per page" is the mandatory `source` back-edge above. Under-assigning edges is a silent quality loss — the graph is only as navigable as the edges recorded. **Guidance for retrieval:** follow the edges the question implies (a "how does X work in practice" question follows `example-of`; a "is this still true" question follows `contradicts`/`supersedes`), within the stated hop budget.

## Scripts

Scripts that touch the vault resolve its root themselves (`$WIKI_ROOT`, else the nearest ancestor holding a `wiki/` directory or a `.wiki-root` marker, else cwd — see `vault.py`). Set `WIKI_ROOT` before invoking any of them. `wikipage.py` and `place.py` are the exceptions: they operate only on what you hand them, so no root is resolved.

The scripts themselves live in *this plugin's own* install directory, not the vault — invoke them via `${CLAUDE_PLUGIN_ROOT}/scripts/<name>.py` (the placeholder is substituted before you ever see this text, so the commands below are already the resolved absolute path). This works identically whether cwd is inside the plugin's own repo (dedicated mode) or a separate vault repo (query-from-anywhere mode).

### Script catalogue

Everything an agent invokes, plus the two libraries this file's own contracts name. Other modules in `scripts/` are internal implementation, reached through these.

| Script | Call it for | Usage |
|---|---|---|
| `discover.py` | Discovery before ingesting — BM25 overlap classification for every page a draft plan proposes, plus the vault's tag vocabulary. Call during ingestion step 3, before writing the real `IngestPlan`. | `discover.py --plan <draft-plan.json> [--limit N] [--duplicate-threshold F] [--related-threshold F]` → `{"pages": [{"title", "candidates": [{rel, title, score, hint, summary, tags, volatility, superseded_by}]}], "vocabulary": [{"tag", "count"}]}`. A single-page mode (`--title`/`--summary`/`--body-file`, same candidate shape) remains for ad-hoc checks outside a plan. |
| `search.py` | Any vault search. | `search.py "<terms>" [--tag T] [--tag-any T] [--kind K] [--since DATE] [--until DATE] [--date-field FIELD] [--volatility V] [--limit N] [--include-superseded] [--raw] [--json]`. Also `--reindex [--full]` and `--status`. |
| `ingest.py` | Executing an `IngestPlan` — validate, place, write, reindex, commit — after the agent assembles a plan (ingestion or synthesis save). | `ingest.py --plan <path>`. `ingest.py --ignore <raw_rel> [--ignore-comment <text>]` appends to that file's folder's `.ingestignore` instead — the sweep's `never` answer, mutually exclusive with `--plan`. |
| `ingest_scan.py` | Sweeping `raw/` for files needing ingestion (never-ingested, changed-since-ingestion) — `/wiki-ingest` sweep mode or `/wiki-watch` startup. | `ingest_scan.py [folder] --json`. |
| `watch_raw.py` | Long-running filesystem watcher over `raw/` with per-file debounce, exclusive lock, and queue file — launched in the background by `/wiki-watch`. | `watch_raw.py [--vault ROOT] [--debounce SECONDS] [--poll-interval SECONDS]`. |
| `vault.py` | Resolving the vault root, or moving a page (rewrites all inbound links across the vault and outbound links inside the page). Imported by most other scripts. | Bare invocation (or `vault.py root`) prints the resolved root. `vault.py move <old-rel> <new-rel>`. |
| `init_wiki.py` | Scaffolding a new empty vault: folders, empty index, `.gitignore`, git init, optional `settings.json`. Called from `/wiki-init`. | `init_wiki.py <path> --mode {query-from-anywhere|dedicated} [--plugin-root DIR]`. |
| `save-session-to-vault.py` | Capturing the current session's transcript as a raw file in the vault. Called from `/save-conversation`. | `save-session-to-vault.py [--slug "<phrase>"]`. |
| `wikipage.py` | Frontmatter edits during ingestion (discovery-driven updates, edge refinement). Pure-functional page model: get/set/merge, body access, link iteration. Imported by `vault.py`, `page_record.py`, `commit.py`, `chain_of_evidence.py`, `ingest.py`, `ingest_scan.py`, `search_index.py`. | `wikipage.py get <file> <key>` · `set <file> <key> <value> [--json]` (overwrite any field) · `merge <file> <key> <json-list>` (unions list-valued keys — `tags`, edge keys — no read-then-write needed). |
| `page_record.py` | (library only) The single module that reads the frontmatter schema into typed `PageRecord` objects: derives `kind` from folder, `superseded_by` by inverting `supersedes` edges across all pages. Imported by `build_index.py`, `Vault.pages()`, `ingest_scan.py`, `search_index.py`. | No CLI. |
| `place.py` | Computing a new page's vault-relative path from kind and title. Used by `ingest.py`; call directly to preview a planned path. | `place.py <kind> "<title>"`. |
| `commit.py` | Writing one structured git commit per ingestion/edit manifest: stages paths, gates on chain-of-evidence, returns the SHA. Called by `ingest.py`; call directly for hand-assembled commits. | `commit.py --manifest <path>`. |
| `chain_of_evidence.py` | (library only) Enforcing the page → source stub → raw file chain. Imported by `ingest.py` (pre-flight validation) and `commit.py` (commit-time gate). | No CLI. `check(staged, raw)` → list of errors. |
| `build_index.py` | Regenerating `wiki/_index.md` from every page's frontmatter as a GFM table. Called by `init_wiki.py`, `ingest.py`, and directly on demand. | `build_index.py` (no arguments). |
