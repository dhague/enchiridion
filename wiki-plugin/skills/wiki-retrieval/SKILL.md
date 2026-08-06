---
name: wiki-retrieval
description: Turn a question into a grounded, cited answer over the wiki vault — query-expanded, BM25-ranked, frontmatter-first, and budget-bounded, with each citation's age and volatility stated honestly. Invoke via /wiki-retrieval <question>, or whenever the vault should be asked something.
---

# Wiki Retrieval

Reads `wiki-conventions` for anything this procedure doesn't spell out — folder structure, frontmatter schema, link format, typed-edge vocabulary. That skill is the contract: ingestion writes to it, this procedure reads assuming it. This file is preloaded into the `wiki-researcher` agent's context at startup, and is also what `/wiki-retrieval <question>` loads when invoked directly.

Retrieval **never modifies an existing page** — no edit, no move, no delete, ever. Its one write is a *new* `synthesis/` page, and only on the user's explicit confirmation ([Saving an answer as a synthesis page](#saving-an-answer-as-a-synthesis-page)).

## Invocation

- **If you are not already running as the `wiki-researcher` agent** (your own system prompt doesn't identify you as it — e.g. you were invoked directly via `/wiki-retrieval <question>` in an ordinary session), your only action is to delegate: call `Task` with `subagent_type: "wiki-researcher"` and a prompt containing the question, then relay the answer it returns back to the user. This keeps the reading and link-following inside the subagent's context — and on its Haiku model — regardless of what model the invoking session happens to be running.

  If the returned answer carries a `save-candidate` block, you are also the one who **puts the offer to the user and performs the save on their yes** — see [Saving an answer as a synthesis page](#saving-an-answer-as-a-synthesis-page). You hold the conversation, so the confirmation can only happen here.
- **If you are the `wiki-researcher` agent**, continue directly with the procedure below using your own tools. You **recommend** a save (step 8); you never perform one — a subagent cannot ask the user anything, and an unconfirmed save is the exact failure this design exists to prevent.

Every script invoked below lives in this plugin's install directory and resolves the vault root itself — see the `## Scripts` section of `wiki-conventions` for the full reference (vault-root resolution, `${CLAUDE_PLUGIN_ROOT}`, common tasks, and the script catalogue).

Search `wiki/**` only. `raw/` holds the immutable originals a `source/` page already stands in for; reading it duplicates content you have summaries for, and it is not in the index.

## Procedure

Given a question:

1. **Expand the query.** Before searching, write down **5–8 alternative phrasings** of the question's key terms: synonyms, the jargon form, the plain-English form, the singular/plural, the acronym and its expansion, the verb and noun forms. A single word choice must not decide whether a page is found — the vault's tags are emergent, so the page you want may name the thing differently than the asker did. These expansions become the term list you pass to `search.py` below.

2. **Single search call.** One call to `scripts/search.py` does the work — it composes BM25 text matching with metadata filters, ranks the results, and defaults to excluding superseded pages. Pass **only the term list** from step 1 as a single space-separated string (`search.py` tokenizes and phrase-quotes each term). Use `--json` and read the records:

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/search.py" \
       "<term1> <term2> <term3>" \
       --kind concept \
       --since 2026-07-01 --date-field source_date \
       --limit 20 --json
   ```

   Filter flags worth knowing:
   - `--tag <t>` (repeat for AND) and `--tag-any <t>` (OR). Tags are emergent — leave them off unless the question is clearly tag-shaped ("all pages tagged `db`").
   - `--kind concept|entity|source|synthesis` (comma-separated for multiple). Use this when the kind is obvious from the question ("who is X" → `--kind entity`).
   - `--since` / `--until` against `--date-field source_date` (valid time, the default) or `git_date` (transaction time). Use `git_date` for "updated this week" / "since the last ingestion"; use `source_date` for "knowledge from before X" / "the 2023 view of Y".
   - `--include-superseded` only when discussing history, not when answering "what is current". The default excludes superseded pages.
   - `--raw` is the escape hatch for callers who really want FTS5 operators (`NEAR`, `OR`, prefix `*`). Don't reach for it unless you have a specific reason.

   The first call to `search.py` triggers an `(mtime_ns, size)` staleness scan over `wiki/**` (~50 ms at 2000 pages, measured) so `git pull`, Obsidian edits, and manual changes are caught.

3. **Expand the frontier, frontmatter-first.** The hits are candidates, not answers. Judge each by its **`summary`** field — that is what `summary` exists for — and discard the ones that don't bear on the question. **Only a candidate that survives the summary judgment earns a full `Read` of its body.** Most of the frontier should die at the summary; a body read is the expensive step and is never the first move.

   From each page you do read, harvest its outbound relationships — the typed-edge keys and `supersedes` in frontmatter, plus body links to other `wiki/` pages — as **next-hop candidates**, and judge those the same way (summary first, body only on survival).

   **Follow the typed edge the question implies.** Unambiguous shapes map to one edge and one direction; anything else defaults to "follow all typed edges in both directions **except `source`**." The patterns live in [Edge-following rules](#edge-following-rules) below. Direction matters: a question that reads "what does X refine" asks for X's outbound `refines:` list; "what refines X" asks which pages name X under their own `refines:`. Inverting one for the other is a silent miss in either direction.

   `source` is excluded from that fallback because it belongs to the provenance path, not general expansion — it names the raw artifact a page distilled, not another concept to fan out into. Only follow it when the question matches the provenance row below.

4. **Filter the frontier for currency.** A superseded page is never an answer — `supersedes` is a *recorded fact* (see [Frontmatter schema](../wiki-conventions/SKILL.md#frontmatter-schema)), and a recorded fact beats any recency guess. Build a `superseded_by` map for the candidate set: for every page P, if any other page Q in the vault has `supersedes: [P]` in its frontmatter, then P is replaced by Q. With the agent's tools, one pass is `Grep` for the frontmatter pattern `^\s*supersedes:` plus each seed's filename in the body of any `wiki/**/*.md` — `page_record.py` derives the same map in-process for `build_index.py` and the upcoming `Vault.search()` under the name `superseded_by`.

   **Three rules for the filter pass:**

   - **Same-set supersession.** For each candidate P, if a candidate Q has `supersedes: [P]`, **drop P from the active set and keep Q as current**. P is history, not the answer.
   - **Chains.** A supersession chain A → B → C where A, B, C are all in the set collapses to **just C** (the head); A and B are mentioned as history, not read. Don't blow the budget walking a chain frontmatter by frontmatter — one follow per page is enough; the rest is mentioned, not cited.
   - **Head not in set.** If a seed P is superseded by a Q that *isn't* in the candidate set, **add Q to the set** (frontmatter-only read is enough to confirm the chain head) and remove P from the active set. The user asked about the current view; P is not it.

   The mental shortcut, when you're about to cite a page: scan the frontmatter of every other page you're about to cite for a `supersedes:` that names this one. If one exists, cite the *superseding* page instead and frame the older one as what it replaced.

5. **Stop at the budget.** Retrieval is bounded, and the bound is stated so it can't quietly run away:
   - **max 2 hops** from the seed set,
   - **~12 pages read** in full,
   - **stop early when the next page adds nothing** — if the last page or two contributed no new fact bearing on the question, you are done, whatever the counters say.

   If you hit the budget with the question still unanswered, say so in the answer (what you searched, what you'd read next) rather than silently truncating or blowing through it.

6. **Synthesize, with honest temporal framing.** Answer from what you actually read, and **cite every claim** with the page it came from (a relative link or its vault path — the reader must be able to open it).

   **Two citation modes, and the question decides which applies:**
   - **Normal** — the common case, for every question that isn't provenance-shaped. Cite the concept (or entity/synthesis) page itself, with its age and `volatility` stated as below. Do not read the `source` stub or the raw artifact behind it; the concept page is standing in for that raw material, and reading through it is wasted budget the question didn't ask for.
   - **Provenance** — when the question matches the provenance row in [Edge-following rules](#edge-following-rules). Follow the chain all the way to the raw artifact (concept page → `source` → stub → `raw_source` → raw file) and cite the raw artifact itself, with a specific location (line or page number) for the claim. Frame the concept page as a lens on that source, not as the citation: *"per [Concept](...), drawing on [raw-file.md](...) line 42…"*. If a provenance question lands on a page with no `source` edge (pre-#34 content), degrade gracefully — cite the page normally and note that no provenance chain exists for it.

   For each cited page state its age and its `volatility` plainly, so the asker can calibrate their own trust:
   - **valid time** is the page's own `source_date` — when the knowledge is *from*;
   - **transaction time** is `git_date` from the search hit, populated in one `git log` pass at index time (see [Derived from git](../wiki-conventions/SKILL.md#derived-from-git)) — sanity-check it before quoting it: a vault that was bulk-imported gives every page the same commit date, which says nothing about the knowledge. When commit dates are uninformative, frame the answer on `source_date` and say that's what you're using;
   - **`volatility`** says how much that age matters: `stable` facts do not age out, `evolving` ones drift, `volatile` ones are possibly current-only.

   Phrase it in the answer, don't bury it — e.g. *"per [Rate limits](wiki/concept/rate-limits.md), marked `volatile`, from 2025-01-12 and last committed 14 months ago…"*. A `stable` page from three years ago is not stale; a `volatile` page from last quarter may already be wrong. Never present a `volatile` fact with the same confidence as a `stable` one just because it is what you found.

   **Recency is never a re-ranking signal.** When the question matches two pages P₁ (older, `stable`) and P₂ (newer, `volatile`), do not promote P₂ above P₁ just because P₂ is newer — present both with their volatilities and let the asker judge. The one exception is a question that is *explicitly* time-anchored ("what's the latest X", "as of YYYY-MM-DD"), where recency IS the signal; even then, a `supersedes` edge still wins, because it is a recorded fact, stronger than any recency guess.

   **Never present a superseded page as current** — step 4 already filtered them out of the active set; the framing here just makes sure the *answer* is consistent. For a chain where the user asked about a page that has been replaced, frame the answer on the *current* page ("per [Current]…") and mention the older one only as what it replaced ("replaces [Old], which said…"). When the user asks about the chain itself ("what did we use before X?"), say so explicitly: *"per [A] (now superseded by [C] via [B])…"*.

   Where the pages you read genuinely conflict without one superseding the other, say so and show both — resolving a live contradiction is the asker's call, not yours.

7. **Report.** Reply with the answer and its citations, plus one short line on what was searched (expansions used, pages read, hops taken) so the asker can see whether the search missed their framing. Never dump page bodies into the answer — that is the reading noise this subagent exists to keep out of the main thread.

8. **Offer to save, when the answer is worth keeping.** Judge the answer you just wrote against both bars — and *both* must hold, or say nothing:

   - **Durable** — it isn't a one-off lookup whose value expires with this session, and it isn't already a single page's content restated (if one page answered the question, cite that page; a synthesis page that duplicates it is vault noise).
   - **Reusable** — it drew several pages together into something the next asker would otherwise have to re-derive, and it will still be true next month.

   When both hold, append a `save-candidate` block to your report — a **proposal, not a write**. You have no `Write` tool and no way to ask the user; the session that invoked you puts the offer and, on an explicit yes, performs the save.

   ````markdown
   ```save-candidate
   title: How connection pooling is configured
   summary: Pool size is set per-service in the deploy config, not globally
   tags: [db, deployment]
   source_date: 2026-07-28
   volatility: evolving
   source:
     - wiki/concept/db-connection-pooling.md
     - wiki/source/deploy-github-actions.md
   ```
   ````

   Judgment notes for the fields: `summary` is one line, ≤ ~20 words, and is what the *next* retrieval will judge this page by — write it as well as you'd want to find it. `source_date` is **today**, because the synthesis was made today even though its inputs are older. `volatility` is the **most volatile** of the pages you drew on: a synthesis is only as durable as its shakiest input. `source:` lists every page the answer actually cited, as vault-relative paths (`ingest.py` composes the actual links from these when the plan runs) — nothing you merely skimmed.

   If neither bar holds, do not mention saving at all. Offering on every answer trains the user to say no, which is how a confirmation gate stops working.

## Edge-following rules

When a question's shape implies a specific typed edge, follow *that* edge in the implied direction. The agent's job is to identify the implied edge from the question's pattern; the table covers the unambiguous cases. Anything not on it defaults to "follow all typed edges in both directions" — the broader `related` graph is the fallback.

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
| "what's the evidence for X" / "source for X" / "raw data behind X" / "provenance of X" / "where did this come from" / "cite the original" / "back it up" | `source` | outbound from X | X's `source:` list → the stub page → its `raw_source:` → the raw artifact, with a location (line or page number) |

The two directions are NOT symmetric: outbound follows links that *leave* a page; inbound finds pages that *point at* a page. Inverting one for the other is a silent miss in either direction.

## Saving an answer as a synthesis page

This section is for **the session that holds the conversation** — the one that invoked `wiki-researcher` and got a `save-candidate` block back (or that ran the procedure and reached step 8 itself). The researcher subagent never gets here.

**The gate:** the vault is not written unless the user says yes to a question you actually asked. Silence isn't yes; "sounds useful" isn't yes; a fresh session is not still holding an earlier yes. If you are unsure whether you were told to save, you were not.

1. **Put the offer** in one line after relaying the answer, naming what would be written and where:

   > *Worth saving as `wiki/synthesis/how-connection-pooling-is-configured.md`, sourced from the 3 pages it cites? (y/n)*

2. **On anything but an explicit yes, stop.** No page, no plan file, no commit, no "I'll prepare it just in case" — declining leaves the vault byte-identical. Acknowledge in a few words and move on; don't re-offer later in the same session.

3. **On an explicit yes, write the plan.** The save is an `IngestPlan` run through the same executor ingestion uses — placement, frontmatter, index regeneration and the commit are all mechanics, and mechanics belongs in the tested script, not re-derived here. Write a scratch `plan.json` (not a vault file) from the `save-candidate` block:

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
           "source": ["wiki/concept/db-connection-pooling.md"]   // one per cited page — the block's vault-relative paths, unchanged; ingest.py composes the actual link
         }
       }
     ]
   }
   ```

   One conversion the block leaves to you:
   - **`body`** — rewrite the answer as a page, not a transcript: no "you asked", no search-trajectory line, no "per the vault". Keep the citations as inline relative links, keep the temporal framing (a synthesis inherits its inputs' uncertainty and must not launder it into confidence).

   `source:` needs no conversion — the block's paths are already vault-relative, exactly what `edges` takes; `ingest.py` composes the title, `../` relativisation, and encoding, and its validation rejects a target that doesn't resolve to a real page.

   No `raw` field and no `raw_source` — a synthesis page has no raw artifact; it stands on `source` edges to other pages. That is exactly the `raw_source:`/`source:` split the schema draws.

4. **Run it and report.**

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/ingest.py" --plan <plan.json>
   ```

   It validates the whole plan before touching disk, then writes the page, regenerates `_index.md`, and makes one structured commit — printing the SHA. Report the path and the SHA in one line. If it raises, nothing was committed; fix the plan and rerun (writes are idempotent).

   If the title collides with an existing `synthesis/` page, validation fails with *create target … already exists*. Don't work around it by renaming to a near-duplicate — that existing page is either the answer already (cite it instead) or genuinely superseded, which is an ingestion decision, not a retrieval one.
