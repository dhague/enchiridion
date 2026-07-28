---
name: wiki-retrieval
description: Turn a question into a grounded, cited answer over the wiki vault — query-expanded, BM25-ranked, frontmatter-first, and budget-bounded, with each citation's age and volatility stated honestly. Invoke via /wiki-retrieval <question>, or whenever the vault should be asked something.
---

# Wiki Retrieval

Reads `wiki-conventions` for anything this procedure doesn't spell out — folder structure, frontmatter schema, link format, typed-edge vocabulary. That skill is the contract: ingestion writes to it, this procedure reads assuming it. This file is preloaded into the `wiki-researcher` agent's context at startup, and is also what `/wiki-retrieval <question>` loads when invoked directly.

Retrieval is **read-only** apart from one exception (a `synthesis/` page saved on the user's explicit confirmation). Never edit, move, or delete an existing page while answering a question.

## Invocation

- **If you are not already running as the `wiki-researcher` agent** (your own system prompt doesn't identify you as it — e.g. you were invoked directly via `/wiki-retrieval <question>` in an ordinary session), your only action is to delegate: call `Task` with `subagent_type: "wiki-researcher"` and a prompt containing the question, then relay the answer it returns back to the user. This keeps the reading and link-following inside the subagent's context — and on its Haiku model — regardless of what model the invoking session happens to be running.
- **If you are the `wiki-researcher` agent**, continue directly with the procedure below using your own tools.

## Vault root and script location

Every script in `scripts/` resolves the vault root itself (`$WIKI_ROOT`, else the nearest ancestor `wiki/` directory, else cwd — see `vault.py`). Make sure your shell's working directory is already inside the target vault (or export `WIKI_ROOT`) before invoking any of them. To resolve it explicitly for your own `Read` paths:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/vault.py"   # prints the resolved vault root
```

The scripts themselves live in *this plugin's own* install directory, not the vault — invoke them via `${CLAUDE_PLUGIN_ROOT}/scripts/<name>.py` (the placeholder is substituted before you ever see this text, so the commands below are already the resolved absolute path). This works identically whether cwd is inside the plugin's own repo (dedicated mode) or a separate vault repo (query-from-anywhere mode).

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

   **Follow the typed edge the question implies.** Unambiguous shapes map to one edge and one direction; anything else defaults to "follow all typed edges in both directions." The patterns live in [Edge-following rules](#edge-following-rules) below. Direction matters: a question that reads "what does X refine" asks for X's outbound `refines:` list; "what refines X" asks which pages name X under their own `refines:`. Inverting one for the other is a silent miss in either direction.

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

6. **Synthesize, with honest temporal framing.** Answer from what you actually read, and **cite every claim** with the page it came from (a relative link or its vault path — the reader must be able to open it). For each cited page state its age and its `volatility` plainly, so the asker can calibrate their own trust:
   - **valid time** is the page's own `source_date` — when the knowledge is *from*;
   - **transaction time** is `git_date` from the search hit, populated in one `git log` pass at index time (see [Derived from git](../wiki-conventions/SKILL.md#derived-from-git)) — sanity-check it before quoting it: a vault that was bulk-imported gives every page the same commit date, which says nothing about the knowledge. When commit dates are uninformative, frame the answer on `source_date` and say that's what you're using;
   - **`volatility`** says how much that age matters: `stable` facts do not age out, `evolving` ones drift, `volatile` ones are possibly current-only.

   Phrase it in the answer, don't bury it — e.g. *"per [Rate limits](wiki/concept/rate-limits.md), marked `volatile`, from 2025-01-12 and last committed 14 months ago…"*. A `stable` page from three years ago is not stale; a `volatile` page from last quarter may already be wrong. Never present a `volatile` fact with the same confidence as a `stable` one just because it is what you found.

   **Recency is never a re-ranking signal.** When the question matches two pages P₁ (older, `stable`) and P₂ (newer, `volatile`), do not promote P₂ above P₁ just because P₂ is newer — present both with their volatilities and let the asker judge. The one exception is a question that is *explicitly* time-anchored ("what's the latest X", "as of YYYY-MM-DD"), where recency IS the signal; even then, a `supersedes` edge still wins, because it is a recorded fact, stronger than any recency guess.

   **Never present a superseded page as current** — step 4 already filtered them out of the active set; the framing here just makes sure the *answer* is consistent. For a chain where the user asked about a page that has been replaced, frame the answer on the *current* page ("per [Current]…") and mention the older one only as what it replaced ("replaces [Old], which said…"). When the user asks about the chain itself ("what did we use before X?"), say so explicitly: *"per [A] (now superseded by [C] via [B])…"*.

   Where the pages you read genuinely conflict without one superseding the other, say so and show both — resolving a live contradiction is the asker's call, not yours.

7. **Report.** Reply with the answer and its citations, plus one short line on what was searched (expansions used, pages read, hops taken) so the asker can see whether the search missed their framing. Never dump page bodies into the answer — that is the reading noise this subagent exists to keep out of the main thread.

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

The two directions are NOT symmetric: outbound follows links that *leave* a page; inbound finds pages that *point at* a page. Inverting one for the other is a silent miss in either direction.

## Not yet built

The remaining part of the retrieval design is specced and ticketed but deliberately not documented here yet — don't improvise it:

- **Saving a result as a `synthesis/` page** ([#18](https://github.com/dhague/enchiridion/issues/18)) — only ever on the user's explicit yes, never auto-saved. Until it is built, do not write pages.
