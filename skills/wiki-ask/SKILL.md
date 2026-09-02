---
name: wiki-ask
description: Turn a question into a grounded, cited answer over the wiki vault — query-expanded, BM25-ranked, frontmatter-first, and budget-bounded, with each citation's age and volatility stated honestly. Invoke whenever the vault should be asked something. Runs on the bundled node scripts in this skill's scripts/ directory; no MCP tools, no network.
compatibility: requires node on PATH; the vault is a git repository reachable on local disk
metadata:
  author: dhague
  license: Apache-2.0
---

# Wiki Ask (host-neutral)

Reads `wiki-conventions` for anything this procedure doesn't cover — folder structure, frontmatter schema, link format, typed-edge vocabulary. That skill is the contract: ingestion writes it, this reads from it. (On a host where `wiki-conventions` is a sibling installed skill, consult it the same way; where only this skill is installed, the vocabulary it needs is condensed inline in [Wiki-conventions contract (condensed)](#wiki-conventions-contract-condensed).)

Retrieval **never modifies an existing page** — no edit, no move, no delete, ever. One write: new `synthesis/` page, only on explicit user confirmation ([Saving an answer as a synthesis page](#saving-an-answer-as-a-synthesis-page)).

## Invocation

You hold the conversation and execute the procedure yourself. Every script below is the bundled `enchiridion.cjs` entry point inside this skill's `scripts/` directory, invoked **directly** as:

```bash
node <scripts>/enchiridion.cjs <subcommand> …
```

where `<scripts>` is this skill's `scripts/` subdirectory (the installed `enchiridion.cjs` + `node-sqlite3-wasm.wasm` supporting files). If `node` is not on PATH, say so plainly and stop; this skill cannot run without it.

Run `node <scripts>/enchiridion.cjs help` for a list of commands, and `node <scripts>/enchiridion.cjs <command> --help` to list the options for each command.

**Vault root.** The scripts resolve the vault root themselves: `$WIKI_ROOT`, else nearest ancestor holding a `wiki/` directory or `.wiki-root` marker, else the working directory. If the user has not yet pointed you at a vault, ask on first use — a one-line prompt, e.g. *"Which folder is your vault?"* — then set `WIKI_ROOT=<dir>` inline on every subsequent invocation.

**Body reads.** The `Read` tool is not available here. Use `read-page <page_ref> --json` to read any vault page body:

```bash
node <scripts>/enchiridion.cjs read-page wiki/concepts/foo.md --json
```

Returns `{page_ref, frontmatter, body}`. `raw/` originals are also readable via `read-page`, but skip them — a `sources/` page already stands in for each one, and `raw/` content is not in the search index.

Search `wiki/**` only.

## Procedure

Given a question:

1. **Expand the query.** Before searching, write **5–8 alternative phrasings** of key terms: synonyms, jargon form, plain-English form, singular/plural, acronym and expansion, verb and noun forms. Single word choice must not decide whether a page is found — vault tags are emergent, so the target page may name the thing differently. Expansions become the term list passed to `enchiridion search` below.

2. **Single search call.** One call does the work — composes BM25 text matching with metadata filters, ranks results, defaults to excluding superseded pages. Pass **only term list** from step 1 as a single space-separated string (it tokenizes and phrase-quotes each term). Use `--json` and read the records:

   ```bash
   node <scripts>/enchiridion.cjs search \
       "<term1> <term2> <term3>" \
       --kind concept \
       --since 2026-07-01 --date-field source_date \
       --limit 20 --json
   ```

   Filter flags worth knowing:
   - `--tag <t>` (repeat for AND) and `--tag-any <t>` (OR). Tags are emergent — leave off unless question is clearly tag-shaped ("all pages tagged `db`").
   - `--kind concept|entity|source|synthesis` (comma-separated). Use when kind is obvious ("who is X" → `--kind entity`).
   - `--since` / `--until` against `--date-field source_date` (valid time, default) or `git_date` (transaction time). Use `git_date` for "updated this week" / "since last ingestion"; use `source_date` for "knowledge from before X" / "2023 view of Y".
   - `--include-superseded` only when discussing history, not answering "what is current". Default excludes superseded.
   - `--raw` is escape hatch for callers who need FTS5 operators (`NEAR`, `OR`, prefix `*`). Don't reach for it without specific reason.

3. **Expand frontier, frontmatter-first.** Hits are candidates, not answers. Judge each by **`summary`** field first — discard ones that don't bear on the question. **Only a candidate surviving summary judgment earns a full `read-page` of its body.** Most frontier should die at summary; body read is expensive and never the first move. Where more than one candidate survives the same hop's summary judgment, issue their `read-page` calls together, not serially.

   From each page read, harvest outbound relationships — typed-edge keys and `supersedes` in frontmatter, plus body links to other `wiki/` pages — as **next-hop candidates**; judge those the same way (summary first, body only on survival).

   **Follow typed edge the question implies.** Unambiguous shapes map to one edge and one direction; anything else defaults to "follow all typed edges both directions **except `source`**." Patterns in [Edge-following rules](#edge-following-rules) below. Direction matters: "what does X refine" asks for X's outbound `refines:` list; "what refines X" asks which pages name X under their own `refines:`. Inverting is a silent miss.

   `source` excluded from fallback — belongs to provenance path, not general expansion. Only follow it when the question matches the provenance row below.

4. **Filter frontier for currency.** Superseded page is never an answer — `supersedes` is a *recorded fact*, and recorded fact beats any recency guess. Run `superseded-by` with every candidate's `page_ref` as positional arg (`--json` for machine-readable line per candidate); walks each one's `supersedes` inversions in-process and returns each candidate's *active* page:

   ```bash
   node <scripts>/enchiridion.cjs superseded-by wiki/concepts/a.md wiki/concepts/x.md --json
   ```

   Each result: `{"seed": ..., "active": ..., "chain": [...]}`. Apply directly — **no need to re-derive by hand:**

   - **`active == seed`** — candidate is current; keep it.
   - **`active != seed`** — **drop seed from active set and keep `active` instead**, adding to candidate set if not already there. Seed is history, not the answer, whether replacement was one hop or multi-page chain — script already walked to head.

5. **Stop at budget.** Retrieval is bounded:
   - **max 2 hops** from seed set,
   - **~12 pages read** in full,
   - **stop early when next page adds nothing** — if last page or two contributed no new fact, done regardless of counters.

   If budget hit with question unanswered, say so (what was searched, what to read next) — never silently truncate or blow through it.

6. **Synthesize, with honest temporal framing.** Answer from what was actually read; **cite every claim** with source page (relative link or vault path — reader must be able to open it).

   **Two citation modes — question decides:**
   - **Normal** — common case, every question that isn't provenance-shaped. Cite concept (or entity/synthesis) page itself, with age and `volatility` as below. Don't read `source` stub or raw artifact behind it; concept page stands in for that raw material, reading through it wastes budget the question didn't ask for.
   - **Provenance** — when question matches provenance row in [Edge-following rules](#edge-following-rules). Follow chain to raw artifact (concept page → `source` → stub → `raw_source` → raw file) and cite raw artifact itself, with specific location (line or page number). Frame concept page as lens on source, not citation: *"per [Concept](...), drawing on [raw-file.md](...) line 42…"*. If provenance question lands on page with no `source` edge (pre-ingestion content), degrade gracefully — cite page normally and note no provenance chain exists.

   For each cited page state age (`source_date` = valid time; `git_date` = transaction time) and `volatility` plainly so the asker can calibrate trust. Sanity-check `git_date` before quoting: bulk-imported vault gives every page the same commit date, which says nothing about the knowledge — when commit dates are uninformative, frame on `source_date` and say so.

   Phrase it in the answer, don't bury it — e.g. *"per [Rate limits](wiki/concepts/rate-limits.md), marked `volatile`, from 2025-01-12 and last committed 14 months ago…"*. A `stable` page from three years ago is not stale; a `volatile` page from last quarter may already be wrong. Never present `volatile` fact with same confidence as `stable` just because it's what was found.

   **Recency is never a re-ranking signal.** When question matches P₁ (older, `stable`) and P₂ (newer, `volatile`), don't promote P₂ above P₁ — present both with volatilities and let asker judge. Exception: explicitly time-anchored question ("what's the latest X", "as of YYYY-MM-DD"), where recency IS the signal; even then, `supersedes` edge wins — recorded fact beats any recency guess.

   **Never present superseded page as current** — step 4 already filtered them; framing here keeps the *answer* consistent. When user asked about replaced page, frame on *current* page ("per [Current]…") and mention older only as what it replaced ("replaces [Old], which said…"). When user asks about chain itself ("what did we use before X?"), say so explicitly: *"per [A] (now superseded by [C] via [B])…"*.

   Where pages genuinely conflict without one superseding the other, say so and show both — resolving live contradiction is asker's call.

7. **Report.** Reply with answer and citations, plus one short line on what was searched (expansions used, pages read, hops taken) so the asker can see if search missed their framing. Never dump page bodies into answer.

8. **Offer to save when answer worth keeping.** Judge against both bars — *both* must hold or say nothing:

   - **Durable** — not a one-off lookup expiring with session, and not a single page's content restated (if one page answered the question, cite it; synthesis duplicating it is vault noise).
   - **Reusable** — drew several pages into something next asker would otherwise re-derive, and still true next month.

   When both hold, offer in one line after relaying the answer, naming what would be written:

   > *Worth saving as `wiki/synthesis/how-connection-pooling-is-configured.md`, sourced from the 3 pages it cites? (y/n)*

   Then wait. **Gate:** vault not written unless user says yes to the question you actually asked. Silence isn't yes; "sounds useful" isn't yes. If unsure whether told to save — you were not.

   On anything but explicit yes, stop. No page, no plan file, no commit. Acknowledge in few words and move on; don't re-offer later in the same session.

   On explicit yes, see [Saving an answer as a synthesis page](#saving-an-answer-as-a-synthesis-page).

## Edge-following rules

When question's shape implies specific typed edge, follow *that* edge in implied direction. Table covers unambiguous cases. Anything not on it defaults to "follow all typed edges both directions" — broader `related` graph is fallback.

| Question pattern | Edge | Direction | What to follow |
|---|---|---|---|
| "what does X refine" / "X refines" | `refines` | outbound from X | X's `refines:` list |
| "what refines X" / "refined by" | `refines` | inbound to X | pages whose `refines:` lists X |
| "what superseded X" / "replaces X" / "is X still current" | `supersedes` | inbound to X | pages whose `supersedes:` lists X |
| "what does X supersede" | `supersedes` | outbound from X | X's `supersedes:` list |
| "what contradicts X" / "is X contradicted" | `contradicts` | inbound to X | pages whose `contradicts:` lists X |
| "examples of X" / "what is an example of X" | `example-of` | inbound to X | pages whose `example-of:` lists X |
| "what is X an example of" | `example-of` | outbound from X | X's `example-of:` list |
| "what did X draw on" / "what sources X" | `source` | outbound from X | X's `source:` list |
| "where is X used" / "what uses X" | `source` | inbound to X | pages whose `source:` lists X |
| "related to X" (default) | `related` | both | X's `related:` list + pages whose `related:` lists X |
| "what's the evidence for X" / "source for X" / "raw data behind X" / "provenance of X" / "where did this come from" / "cite the original" / "back it up" | `source` | outbound from X | X's `source:` list → stub page → its `raw_source:` → raw artifact, with location (line or page number) |

Two directions are NOT symmetric: outbound follows links that *leave* a page; inbound finds pages that *point at* a page. Inverting is a silent miss.

## Saving an answer as a synthesis page

**Gate:** vault not written unless user says yes (step 8 above).

On explicit yes, write the IngestPlan and run it:

```jsonc
{
  "title": "<the answer title>",
  "action": "synthesize",
  "source_date": "<today>",
  "pages": [
    {
      "op": "create",
      "kind": "synthesis",
      "title": "<the answer title>",
      "body": "<the answer, in full markdown, written as a page — no 'you asked', no search-trajectory line, no 'per the vault'; citations as inline relative links; temporal framing preserved>",
      "frontmatter": {
        "summary": "<one line, ≤ ~20 words — what next retrieval judges this page by>",
        "tags": ["<tags from answer>"],
        "source_date": "<today>",
        "volatility": "<most volatile of cited pages — synthesis only as durable as shakiest input>"
      },
      "edges": {
        "source": ["wiki/concepts/db-connection-pooling.md"]  // one per cited page — vault-relative paths; enchiridion ingest composes the actual link
      }
    }
  ]
}
```

Feed the finished plan over **stdin**:

```bash
node <scripts>/enchiridion.cjs ingest --plan -
```

Validates whole plan before touching disk, then writes page and makes one structured commit — printing SHA. Report path and SHA in one line. If it raises, nothing was committed; fix plan and rerun (writes are idempotent).

If title collides with existing `synthesis/` page, validation fails with *create target … already exists*. Don't work around by renaming to near-duplicate — existing page is either the answer already (cite it instead) or genuinely superseded (an ingestion decision, not retrieval).

No `raw` field and no `raw_source` — synthesis page has no raw artifact; stands on `source` edges to other pages.

## Wiki-conventions contract (condensed)

When a separate `wiki-conventions` skill isn't installed, this is the vocabulary the procedure reads from.

### Frontmatter schema (key fields)

```yaml
---
title: <human title>
summary: <one line, ≤ ~20 words>         # THE field retrieval reads first
tags: [<emergent — reuse existing, mint new where needed>]
source_date: <YYYY-MM-DD>                # when the knowledge is FROM (valid time)
raw_source: "[<filename>](<encoded relative/path into raw/>)"   # sources/ pages only
volatility: stable | evolving | volatile
supersedes:
  - "[<title>](<relative/path.md>)"
refines:
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

- **`summary`** — one line, ≤ ~20 words. The single most important field for retrieval.
- **`source_date`** — valid time: when the knowledge is *from* (not today). `YYYY-MM-DD`.
- **`volatility`** — `stable` | `evolving` | `volatile`. Authored, not inferred.
- **`supersedes`** — pages this page replaces. Recorded fact, stronger than any recency guess. Never author a `superseded_by` key — the inverse is derived by inverting every page's `supersedes` edges.
- **Typed-edge keys** — `refines`, `contradicts`, `example-of`, `source`, `related`. Each an optional list of markdown links to target pages; see table in [Edge-following rules](#edge-following-rules) above.

### Typed edges

| Type | Reads as | Use when |
|---|---|---|
| `refines` | this page refines the target | Sharpens / extends / adds precision to the target's idea |
| `contradicts` | this page contradicts the target | A claim conflicts with the target's |
| `example-of` | this page is an example of the target | A concrete instance / case study of the general concept |
| `source` | this page is sourced from the target | Page draws content from target page (mandatory chain-of-evidence back-edge) |
| `related` | associatively related to the target | Real connection that is none of the above — the fallback |

`supersedes` (a page this page replaces) is a **recorded fact**, distinct from the typed edges; its inverse (`superseded_by`) is derived by inversion, never authored directly.
