---
name: wiki-conventions
description: The single source of truth for the wiki vault — folder structure, frontmatter schema, relative-markdown link rules, and the typed-edge vocabulary. Preloaded by both the ingestion and retrieval agents; it is the contract between them. Consult before creating, moving, linking, or reading any wiki page.
---

# Wiki conventions

Shared contract between ingestion (`wiki-ingest`, Sonnet) and retrieval (`wiki-researcher`, Haiku). Both agents preload it. Ingestion writes to these rules; retrieval reads assuming them. If the two disagree about where a page lives, what a field means, or what an edge type asserts, this file wins — change here, once, both sides move.

Two halves: **fixed half** — folder structure, schema shape, link format, edge vocabulary — never varies per vault. **Emergent half** — `tags` — generated at ingestion, not enumerated here (see [Tags](#tags)).

## Terminology

Wiki units are **pages**, not "notes". A page is one markdown file under `wiki/`, carrying frontmatter + body. Keeps `raw/notes/` (a raw-capture type) unambiguous.

## Vault structure

Vault is a **git repository**. Layout is opinionated and **plugin-fixed** — same in every vault:

```
<vault root>/
├── wiki/                     ← pages; the vault marker
│   ├── concepts/             ← an idea / technique / pattern / principle / how-it-works (the default; kind value `concept`)
│   ├── entities/             ← a named thing linked repeatedly (person / team / product / tool / service / project / org; kind value `entity`)
│   ├── sources/              ← a stand-in for a raw artifact; one per ingested raw file, REQUIRES `raw_source:` → ../../raw/… (kind value `source`)
│   └── synthesis/            ← a saved query result; links to its inputs via `source`-type edges (kind value `synthesis`, folder unchanged)
└── raw/                      ← immutable originals, git-tracked, sibling of wiki/
    └── <user-extensible>/    ← emails/ meetings/ notes/ clippings/ documents/ … an OPEN set
```

- Four **kind-folders** under `wiki/` are the fixed set. **Kind** is the only axis both decidable from a page's content *and* domain-independent. Domain- or topic-axed trees fail decidability and are not used. (See [Naming](#naming) for folder-vs-value convention.)
- **Multi-membership never spawns a second folder.** Page touching several subjects filed once, by primary function; every other facet rides on **tags + typed edges**. Folder tree is only a thin, decidable filing handle.
- `raw/` is **sibling** of `wiki/`, not child — immutable-originals-vs-generated split. Search index walks `wiki/**` only; never lists `raw/`.
- `raw/` is **inbox** scanned by deterministic script; subfolders are **user-extensible** — no mandated catch-all.

### Placement algorithm

**Top-to-bottom, first match wins** — placement is deterministic. Kinds split into *origin-defined* (`source`, `synthesis`) and *subject-defined* (`entity`, `concept`):

1. Stand-in for an ingested raw artifact? → **`sources/`** (must carry `raw_source:` field → its `raw/` file).
2. Saved query result synthesized from other pages? → **`synthesis/`**.
3. Primarily a named thing linked repeatedly? → **`entities/`**.
4. Otherwise → **`concepts/`** (default).

### The chain of evidence

**Every raw file a pass produces pages from gets a `sources/` stand-in, and every page produced carries a `source` edge back to it.** Reader can always walk *page → `sources/` stub → `raw/` artifact* — the one path that makes a citation checkable.

- **No exemption for distillation.** When raw file's value lands in `concepts/`/`entities/` pages, stub still created — just a **thin stub**: `title`, one-paragraph `summary`, required `raw_source` link. Its job is to be the addressable link target.
- **`source` back-edge is not judgment.** Unlike `refines`/`contradicts`/`example-of`/`related` (weighed per page), this edge is mandatory on every page of the pass — each page of a multi-chunk split, and a page **updated in place** as much as newly created.
- **Enforced, not merely conventional.** `enchiridion ingest` validates both halves before writing: plan naming a `raw` artifact must place a `sources/` page whose `raw_source` resolves to it, and every other page in that plan must carry a `source` edge to that stub. Plan that doesn't is rejected.
- **Raw file ingestion declines outright** — spam, exact duplicate, junk — produces no pages; rule doesn't apply.

**Decidability bar:** given only title + summary, correct folder is same every time, no tie-break needed. If two folders are ever a genuine toss-up, the axis is wrong — fix is to merge them and push distinction to tags.

**Subject tie-break:** page plausibly *about* two subjects filed by primary function; other subject becomes tag or typed edge.

### The `raw/` layer

`raw/` holds **content-immutable** originals. Ingestion **never edits a raw file's contents**. Links into `raw/` are percent-encoded (see [Links](#links)) so any filename is linkable. See [Naming](#naming) for filename and prefix rules.

### Naming

- **Kind-folders pluralize** (`concepts/`, `entities/`, `sources/`; `synthesis/` unchanged) — **kind values stay singular** (`concept`, `entity`, `source`, `synthesis`).
- **Page filenames** — lowercase **kebab-slug of the title, no date prefix** — `concepts/prepared-statements.md`. Git carries ingestion date; `source_date` carries valid-time; a filename date would be a third, drifting clock.
- **Raw filenames** preserve external identity unchanged. Plugin-authored raw files carry `YYYY-MM-DD-hhmm-` prefix at creation. External raw files renamed outside tool are repaired by deferred linter; core build never renames existing raw file.

## Frontmatter schema

Every page opens with YAML frontmatter block. **Only fields requiring judgment live here** — anything git can tell us is derived on demand (see [Derived from git](#derived-from-git)).

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

- **`title`** — human-readable name; filename is its kebab-slug.
- **`summary`** — single most important field. Retrieval judges a candidate page by its `summary` before reading body; search index matches it. One line, ≤ ~20 words, written well at ingestion.
- **`tags`** — emergent, not controlled. See [Tags](#tags).
- **`source_date`** — **valid time**: when the knowledge is *from* (document's own date, meeting's date). Judgment git cannot reconstruct; what temporal queries key off. Distinct from commit date.
- **`raw_source`** — **single markdown link into `raw/`** (title = artifact's literal filename, destination = percent-encoded path), **required on `sources/` pages, omitted on every other kind**. Points at the immutable artifact this page stands in for. Distinct from `source`-type *edge* (see [Typed edges](#typed-edges)): this field points into `raw/`; edge points at another `wiki/` page. Split onto different keys (`raw_source:` vs `source:`) so nothing has to guess which is meant. Example: `"[my file.txt](../../raw/notes/my%20file.txt)"`.
- **`volatility`** — `stable` | `evolving` | `volatile`. Drives conditional decay at retrieval: `stable` facts don't age out, `volatile` ones flagged as possibly current-only. Authored, not inferred.
- **`supersedes`** — optional list of markdown links to pages this page replaces. **Recorded fact**, stronger than any "newer wins" guess. On contradiction, ingestion **appends new page and records `supersedes`; does not overwrite** the old one.
- **typed-edge keys** (`refines`, `contradicts`, `example-of`, `source`, `related`) — each optional list of markdown links to target pages; see [Typed edges](#typed-edges).

### Derived from git

**Deliberately absent from schema:** `updated_at` and `ingested_at`. Git's commit history is authoritative record of when a page was first added and last touched — hand-maintained timestamp an agent forgets to bump is worse than none. Derive from `git log` on demand.

Clean **bitemporal** model: `source_date` is **valid time** (authored), git commit date is **transaction time** (derived). Never add frontmatter field for anything git already knows.

## Tags

Tags are **emergent half** of contract — generated at ingestion, not conformed to a fixed list. **Reuse existing tag** where one fits; **mint new one** only where nothing fits. `enchiridion discover --plan` returns vault's tag vocabulary alongside every candidate — reuse-first is something the discovery call gives you. Prefer `--tags-containing`/`--tag-count`, derived from the draft's own candidate tags, over the full vocabulary dump — see the `discover` catalogue entry. No controlled vocabulary, no lint rule rejecting "off-vocabulary" tags; consistency comes from reuse-first discipline, not a closed set.

## Links

Links between pages are **relative markdown links — not wikilinks.**

- **Standard link:** `[prepared statements](../concepts/prepared-statements.md)`. Path relative to linking file's location; `entities/` to `concepts/` climbs one level (`../concepts/…`).
- **Anchors:** append heading fragment — `[the budget rule](../wiki-retrieval/SKILL.md#termination-budget)` / `[…](../concepts/caching.md#ttl)`. Fragment is GitHub-style slug of target heading.
- **Image embeds:** leading-bang form — `![cache diagram](../raw/diagrams/2026-03-01-cache.png)`. Embeds may point into `raw/`; ordinary links between pages stay within `wiki/`.

All links **position-spliced** on move/rename by `enchiridion vault move` (both inbound links across vault and outbound links inside moved page). Links into `raw/` are **percent-encoded**: encode space, `#`, `%`, `(`, `)`, `<`, `>`; everything else (unicode, `&`, `'`, `,`, `+`) stays literal. Obsidian cannot follow destination containing literal space, so encoding is essential for interoperability.

**Frontmatter relationships use the same link form.** `raw_source` field, `supersedes` key, and every typed-edge key hold `[title](relative/path.md)` markdown, always **quoted** (`"[…](…)"`) so YAML doesn't parse leading `[` as flow sequence. `raw_source` holds **single** link; `supersedes` and typed-edge keys hold **list** (`- "[…](…)"`). Real markdown links keep every relationship clickable in plain markdown viewers and in Obsidian's Properties panel, and lets a move rewrite frontmatter and body links by same rule.

## Typed edges

Typed edges are **highest-leverage output of ingestion** — retrieval cannot recover an edge type never recorded. **Each edge type is its own frontmatter key**, holding list of markdown links to target pages:

```yaml
refines:
  - "[Prepared statements](../concepts/prepared-statements.md)"
source:
  - "[Deploy runbook](../sources/deploy-github-actions.md)"
```

Edge is **directional** — reads *this page* → *key* → *target*. Include only keys that have edges; omit rest.

| Type | Reads as | Use when |
|---|---|---|
| **`refines`** | *this page refines the target* | Sharpens, extends, or adds precision to target's idea. Target is broader/earlier statement; this page is finer. |
| **`contradicts`** | *this page contradicts the target* | Claim conflicts with target's. Record edge even before conflict is resolved; when resolved by replacement, also set `supersedes`. |
| **`example-of`** | *this page is an example of the target* | Concrete instance / case study of general concept target describes. |
| **`source`** | *this page is sourced from the target* | Page draws content from target **page**. Two uses: `synthesis/` page lists under `source:` each `wiki/` page it was synthesized from, and — **mandatorily**, see [The chain of evidence](#the-chain-of-evidence) — every page an ingestion produces points at that raw file's `sources/` stub. Distinct from `raw_source:` field — see [field notes](#frontmatter-schema). |
| **`related`** | *this page is associatively related to the target* | Real connection that is none of the above. Catch-all — prefer sharper type whenever one fits; retrieval can follow specific type purposefully but can only wander a `related` one. |

**Ingestion guidance:** assign most specific type that is true; reach for `related` only when no sharper type applies. Exception to "judge per page" is mandatory `source` back-edge. Under-assigning edges is silent quality loss — graph only as navigable as edges recorded. **Retrieval guidance:** follow edges the question implies (a "how does X work in practice" follows `example-of`; "is this still true" follows `contradicts`/`supersedes`), within stated hop budget.

## Scripts

Subcommands touching the vault resolve its root themselves (`$WIKI_ROOT`, else nearest ancestor holding `wiki/` directory or `.wiki-root` marker, else cwd). Set `WIKI_ROOT` before invoking any. `page` and `place` exceptions: operate only on what you hand them, no root resolved.

**Everything is one Go binary** ([ADR-0011](../../../docs/adr/0011-go-rewrite-scope-sequencing-toolchain.md)) — no Python anywhere in this layer, nothing to install. Invoke via `<plugin-root>/bin/enchiridion <subcommand>`, where `<plugin-root>` is located per host: on Claude Code, `${CLAUDE_PLUGIN_ROOT}` (substituted before you read this); on OpenCode, the `plugin_root` value in `.opencode/wiki-knowledge/config.json` (written by install). That wrapper lazy-fetches the platform binary on first use. Works identically in dedicated mode or query-from-anywhere mode.

### Script catalogue

| Script | Call it for | Usage |
|---|---|---|
| `enchiridion discover` | Discovery before ingesting — BM25 overlap classification for every page a draft plan proposes, plus vault's tag vocabulary. Call during ingestion step 3, before writing real `IngestPlan`. **Go binary, not Python.** | `bin/enchiridion discover --plan <draft-plan.json> [--limit N] [--duplicate-threshold F] [--related-threshold F]` → `{"pages": [{"title", "candidates": [{page_ref, title, score, hint, summary, tags, volatility, superseded_by}]}], "vocabulary": [{"tag", "count"}]}`. Single-page mode (`--title`/`--summary`/`--body-file`) for ad-hoc checks. `--tags-containing "a,b,c"` (case-insensitive substring OR match) and/or `--tag-count "x,y"` (exact match, 0 if the tag doesn't exist yet) each replace the `vocabulary` JSON with a plain-text filtered form — `['tag1', 'tag2']` for the former, one `tag count: N` line per tag for the latter — the `pages` payload is unchanged either way. Pass both, derived from the draft's own candidate tags, instead of the full vocabulary dump. |
| `enchiridion search` | Any vault search. **Go binary, not Python.** | `bin/enchiridion search "<terms>" [--tag T] [--tag-any T] [--kind K] [--since DATE] [--until DATE] [--date-field FIELD] [--volatility V] [--limit N] [--include-superseded] [--raw] [--json]`. Also `--reindex [--full]` and `--status`. |
| `enchiridion superseded-by` | Filtering retrieval candidate set for currency — resolving each candidate to its current page. Call during retrieval step 4, once frontier is expanded. **Go binary, not Python.** | `bin/enchiridion superseded-by <page_ref> [<page_ref> ...] [--json]` → one `{seed, active, chain}` per candidate; `active == seed` means current. |
| `enchiridion ingest` | Executing an `IngestPlan` — resolve, validate, write, commit — after agent assembles plan. **Go binary, not Python.** | `bin/enchiridion ingest --plan <path>`; add `--dry-run` to validate and print without writing. `bin/enchiridion ingest --ignore <raw_rel> [--ignore-comment <text>]` appends to folder's `.ingestignore`. |
| `enchiridion ingest-scan` | Sweeping `raw/` for files needing ingestion (never-ingested, changed-since-ingestion). **Go binary, not Python.** | `bin/enchiridion ingest-scan [folder] --json`. |
| `enchiridion watch` | Long-running filesystem watcher over `raw/` with per-file debounce, exclusive lock, queue file — launched by `/wiki-watch`. **Go binary, not Python.** | `bin/enchiridion watch [--vault ROOT] [--debounce SECONDS] [--poll-interval SECONDS]`. `bin/enchiridion watch [--vault ROOT] --dequeue <raw_rel>` removes one queue entry. |
| `enchiridion vault` | Resolving vault root, or moving a page (rewrites all inbound links across vault and outbound links inside moved page). **Go binary, not Python.** | Bare `bin/enchiridion vault` (or `bin/enchiridion vault root`) prints resolved root. `bin/enchiridion vault move <old-page-ref> <new-page-ref>` prints each changed page ref, one per line. |
| `enchiridion init` | Scaffolding new empty vault: folders, `.gitignore`, git init, optional `settings.json`. **Go binary, not Python.** | `bin/enchiridion init <path> --mode {query-from-anywhere|dedicated} [--plugin-root DIR]`. |
| `enchiridion save-session` | Capturing current session's transcript as raw file in vault. Called from `/save-conversation`. **Go binary, not Python.** | `bin/enchiridion save-session [--slug "<phrase>"]`. |
| `enchiridion page` | Frontmatter edits during ingestion. Takes a **file path**, resolves no vault root. **Go binary, not Python.** | `bin/enchiridion page get <file> <key>` (exits non-zero when key absent) · `page set <file> <key> <value> [--json]` · `page merge <file> <key> <json-list>` (unions list-valued keys — `tags`, edge keys). |
| `internal/pagerecord` | (library only) Reads frontmatter schema into typed `PageRecord` values: derives `kind` from folder, `superseded_by` by inverting `supersedes` edges. | No CLI. |
| `enchiridion place` | Computing new page's vault-relative path from kind and title. Takes only what you hand it, resolves no vault root. **Go binary, not Python.** | `bin/enchiridion place <kind> "<title>"`. |
| `enchiridion commit` | Writing one structured git commit per ingestion/edit manifest: stages paths, gates on chain-of-evidence, returns SHA. Only for a hand-built manifest — `enchiridion ingest` commits its own plan. **Go binary, not Python.** | `bin/enchiridion commit --manifest <path>`. |
| `internal/chainofevidence` | (library only) Enforcing page → source stub → raw file chain. No CLI on either side; `enchiridion ingest` runs it as pre-flight *and* commit-time gate. | No CLI. `Check(staged, raw)` → list of errors. |
