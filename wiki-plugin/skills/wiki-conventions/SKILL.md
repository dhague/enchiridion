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
│   ├── concept/              ← an idea / technique / pattern / principle / how-it-works (the default)
│   ├── entity/               ← a named thing linked repeatedly (person / team / product / tool / service / project / org)
│   ├── source/               ← a stand-in for a raw artifact; REQUIRES `source:` → ../../raw/…
│   └── synthesis/            ← a saved query result; links to its inputs via `source`-type edges
└── raw/                      ← immutable originals, git-tracked, sibling of wiki/
    └── <user-extensible>/    ← emails/ meetings/ notes/ clippings/ documents/ … an OPEN set
```

- The four **kind-folders** under `wiki/` are the fixed set. **Kind** is the only axis both decidable from a page's content *and* domain-independent — the two properties a plugin-fixed structure requires. Domain- or topic-axed trees fail decidability (a page fits two sibling folders equally) and are not used.
- **Multi-membership never spawns a second folder.** A page that touches several subjects is filed once, by primary function; every other facet rides on **tags + typed edges**. The `_index.md` of summaries and the typed-edge graph are the real retrieval surface — the folder tree is only a thin, decidable filing handle.
- `raw/` is a **sibling** of `wiki/`, not a child — the immutable-originals-vs-generated split. `_index.md` indexes `wiki/**` only; it never lists `raw/`.
- `raw/` is an **inbox** that a deterministic script scans for new files, so its subfolders are **user-extensible** — the five above are typical defaults, not a closed set, and there is no mandated catch-all.

### Placement algorithm

Ingestion runs this **top-to-bottom, first match wins**, so placement is deterministic. The kinds split into *origin-defined* (`source`, `synthesis` — where the page came from, mutually exclusive) and *subject-defined* (`entity`, `concept` — what it's about):

1. Is it a stand-in for an ingested raw artifact? → **`source/`** (must carry a `source:` field → its `raw/` file).
2. Is it a saved query result synthesized from other pages? → **`synthesis/`**.
3. Is it primarily a named thing linked repeatedly? → **`entity/`**.
4. Otherwise → **`concept/`** (the default).

**Decidability bar:** given only a page's title + summary, the correct folder is the same every time, with no tie-break needed. If two folders are ever a genuine toss-up, the axis is wrong — the fix is to merge them and push the distinction to tags, never to add a folder.

**Subject tie-break:** a page plausibly *about* two subjects is filed by its primary function; the other subject becomes a tag or a typed edge.

### The `raw/` layer

`raw/` holds **content-immutable** originals. Ingestion **never edits a raw file's contents**. It may **rename** one to normalize the filename (see [Naming](#naming)) via `normalize_raw.py`, which drives `wikipage.py` so any `source:` pointer follows the rename. (Repairing `source:` links after an *external* rename is deferred linter work — out of scope for the core build.)

### Naming

- **Kind-folders are singular** (`concept/`, not `concepts/`); **raw sub-folders are plural** (`emails/`, `meetings/`) and user-extensible.
- **Page filenames** are the lowercase **kebab-slug of the title, with no date prefix** — `concept/prepared-statements.md`. Git carries the ingestion date and `source_date` carries the valid-time; a filename date would be a third, drifting clock.
- **Raw filenames** are normalized to a `YYYY-MM-DD-hhmm-…` prefix with spaces→underscores. Raw files keep a datetime prefix precisely because they are artifact-anchored, not subject-anchored.

## Frontmatter schema

Every page opens with a YAML frontmatter block. **Only fields that require judgment live here** — anything git can tell us is derived on demand, never authored (see [Derived from git](#derived-from-git)).

```yaml
---
title: <human title>
summary: <one line, ≤ ~20 words>        # THE field retrieval reads first; write it well at ingestion
tags: [<emergent — reuse existing tags where sensible, mint new where needed; NOT a controlled vocabulary>]
source_date: <YYYY-MM-DD>               # when the knowledge is FROM (valid time) — judgment, not recoverable from git
raw_source: "[<filename>](<relative/path into raw/>)"   # REQUIRED on source/ pages; a single link (title = filename) to the ingested artifact. Omit on other kinds.
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
- **`raw_source`** — a **single markdown link into `raw/`** (use the artifact's filename as the link title), **required on `source/` pages and omitted on every other kind**. It points at the immutable artifact this page stands in for — one link, not a list, since a `source/` page stands in for exactly one artifact. Distinct from the `source`-type *edge* (see [Typed edges](#typed-edges)): this field points into `raw/`; the edge points at another `wiki/` page. The two were split onto different keys (`raw_source:` vs `source:`) precisely so nothing has to guess which is meant.
- **`volatility`** — `stable` | `evolving` | `volatile`. Drives conditional decay at retrieval: `stable` facts do not age out, `volatile` ones are flagged as possibly current-only. A blanket recency prior is wrong on exactly the facts that were `stable`, which is why this is authored, not inferred.
- **`supersedes`** — optional list of markdown links to the pages this page replaces. A **recorded fact**, stronger than any "newer wins" guess: retrieval prefers a `supersedes` relationship over recency. On a contradiction, ingestion **appends a new page and records `supersedes`; it does not overwrite** the old one.
- **typed-edge keys** (`refines`, `contradicts`, `example-of`, `source`, `related`) — each an optional list of markdown links to the target pages; see [Typed edges](#typed-edges).

### Derived from git

**Deliberately absent from the schema:** `updated_at` and `ingested_at`. Git's commit history is the authoritative, trust-free record of when a page was first added and last touched — a hand-maintained timestamp an agent forgets to bump is worse than none. Derive both from `git log` on demand.

This gives a clean **bitemporal** model with no field able to lie: `source_date` is **valid time** (authored), the git commit date is **transaction time** (derived). Never add a frontmatter field for anything git already knows.

## Tags

Tags are the **emergent half** of the contract — generated at ingestion, not conformed to a fixed list. When ingesting, **reuse an existing tag** where one fits (grep the index / existing pages first) and **mint a new one** only where nothing fits. There is no controlled vocabulary to enumerate here and no lint rule rejecting "off-vocabulary" tags; consistency comes from reuse-first discipline, not from a closed set. This is why the folder structure is spelled out above and the tag set is not.

## Links

Links between pages are **relative markdown links — not wikilinks.**

- **Standard link:** `[prepared statements](../concept/prepared-statements.md)`. The path is relative to the linking file's own location, so a link from `entity/` to `concept/` climbs one level (`../concept/…`).
- **Anchors:** append a heading fragment — `[the budget rule](../wiki-retrieval/SKILL.md#termination-budget)` / `[…](../concept/caching.md#ttl)`. The fragment is the GitHub-style slug of the target heading.
- **Image embeds:** the leading-bang form — `![cache diagram](../raw/diagrams/2026-03-01-cache.png)`. Embeds may point into `raw/` (e.g. an extracted figure); ordinary links between pages stay within `wiki/`.

All links are **position-spliced** on move/rename by `wikipage.py` (both inbound links across the vault and outbound links inside a moved page), so a page can be re-filed without hand-editing references. Keep links as plain relative paths; do not URL-encode or absolutize them.

**Frontmatter relationships use the same link form.** The `raw_source` field, the `supersedes` key, and every typed-edge key hold this identical `[title](relative/path.md)` markdown, always **quoted** (`"[…](…)"`) so YAML doesn't parse the leading `[` as a flow sequence. `raw_source` holds a **single** such link; `supersedes` and the typed-edge keys hold a **list** (one link per item, `- "[…](…)"`). Writing them as real markdown links keeps every relationship clickable in plain markdown viewers and in Obsidian's Properties panel with no loss of semantics, and lets `wikipage.py` rewrite frontmatter and body links by the same rule.

## Typed edges

Typed edges are the **highest-leverage output of ingestion** — retrieval cannot recover an edge type that was never recorded. **Each edge type is its own frontmatter key**, holding a list of markdown links to the target pages:

```yaml
refines:
  - "[Prepared statements](../concept/prepared-statements.md)"
source:
  - "[Deploy runbook](../source/deploy-github-actions.md)"
```

The edge is **directional** — it reads *this page* → *key* → *target*. Include only the keys that have edges; omit the rest.

| Type | Reads as | Use when |
|---|---|---|
| **`refines`** | *this page refines the target* | This page sharpens, extends, or adds precision to the target's idea. The target is the broader/earlier statement; this page is the finer one. |
| **`contradicts`** | *this page contradicts the target* | This page's claim conflicts with the target's. Record the edge even before the conflict is resolved; when it *is* resolved by replacement, also set `supersedes`. |
| **`example-of`** | *this page is an example of the target* | This page is a concrete instance / case study of the general concept the target describes. |
| **`source`** | *this page is sourced from the target* | This page draws its content from the target **page**. The canonical use: a `synthesis/` page lists under `source:` each `wiki/` page it was synthesized from. **Not** the same as the `raw_source:` frontmatter field, which points a `source/` page into `raw/`. |
| **`related`** | *this page is associatively related to the target* | A real connection that is none of the above. The catch-all — prefer a sharper type whenever one fits, because retrieval can follow a specific type purposefully and can only wander a `related` one. |

**Guidance for ingestion:** assign the most specific type that is true; reach for `related` only when no sharper type applies. Under-assigning edges is a silent quality loss — the graph is only as navigable as the edges recorded. **Guidance for retrieval:** follow the edges the question implies (a "how does X work in practice" question follows `example-of`; a "is this still true" question follows `contradicts`/`supersedes`), within the stated hop budget.
