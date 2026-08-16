---
name: wiki-retrieval
description: Turn a question into a grounded, cited answer over the wiki vault — query-expanded, BM25-ranked, frontmatter-first, and budget-bounded, with each citation's age and volatility stated honestly. Invoke via /wiki-retrieval <question>, or whenever the vault should be asked something.
---
# Wiki Retrieval

Reads `wiki-conventions` for anything this procedure doesn't cover — folder structure, frontmatter schema, link format, typed-edge vocabulary. That skill is the contract: ingestion writes it, this reads from it. File preloaded into `wiki-researcher` agent context at startup, and what `/wiki-retrieval <question>` loads when invoked directly.

Retrieval **never modifies an existing page** — no edit, no move, no delete, ever. One write: new `synthesis/` page, only on explicit user confirmation ([Saving an answer as a synthesis page](#saving-an-answer-as-a-synthesis-page)).

## Invocation

- **If not already running as `wiki-researcher` agent** (system prompt doesn't identify you as it — e.g. invoked directly via `/wiki-retrieval <question>`): only action is delegate. Call `Task` with `subagent_type: "wiki-researcher"` and prompt containing the question, then relay the answer. Keeps reading and link-following inside subagent's context — on its Haiku model — regardless of invoking session's model.

  If returned answer carries `save-candidate` block, you also **put the offer to user and perform save on yes** — see [Saving an answer as a synthesis page](#saving-an-answer-as-a-synthesis-page). You hold the conversation; confirmation can only happen here.
- **If you are `wiki-researcher` agent**: continue directly with procedure below using own tools. **Recommend** save (step 8); never perform one — subagent can't ask user, and unconfirmed save is the exact failure this design prevents.

Every script below lives in plugin's install directory and resolves vault root itself — see `## Scripts` section of `wiki-conventions` for full reference (vault-root resolution, locating the plugin root, common tasks, script catalogue).

Search `wiki/**` only. `raw/` holds immutable originals a `source/` page already stands in for; reading it duplicates content you have summaries for, and it's not in the index.

## Procedure

Given a question:

1. **Expand the query.** Before searching, write **5–8 alternative phrasings** of key terms: synonyms, jargon form, plain-English form, singular/plural, acronym and expansion, verb and noun forms. Single word choice must not decide whether a page is found — vault tags are emergent, so target page may name the thing differently. Expansions become the term list passed to `enchiridion search` below.

2. **Single search call.** One call to `bin/enchiridion search` does the work — composes BM25 text matching with metadata filters, ranks results, defaults to excluding superseded pages. Pass **only term list** from step 1 as single space-separated string (it tokenizes and phrase-quotes each term). Use `--json` and read the records:

   ```bash
   "<plugin-root>/bin/enchiridion" search \
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

   First call to `enchiridion search` triggers `(mtime_ns, size)` staleness scan over `wiki/**` (~50 ms at 2000 pages, measured) so `git pull`, Obsidian edits, and manual changes are caught.

3. **Expand frontier, frontmatter-first.** Hits are candidates, not answers. Judge each by **`summary`** field — that is what `summary` exists for — discard ones that don't bear on question. **Only candidate surviving summary judgment earns full `Read` of its body.** Most frontier should die at summary; body read is expensive and never the first move. Where more than one candidate survives the same hop's summary judgment, issue their `Read` calls together in one message, not serially; each extra turn re-reads full context.

   From each page read, harvest outbound relationships — typed-edge keys and `supersedes` in frontmatter, plus body links to other `wiki/` pages — as **next-hop candidates**; judge those same way (summary first, body only on survival).

   **Follow typed edge the question implies.** Unambiguous shapes map to one edge and one direction; anything else defaults to "follow all typed edges both directions **except `source`**." Patterns in [Edge-following rules](#edge-following-rules) below. Direction matters: "what does X refine" asks for X's outbound `refines:` list; "what refines X" asks which pages name X under their own `refines:`. Inverting is a silent miss.

   `source` excluded from fallback — belongs to provenance path, not general expansion. Only follow it when question matches provenance row below.

4. **Filter frontier for currency.** Superseded page is never an answer — `supersedes` is a *recorded fact* (see [Frontmatter schema](../wiki-conventions/SKILL.md#frontmatter-schema)), and recorded fact beats any recency guess. Run `"<plugin-root>/bin/enchiridion" superseded-by` with every candidate's `page_ref` as positional arg (`--json` for machine-readable line per candidate); walks each one's `supersedes` inversions in-process and returns each candidate's *active* page:

   ```
   "<plugin-root>/bin/enchiridion" superseded-by wiki/concepts/a.md wiki/concepts/x.md --json
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
   - **Provenance** — when question matches provenance row in [Edge-following rules](#edge-following-rules). Follow chain to raw artifact (concept page → `source` → stub → `raw_source` → raw file) and cite raw artifact itself, with specific location (line or page number). Frame concept page as lens on source, not citation: *"per [Concept](...), drawing on [raw-file.md](...) line 42…"*. If provenance question lands on page with no `source` edge (pre-#34 content), degrade gracefully — cite page normally and note no provenance chain exists.

   For each cited page state age (`source_date` = valid time, `git_date` = transaction time — see [Derived from git](../wiki-conventions/SKILL.md#derived-from-git)) and `volatility` (see [Frontmatter schema](../wiki-conventions/SKILL.md#frontmatter-schema)) plainly so asker can calibrate trust. Sanity-check `git_date` before quoting: bulk-imported vault gives every page same commit date, which says nothing about the knowledge — when commit dates are uninformative, frame on `source_date` and say so.

   Phrase it in the answer, don't bury it — e.g. *"per [Rate limits](wiki/concepts/rate-limits.md), marked `volatile`, from 2025-01-12 and last committed 14 months ago…"*. `stable` page from three years ago is not stale; `volatile` page from last quarter may already be wrong. Never present `volatile` fact with same confidence as `stable` just because it's what was found.

   **Recency is never a re-ranking signal.** When question matches P₁ (older, `stable`) and P₂ (newer, `volatile`), don't promote P₂ above P₁ — present both with volatilities and let asker judge. Exception: explicitly time-anchored question ("what's the latest X", "as of YYYY-MM-DD"), where recency IS the signal; even then, `supersedes` edge wins — recorded fact beats any recency guess.

   **Never present superseded page as current** — step 4 already filtered them; framing here keeps the *answer* consistent. When user asked about replaced page, frame on *current* page ("per [Current]…") and mention older only as what it replaced ("replaces [Old], which said…"). When user asks about chain itself ("what did we use before X?"), say so explicitly: *"per [A] (now superseded by [C] via [B])…"*.

   Where pages genuinely conflict without one superseding the other, say so and show both — resolving live contradiction is asker's call.

7. **Report.** Reply with answer and citations, plus one short line on what was searched (expansions used, pages read, hops taken) so asker can see if search missed their framing. Never dump page bodies into answer — that is the reading noise this subagent exists to keep out of main thread.

8. **Offer to save when answer worth keeping.** Judge against both bars — *both* must hold or say nothing:

   - **Durable** — not a one-off lookup expiring with session, and not a single page's content restated (if one page answered the question, cite it; synthesis duplicating it is vault noise).
   - **Reusable** — drew several pages into something next asker would otherwise re-derive, and still true next month.

   When both hold, append `save-candidate` block to report — **proposal, not a write**. No `Write` tool, no way to ask user; invoking session puts the offer and, on explicit yes, performs save.

   ````markdown
   ```save-candidate
   title: How connection pooling is configured
   summary: Pool size is set per-service in the deploy config, not globally
   tags: [db, deployment]
   source_date: 2026-07-28
   volatility: evolving
   source:
     - wiki/concepts/db-connection-pooling.md
     - wiki/sources/deploy-github-actions.md
   ```
   ````

   Field notes: `summary` is one line, ≤ ~20 words — what *next* retrieval judges this page by, write it as well as you'd want to find it. `source_date` is **today** — synthesis made today even if inputs are older. `volatility` is **most volatile** of cited pages: synthesis only as durable as shakiest input. `source:` lists every page actually cited, as vault-relative paths (`enchiridion ingest` composes actual links when plan runs) — nothing merely skimmed.

   If neither bar holds, don't mention saving. Offering every answer trains user to say no — how confirmation gate stops working.

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

Two directions are NOT symmetric: outbound follows links that *leave* a page; inbound finds pages that *point at* a page. Inverting is silent miss.

## Saving an answer as a synthesis page

For **session holding the conversation** — invoked `wiki-researcher` and got `save-candidate` block back (or ran procedure and reached step 8 itself). Researcher subagent never gets here.

**Gate:** vault not written unless user says yes to question you actually asked. Silence isn't yes; "sounds useful" isn't yes; fresh session isn't holding earlier yes. If unsure whether told to save — you were not.

1. **Put the offer** in one line after relaying answer, naming what would be written and where:

   > *Worth saving as `wiki/synthesis/how-connection-pooling-is-configured.md`, sourced from the 3 pages it cites? (y/n)*

2. **On anything but explicit yes, stop.** No page, no plan file, no commit, no "I'll prepare just in case" — declining leaves vault byte-identical. Acknowledge in few words and move on; don't re-offer later in same session.

3. **On explicit yes, write the plan.** Save is `IngestPlan` run through same executor ingestion uses — placement, frontmatter, index regeneration, commit are mechanics, and mechanics belongs in tested script, not re-derived here. Write scratch `plan.json` (not a vault file) from `save-candidate` block:

   ```jsonc
   {
     "title": "<the candidate's title>",
     "action": "synthesize",              // NOT "ingest" — this is a researcher-saved page; the commit subject says so
     "source_date": "<today>",
     "pages": [
       {
         "op": "create",
         "kind": "synthesis",             // always — a saved query result is synthesis/ by the placement algorithm's step 2
         "title": "<the candidate's title>",
         "body": "<the answer, in full markdown, written as a page rather than a chat reply>",
         "frontmatter": {
           "summary": "<the candidate's summary line>",
           "tags": ["<the candidate's tags>"],
           "source_date": "<today>",
           "volatility": "<the candidate's volatility>"
         },
         "edges": {
           "source": ["wiki/concepts/db-connection-pooling.md"]   // one per cited page — the block's vault-relative paths, unchanged; `enchiridion ingest` composes the actual link
         }
       }
     ]
   }
   ```

   One conversion the block leaves to you:
   - **`body`** — rewrite answer as a page, not a transcript: no "you asked", no search-trajectory line, no "per the vault". Keep citations as inline relative links, keep temporal framing (synthesis inherits inputs' uncertainty and must not launder it into confidence).

   `source:` needs no conversion — block's paths are already vault-relative, exactly what `edges` takes; `enchiridion ingest` composes title, `../` relativisation, and encoding, and its validation rejects target that doesn't resolve to real page.

   No `raw` field and no `raw_source` — synthesis page has no raw artifact; stands on `source` edges to other pages. That is the `raw_source:`/`source:` split the schema draws.

4. **Run it and report.**

   ```bash
   "<plugin-root>/bin/enchiridion" ingest --plan <plan.json>
   ```

   Validates whole plan before touching disk, then writes page and makes one structured commit — printing SHA. Report path and SHA in one line. If it raises, nothing was committed; fix plan and rerun (writes are idempotent).

   If title collides with existing `synthesis/` page, validation fails with *create target … already exists*. Don't work around by renaming to near-duplicate — existing page is either the answer already (cite it instead) or genuinely superseded, which is an ingestion decision, not retrieval.