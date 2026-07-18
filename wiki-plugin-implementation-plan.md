# Wiki Knowledge Plugin — Implementation Plan

**Audience:** a coding agent implementing this end to end.
**Format:** a Claude Code plugin operating on a `wiki/` folder of markdown notes (Karpathy LLM-wiki pattern, markdown links — *not* wikilinks). The vault is a **git repository** — every ingestion or edit commits (§4).
**Scope:** build **both ingestion and retrieval** from scratch (clean-room — no third-party code to port). Helper scripts in **Python**. Deterministic layers built **test-first (TDD)**; agent layers checked with **evals** (§5).

The plugin ships **no MCP server**. Everything is skills + agents + Python scripts invoked through Bash. Deliberate — do not add an MCP server without re-reading §9.

**Two deployment modes** (both supported; the difference is only how the vault is located — see §1):
- **Dedicated** — plugin installed project-scope in the KB folder; you launch Claude Code from there; vault = `cwd`.
- **Query-from-anywhere** — plugin installed **user-scope** (`~/.claude/skills/`) so it's available inside any repo; `WIKI_ROOT` points at the vault. This is the mode for querying the wiki from inside an unrelated code repo.

---

## Wayfinding decisions (2026-07-18)

Charted as a wayfinder map — **[Wiki Knowledge Plugin](https://github.com/dhague/enchiridion/issues/1)** (issue #1). This build is a **hybrid** map: execution is carried into tickets, but genuine design decisions gate the build tickets they inform. Decisions taken while charting, which amend the plan below:

- **Destination (finish line):** ingestion + retrieval working end-to-end, all phase evals green against the golden vault, plugin installed and verified in **both** deployment modes, statusline present for the §10.2 cache measurement. **No marketplace/distribution step.**
- **Tags are emergent, not controlled** — generated at ingestion (reuse where sensible, mint where needed). Amends the §3 schema, §3 Phase 3 step 5, and retires the §12-A tag-vocab lint rule.
- **Folder structure is opinionated and plugin-fixed**, not per-vault — **resolved** in [#5](https://github.com/dhague/enchiridion/issues/5) (on prior-art survey [#2](https://github.com/dhague/enchiridion/issues/2)): a **kind-axed, single-level page tree**, detailed in [Vault structure](#vault-structure) below. Per-vault taxonomy is explicitly *not* a feature.
- **The wiki's units are "pages", not "notes"** (from [#5](https://github.com/dhague/enchiridion/issues/5)). Prose below still says "note" in places — same thing; the conventions spec (Phase 1) standardizes on "page".
- **Schema gains a `source:` field** (from [#5](https://github.com/dhague/enchiridion/issues/5)) — required on `source/` pages, a link to the ingested `raw/` artifact. Amends the §3 schema.
- **Retrieval becomes a writer for `synthesis/`** (from [#5](https://github.com/dhague/enchiridion/issues/5)) — the `wiki-researcher` gains `Write` and, on a valuable result, *recommends* saving a `synthesis/` page that the user confirms before it writes (no auto-save). Amends the §3 / Phase-5 read-only tool list.
- **Golden vault is the eval + measurement substrate.** Real-vault-scale numbers are out of scope, so §10.1 is nearly foregone (whole-read). Human owns the golden vault + property list (§5).
- **Out of scope:** marketplace/distribution; the entire §12 roadmap; real-vault-scale tuning (folder tiering at ~1k notes, embeddings); a controlled tag vocabulary.

---

## 0. Four efficiency lenses (apply to every decision)

Acceptance criteria, not aspirations.

- **Token-efficient** — nothing loads into context that isn't needed for the current step. Summaries before bodies.
- **Context-efficient** — heavy work (document reading, grep noise, traversal) happens in subagents whose context is discarded; only summaries return to the main thread.
- **Cache-efficient** — stable content in the prefix, per-request content appended at the end; nothing reorders the prefix mid-session.
- **Model-efficient** — the weakest viable model per component:

  | Component | Model | Why |
  |---|---|---|
  | Python scripts (frontmatter, links, index) | **none** | Deterministic. Zero inference. Run via Bash. |
  | Retrieval agent (`wiki-researcher`) | **Haiku** | Map-reading and link-following are comprehension, not reasoning. Escalate only if §10 measurement shows synthesis is too weak. |
  | Ingestion agent (`wiki-ingest`) | **Sonnet** | Semantic chunking, dedup against existing notes, and typed-edge assignment are judgment. Haiku will under-assign edges. |

  The biggest model-efficiency win: **all mechanical work is Python, not inference**. Push every deterministic operation into a script so the agent spends tokens only on judgment.

---

## 1. Hard constraints (Claude Code plugin mechanics)

Verify against the current plugins reference before starting; accurate as of July 2026.

- Manifest lives at **`.claude-plugin/plugin.json`**. Every other directory (`skills/`, `agents/`, `hooks/`) sits at the **plugin root**, not inside `.claude-plugin/`.
- **`CLAUDE.md` at the plugin root is not loaded as context.** Ship all instructions as skills.
- **`commands/` is legacy.** Use `skills/` for capabilities; a user-facing `/wiki-ingest` can be a thin command whose logic lives in a skill.
- **Plugins cannot reference files outside their copied directory** (no `../`). All scripts live inside the plugin; the plugin resolves its own assets via **`${CLAUDE_PLUGIN_ROOT}`**.
- **Vault-root resolution** (implement once in `vault.py`, used by every script and stated in both agent skills), in order:
  1. **`$WIKI_ROOT`** if set — wins always. This is the query-from-anywhere mode.
  2. else the **nearest ancestor of `cwd`** containing a vault marker (`wiki/` dir or a `.wiki-root` sentinel). Handles being deep inside the KB or a repo that contains one.
  3. else **`cwd`**. The dedicated-mode default.
  Never hard-code a path. Resolving vault *data* location this way is separate from plugin *loading* (next point) — the two are independent and both must be satisfied.
- **Plugin loading vs. vault location are different problems.** Project-scope skills-dir plugins load only from the launch directory's `.claude/skills/` and do not walk up. So: dedicated mode → launch from the KB root. Query-from-anywhere → install the plugin **user-scope** so its skills/agents are available in any repo, and set `WIKI_ROOT`. A `/reload-plugins` is needed after `cd` for project-scope installs.
- **Git is a hard dependency.** The vault is a git repo; scripts commit (§4). If `git` is absent or the vault isn't a repo, `commit.py` should fail loudly rather than silently skipping — the time model and the roadmap features depend on the history being complete.
- **Live-change asymmetry:** `SKILL.md` edits apply immediately; edits to `agents/`, `hooks/`, `.mcp.json` need `/reload-plugins` or restart.
- **Plugin subagents ignore `mcpServers`, `hooks`, `permissionMode` frontmatter** (security). We ship none — leave a comment in each agent file saying so, so nobody adds one later and wonders why it's dropped.

---

## 2. Target file tree

```
wiki-plugin/
├── .claude-plugin/
│   └── plugin.json
├── skills/
│   ├── wiki-conventions/
│   │   └── SKILL.md                # frontmatter schema, link rules, folder map, edge vocabulary — the shared spec
│   ├── wiki-ingest/
│   │   └── SKILL.md                # ingestion procedure (drives the Sonnet agent)
│   └── wiki-retrieval/
│       └── SKILL.md                # retrieval loop + termination budget (drives the Haiku agent)
├── agents/
│   ├── wiki-ingest.md              # Sonnet; tools: Read, Write, Edit, Grep, Glob, Bash; skills: [wiki-conventions, wiki-ingest]
│   └── wiki-researcher.md          # Haiku;  tools: Read, Grep, Glob, Bash;              skills: [wiki-conventions, wiki-retrieval]
├── scripts/
│   ├── vault.py                    # resolve vault root: $WIKI_ROOT → ancestor marker → cwd
│   ├── frontmatter.py              # ruamel round-trip read/patch of YAML frontmatter
│   ├── links.py                    # position-splice link rewrite on move/rename
│   ├── normalize_raw.py            # rename raw file → YYYY-MM-DD-hhmm-…, spaces→underscores; content-immutable; drives links.py
│   ├── build_index.py              # walk vault frontmatter → _index.md
│   ├── commit.py                   # structured git commit after ingestion/edit (§5)
│   ├── backlinks.py                # thin ripgrep wrapper (optional)
│   └── lib/
│       └── md.py                   # shared: split frontmatter/body, parse to AST w/ source positions
├── commands/
│   ├── wiki-ingest.md              # /wiki-ingest <path> → delegates to wiki-ingest agent
│   └── wiki-reindex.md             # /wiki-reindex → runs build_index.py
├── tests/                          # pytest — deterministic script layer (TDD, §5)
│   ├── fixtures/
│   └── ...
├── evals/                          # agent-layer checks (§5)
│   ├── golden-vault/               # hand-authored ground truth — YOU own this
│   ├── cases/                      # ingestion + retrieval cases with expected properties
│   └── run.py                      # harness
├── pyproject.toml                  # ruamel.yaml, markdown-it-py; dev: pytest, hypothesis
├── LICENSE
└── README.md                       # install, launch-from-root caveat, conventions
```

---

## Vault structure

The plugin operates on a **git-backed vault** with a fixed, opinionated layout for its **pages** (the wiki's units — "note" elsewhere in this plan means the same thing; the conventions spec standardizes on "page"). Resolved in [#5](https://github.com/dhague/enchiridion/issues/5) on prior-art survey [#2](https://github.com/dhague/enchiridion/issues/2) (`docs/research/folder-taxonomy.md`).

```
<vault root>/
├── wiki/                     ← pages; the vault marker (§1)
│   ├── _index.md             ← generated (build_index.py); indexes wiki/** only, never raw/
│   ├── concept/              ← idea / technique / pattern / principle / how-it-works (the default)
│   ├── entity/               ← a named thing linked repeatedly (person/team/product/tool/service/project/org)
│   ├── source/               ← stand-in for a raw artifact; REQUIRES `source:` → ../../raw/…
│   └── synthesis/            ← saved query result; links to its inputs via `source`-type edges
└── raw/                      ← immutable originals, git-tracked, sibling of wiki/
    └── <user-extensible>/    ← emails/ meetings/ notes/ clippings/ documents/ … OPEN set
```

**Why kind, not domain or topic.** Kind is the only axis both decidable from a page's content *and* domain-independent — required for a structure shipped fixed to every vault, not tuned per-vault. Deep/topic-fine trees and PARA both fail the decidability test (a page fits two sibling folders equally) and are ruled out. Multi-membership rides on **tags + typed edges**, never a second folder. The `_index.md` of summaries + the typed-edge graph stay the real retrieval surface; the folder tree is a thin, decidable filing + index-grouping handle (it also enables the §6 tiering at ~1k pages, itself out of scope here).

**Placement algorithm** (ingestion runs top-to-bottom, first match wins → deterministic). The kinds split into *origin-defined* (`source`, `synthesis` — where the page came from, mutually exclusive) and *subject-defined* (`entity`, `concept` — what it's about):

1. Stand-in for an ingested raw artifact? → **`source/`** (must carry `source:` → its `raw/` file).
2. A saved query result synthesized from other pages? → **`synthesis/`**.
3. Primarily a named thing linked repeatedly? → **`entity/`**.
4. Otherwise → **`concept/`** (the default).

**Tie-break:** a page plausibly of two *subjects* is filed by primary function; every other facet is tags/edges. If two folders are ever a toss-up given a page's title + summary, the axis is wrong — merge them and push the distinction to tags.

**The `raw/` layer.** Immutable originals, git-tracked, a **sibling** of `wiki/` (Karpathy's raw-vs-generated split). It's an **inbox** a deterministic script scans for new files, so its subfolders are **user-extensible** — the five listed are typical defaults, not a closed set. Ingestion **never edits a raw file's contents**; it may **rename** to normalize (`YYYY-MM-DD-hhmm-…`, spaces→underscores) via `normalize_raw.py` (§3 Phase 2), which drives `links.py` so `source:` pointers follow the rename. (Catching an *external* raw-folder rename and repairing `source:` links belongs to the deferred linter, §12-A — out of scope here.)

**Naming.** Kind-folders singular; raw sub-folders plural & user-extensible. Page filenames are lowercase kebab-slugs of the title with **no date prefix** (`concept/prepared-statements.md`) — git carries the ingestion date and `source_date` the valid-time; a filename date would be a third drifting clock. Raw files keep the datetime prefix precisely because they're artifact-anchored.

---

## 3. Phase plan

Each phase is independently testable and leaves the plugin working. Build in order.

### Phase 1 — Scaffold + conventions spec

- Write `plugin.json` (name, version, description, author) — minimal.
- Write `skills/wiki-conventions/SKILL.md` — the **single source of truth**: frontmatter schema (below), the **opinionated, plugin-fixed folder structure** (designed deliberately — see the wayfinding note above; Karpathy is the reference, not a copy), link format (relative markdown), typed-edge vocabulary. Preloaded by both agents, so it is the contract between ingestion and retrieval. Note the split: the folder structure is the *fixed* half of the contract; **tags are the emergent half** (generated at ingestion), so they are not enumerated here. **→ Delivered:** [`wiki-plugin/skills/wiki-conventions/SKILL.md`](wiki-plugin/skills/wiki-conventions/SKILL.md) (resolved [#6](https://github.com/dhague/enchiridion/issues/6)) — the authoritative artifact; the schema block below is a mirror.
- Confirm project-scope install and that both (empty) agents are discoverable.

**Frontmatter schema.** Only fields that are *judgment* live here; anything git can tell us is derived, not authored (see §4):

```yaml
---
title: <human title>
summary: <one line, ≤ ~20 words>        # THE field retrieval reads; write it well at ingestion
tags: [<emergent — reuse existing tags where sensible, mint new where needed; NOT a fixed controlled vocabulary>]
source_date: <YYYY-MM-DD>               # when the knowledge is FROM — judgment, not recoverable from git
source: <relative/path into raw/>       # REQUIRED on source/ pages: link to the ingested artifact. Omit on other kinds.
volatility: stable | evolving | volatile
supersedes: [<relative/path.md>, ...]   # optional
links:
  - {to: <relative/path.md>, type: refines | contradicts | example-of | source | related}
---
```

**Deliberately absent:** `updated_at` and `ingested_at`. Git's commit history is the authoritative, trust-free record of when each note was touched and first added — a hand-maintained `updated_at` an agent forgets to bump is worse than no field. Derive both from `git log` on demand. `source_date` and `volatility` stay because they're judgments the history can't reconstruct. (The bitemporal model of §8 is now: `source_date` = valid time, git commit date = transaction time.)

**Acceptance:** installs from KB root; `/plugin list` shows it; `wiki-conventions` loads.

### Phase 2 — Python script layer (no model, built test-first)

Clean-room. **Write the test before the code** for each unit: red → green → refactor. The Phase-2 acceptance conditions below are literally your first failing tests.

- **`vault.py`** — resolve the vault root by the §1 order (`$WIKI_ROOT` → ancestor marker → `cwd`). Every other script imports this; none hard-codes a path. Trivial to test: set/unset the env var, run from nested dirs, assert the resolved root.
- **`lib/md.py`** — split frontmatter from body; parse body to an AST carrying **source positions** (markdown-it-py tokens with `map`). Shared primitive.
- **`frontmatter.py`** — read/patch frontmatter with **`ruamel.yaml`** (round-trip: preserves key order, comments, quoting; does not coerce `HH:MM`/dates). CLI: `get`, `set`. Never re-serialise the whole document.
- **`links.py`** — the fiddly one. On move/rename, rewrite (a) inbound links across the vault pointing at the moved note, and (b) outbound relative links inside the moved note. **Locate link nodes via AST positions, then splice the original source back-to-front** (highest offset first). **Never round-trip through a stringifier.** Handle anchors (`path.md#h`) and image embeds.
- **`normalize_raw.py`** — normalize a raw file's *name* (prefix `YYYY-MM-DD-hhmm`, spaces→underscores) **without ever touching its contents**; on rename, drive `links.py` so any `source:` pointer follows. TDD against a fixture: content byte-identical, name normalized, pointer rewritten.
- **`commit.py`** — stage the touched notes + regenerated index and write **one structured commit** per ingestion/edit (§4). Deterministic given a manifest; TDD against a throwaway git repo fixture.
- **`backlinks.py`** — optional; `rg -l 'note.md'` likely suffices.

**These two functions get property-based tests (`hypothesis`), not just examples** — the properties are where the real bugs hide:
- *Frontmatter round-trip:* for any valid note, read→write of an untouched key leaves **every byte identical**. (Catches the reformatting-stringifier bug.)
- *Move:* for any vault + any move, after rewriting, **only link-bearing lines differ**, and all affected links still resolve. (Catches the splice-ordering bug.)

Example-based tests cover the edge cases: anchors, image embeds, links inside list items, a self-linking note, a pure rename.

**Acceptance:** `pytest` green including the two property tests; scripts run standalone against a fixture vault with byte-exact non-target preservation.

### Phase 3 — Ingestion (Sonnet agent)

- `agents/wiki-ingest.md`: `model: sonnet`; `tools: Read, Write, Edit, Grep, Glob, Bash`; `skills: [wiki-conventions, wiki-ingest]`.
- `skills/wiki-ingest/SKILL.md` procedure:
  1. Read the source document.
  2. Split into semantic chunks (judgment — the reason this is Sonnet).
  3. **Check the index / grep before creating** — update or link an existing note rather than duplicating. Dedup-against-existing is the quality bar.
  4. Create/update pages, **placing each by the Vault-structure placement algorithm** (§ *Vault structure*). For an ingested artifact: normalize the raw file first (`normalize_raw.py`), create a `source/` page, and set its `source:` → the raw file. Write frontmatter via `frontmatter.py`; set `source_date` from the document's own date and a `volatility` judgment. (No `updated_at`/`ingested_at` — git records those.)
  5. Assign **typed edges** and **emergent tags** (reuse existing tags where they fit; mint new ones where needed — no fixed vocabulary to conform to). Typed edges are the highest-leverage output — retrieval cannot recover an edge type never recorded.
  6. **On contradiction, append + `supersedes`, don't overwrite.**
  7. Run `build_index.py`, then `commit.py` with the manifest — one structured commit per ingestion (§4).
- `commands/wiki-ingest.md`: thin `/wiki-ingest <path>` delegating to the agent.
- Instruct the agent to return a short manifest (notes created/updated, edges added), **not** content dumps — the reading noise dies in the subagent.

**Acceptance:** ingestion evals pass (§5) — schema-valid notes, resolving relative links, typed edges present, index regenerated, overlapping re-ingest updates rather than duplicates.

### Phase 4 — Index generation (no model, test-first)

- `build_index.py` walks every note's frontmatter → **`_index.md`**: one line per note (path, title, summary, tags, `source_date`, `volatility`, outlinks). ~40 tokens/line.
- Runs as the last ingestion step **and** standalone via `/wiki-reindex`.
- **Derived data — always regenerate, never cache to disk as truth.** A stale index is a correctness bug wearing a performance costume.
- TDD as in Phase 2: assert deterministic output and correct line count/shape against a fixture vault before implementing.

**Acceptance:** regenerates deterministically; **measure its token count on the real vault** (§10) — the number that decides Phase 5 strategy.

### Phase 5 — Retrieval (Haiku agent)

- `agents/wiki-researcher.md`: `model: haiku`; `tools: Read, Grep, Glob, Bash, Write`; `skills: [wiki-conventions, wiki-retrieval]`. (`Write` is for persisting a `synthesis/` page — step 7 below; retrieval is otherwise read-only.)
- `skills/wiki-retrieval/SKILL.md` — the loop:
  1. **Query expansion** — write 5–8 alternative phrasings/synonyms/jargon before searching. (Where "semantic" lives, minus embeddings.)
  2. **Multi-seed** — union of grep-on-terms, tag matches, title matches.
  3. **Frontier expansion, frontmatter-first** — judge candidates by their `summary:` line; read a body only once it survives.
  4. **Follow typed edges** the question implies.
  5. **Termination budget — state it:** max 2 hops, ~12 notes, stop when the next note adds nothing.
  6. **Synthesise** with honest temporal framing (§8): prefer `supersedes` facts over recency guesses; surface age and `volatility`.
  7. **Offer to persist** — if the result is a durable, reusable answer, **recommend** saving it as a `synthesis/` page; on the user's yes (never auto-save), write it (full frontmatter, a `summary:`, and `source`-type edges to the pages it drew on), run `build_index.py`, and `commit.py`.
- At ~1,000 notes, if `_index.md` outgrows a comfortable single read, **tier by folder** (top-level MOC index → pick folders → read those folder indices). Do not reach for embeddings (§10).

**Acceptance:** retrieval evals pass (§5) — correct notes within budget, source paths cited, stale/volatile flagged, superseded facts not returned as current.

### Phase 6 — Packaging, model tuning, measurement

- **Statusline script** reading `current_usage` for `cache_creation_input_tokens` vs `cache_read_input_tokens`.
- Run the §10 experiments; finalise model assignments from evidence.
- `README.md`: both deployment modes (§1), the `WIKI_ROOT` override, conventions summary.
- Marketplace entry if distributing.

**Acceptance:** clean install on a fresh machine, both modes; measurements recorded.

---

## 4. Git integration

The vault is a git repo; every ingestion or edit produces a commit. This is infrastructure, built alongside Phase 2 (`commit.py`) and wired into Phase 3.

**What commits, and how.** One commit per *ingestion*, not per file — a coherent unit ("ingested doc X → 3 notes, superseded Y"), covering the touched notes plus the regenerated `_index.md`. Manual edits commit per edit. **`commit.py` does the committing, not the agent freehand** — that keeps messages uniform and harvestable. A pre-commit hook can additionally run the deterministic linter (roadmap, §12) as a gate.

**Structured commit message.** The message is a compounding asset — it's the audit log, the "what changed this week" feed, and the manager-report source, designed once. Use a parseable trailer:

```
ingest: <source doc title>

created: concepts/prepared-statements.md
updated: concepts/db-connection-pooling.md
superseded: systems/deploy-capistrano.md -> systems/deploy-github-actions.md
source-date: 2026-03-01
```

**Attribution comes from content, not git author.** No need to carry the operator's identity into commits — "who's working on what" is derived from the *ingested material itself* (an email's sender, a doc's author), which is the better signal anyway: the person who ran the ingestion often isn't the person doing the work. So `commit.py` stays simple — default git author is fine, and there's no `WIKI_AUTHOR` mechanism to set up. When the team features are built (§12-C), capture that attribution as structured frontmatter at ingestion so it's queryable without re-reading bodies.

**What git buys immediately:** authoritative `updated_at`/`ingested_at` (derived, §3), true transaction time for the bitemporal model (§8), and the substrate the entire §12 roadmap stands on. What it does *not* capture: work that hasn't been committed — someone researching a topic they haven't written up is invisible to git.

---

## 5. Testing: TDD for scripts, evals for agents

Two layers, two instruments. Don't mix them.

**Deterministic scripts (Phases 2, 4) → TDD.** Clear inputs, exact outputs. Test-first, red-green-refactor, property tests on the two functions that carry correctness risk (§3). This layer should be near-100% covered because it *can* be.

**Agents (Phases 3, 5) → evals.** "Correct" is a judgment, so unit tests don't fit. Evals are fixture inputs with expected *properties*, run against ground truth. Principles:

1. **Structural assertions over LLM-judged ones.** Most of what matters here is checkable by plain code — does the note carry a `source_date`? Is the superseded note absent from the returned set? Did note count grow by the expected amount? Reserve an LLM judge only for genuinely fuzzy questions (is the synthesis coherent?), and treat those as the expensive minority.

2. **A hand-authored golden vault.** ~15–30 pages with known structure: a known supersession chain, a known `stable`-vs-`volatile` pair on one topic, a known duplicate trap, known typed edges, **and coverage of all four page kinds** — including ≥1 `source/` page with a real `raw/` artifact + `source:` link and ≥1 `synthesis/` page. This is ground truth — **you author it, not the agent** (see the note below). Version it; it changes rarely and deliberately.

3. **Test retrieval against the golden vault, not against your own ingestion output.** If retrieval is graded on notes ingestion produced, an ingestion bug and a retrieval bug can cancel and both show green. Isolate the two failure surfaces by giving retrieval a fixed, correct vault.

4. **Turn each design hypothesis into an eval.** The architecture makes falsifiable claims — test exactly those:
   - *Supersession:* seed a superseded fact + replacement; assert the old one isn't returned as current.
   - *Recency trap:* seed an old `stable` note and a new `volatile` note on one topic; assert the stable one isn't down-ranked.
   - *Dedup:* ingest an overlapping doc; assert note count grows by the right delta (updates, not dupes).
   - *Budget:* assert the researcher reads ≤ the stated note budget.

5. **Assert on trajectory, not just final prose.** For retrieval, "which notes did it open" and "did it stop in budget" are cheaper and more diagnostic than judging the answer text. Log the touched-note set and assert on it.

6. **Grader ≠ gradee.** If an eval uses an LLM judge, use a separate model instance (ideally a different, stronger tier) with a written rubric — never the same agent that produced the output. Better still, restructure the check to be structural so no judge is needed.

7. **Account for stochasticity.** Agent behaviour varies run to run. Run each eval N times and assert a **pass rate** (e.g. ≥ 4/5), not a single green. A flaky single-run eval is worse than none — it trains you to ignore reds.

8. **Tier by cost.** Structural checks on Haiku are cheap — run on every change. LLM-judge synthesis quality is expensive — run pre-release. Keep the cheap tier fast enough that nobody skips it.

9. **Small and sharp beats big and fuzzy.** A dozen evals that each target one designed-for property will teach you more than a sprawling suite that targets nothing in particular.

**Who writes them.** Let the coding agent write the **harness, fixtures, and structural assertions** — that's plumbing it does well. **You retain the golden vault and the property list**, because those encode intent the agent can't infer and, more importantly, must not self-certify: an agent that writes both the implementation and its success criteria will converge on criteria its implementation already meets. Owning the ground truth and the "what counts as correct" list breaks that loop while still offloading the bulk of the work. A good workflow: you write 3–4 exemplar cases by hand to pin the format and the bar, then have the agent expand coverage against the same golden vault and review the additions.

---

## 6. Cache strategy (subagent reality)

- A subagent **starts cold** and uses a **5-minute TTL** even on a subscription (the 1-hour TTL is reserved for the main conversation). Plan for cold starts.
- Prefix is ordered stable→volatile (system prompt incl. tools → project context → conversation) and **the match is exact** — any prefix change recomputes everything after it.
- **Where the index lands is the lever.** A skill invoked *normally* injects as a user message at point of invocation (conversation layer — never shared across invocations). A skill preloaded via an agent's **`skills:` field** sits ahead of the task message. Whether that caches is **undocumented — measure it** (§10). If it caches, every question within the TTL reads the index at ~10% input rate.
- For an interactive Q&A burst, **resume the same `wiki-researcher`** rather than spawning fresh — resumed subagents retain full history, so the index read is paid once.
- Don't switch model or effort mid-session; each is a full rebuild. Model is fixed per agent (we do this).

---

## 7. Model-efficiency, restated as a rule

For each capability, in order: **(1) can a Python script do it deterministically?** → no model. **(2) comprehension or judgment?** Comprehension (reading a map, following links, extracting) → Haiku. Judgment (semantic chunking, dedup, edge typing, conflict resolution) → Sonnet. **(3) Never default upward** — start at the floor, escalate only on a measured failure, and record the reason.

---

## 8. The time dimension

Three clocks must not be conflated (note-touched, wiki-learned, fact-true), or ranking misleads — a freshly ingested old paper looks new; a typo-fixed canonical note outranks everything. The schema (§3) now keeps only `source_date` (valid time) and reads the other two from git (transaction time), so the conflation is structurally impossible.

- **Decay conditionally on `volatility`** — `stable` shouldn't decay; a blanket recency prior is confidently wrong on exactly the notes that were right.
- **Conflict:** `supersedes` is a recorded fact; "newer wins" is a guess. Prefer the fact.
- **Temporal queries** ("what did we think last year") key off `source_date`.
- **Honest output** beats flat assertion: "per a note tagged `evolving`, last committed 14 months ago…" (the age now comes from git).

---

## 9. Why no MCP server

Everything here needs only Bash + filesystem + Python, so a server would add prefix tool-definition tokens, a process to manage, and — critically — **plugin subagents ignore inline `mcpServers` for security**, so a bundled server couldn't be scoped to the agent; its tools would load globally for every user. Skills + scripts keep the cost off the main thread and keep the plugin cache-neutral to install (only MCP-providing plugins invalidate the cache on enable). A genuine external integration later (a live API the filesystem can't reach) is the moment to reconsider — and it would belong outside the plugin, connected by the user, not bundled.

---

## 10. Open questions to resolve *by measurement*

1. **Index token count.** Under ~15k → read whole, retrieval design closes, spend effort on typed-edge quality. Well over → tier by folder (§6). Gates Phase 5. *(Wayfinding scope: measured against the ~15–30 note golden vault, so the index is tiny and this is nearly foregone → whole-read. Real-vault-scale tuning — folder tiering, embeddings — is out of scope for this build; see the wayfinding note up top.)*
2. **Does `skills:`-preloaded content cache across subagent invocations?** Ask the researcher one question, then a second within 5 minutes; watch `cache_creation_input_tokens` via the statusline. Determines free-per-session vs paid-per-question.
3. **Is Haiku's synthesis good enough?** Run the retrieval evals; if conflict resolution or temporal framing is weak, escalate *only the synthesis step* to Sonnet (researcher gathers on Haiku, returns the note set, main thread synthesises) rather than moving the whole agent up.

Do not build embeddings to pre-empt #1. At hundreds–1,000 curated notes, an agent reading the map *is* the semantic search, and it sees supersession and volatility that cosine similarity cannot. Embeddings earn their place only when the index no longer fits a read *and* folder tiering stops discriminating — several thousand flat notes. Revisit then.

---

## 11. Dependencies

```
# pyproject.toml (runtime)
ruamel.yaml         # round-trip frontmatter — NOT pyyaml (pyyaml coerces types / loses formatting)
markdown-it-py      # AST with source maps for position-splice link rewriting (marko is an acceptable alt; pick one)
# system
git                 # hard dependency — the vault is a repo; commit.py fails loudly if absent
# dev
pytest
hypothesis          # property tests for frontmatter round-trip + link-move invariants
```

No Node, no MCP SDK, no embedding model, no vector store. That absence is the design.

---

## 12. Roadmap (deferred — not in the core build)

Sequenced by dependency. All four lean on git (§4), which is why git is in the core build and these are not.

**A. Deterministic linter (no model).** Dead links, orphan notes, missing/invalid frontmatter, broken `supersedes` pointers, asymmetric links. *(The "tags outside the vocabulary" rule is retired — tags are emergent, not controlled; see the wayfinding note up top.)* Pure scripts — `links.py` already parses the nodes, so this is a cheap extension. Wire it as a **pre-commit hook** so a broken vault can't be committed. This is the highest-value roadmap item because it protects ingestion quality and costs no inference. Build first.

**B. Contradiction detection (rides into ingestion, Sonnet).** *Not* a batch vault scan — that's O(n²) and a bad deal. Fold it into the ingestion pass: you're already reading the overlapping notes for dedup, which is exactly when the relevant notes are in context. On a detected contradiction, **surface it to the human to resolve at ingestion time** — never auto-resolve. Recency is offered only as a *weak hint* ("the existing note is older; the incoming doc is more recent, so it's *more likely* current — but the new doc may simply contain an error"). The human always decides. This deliberately stays on the safe side of the recency trap (§8): recency informs a human's contradiction call, it never re-ranks retrieval.

**C. Team activity.** "What changed this week" is largely `git log --since='1 week ago'` over the structured commit trailers (§4) — cheap, possibly no-model if the messages are clean. **"Who's working on what" is derived from ingested content**, not git author — the sender of an email, the author of a doc. Capture that attribution as structured frontmatter (e.g. `attributed_to:`) at ingestion so the query reads the index, not note bodies. **One nuance to handle at query time:** content attribution is the *source's* author, which may be someone external (a vendor, a customer) rather than a teammate — filter to your team if the question is about internal activity. **Honest limit:** git shows what *changed*, not what's *in-flight in someone's head*; uncommitted research is invisible.

**D. Weekly manager report (Haiku).** Falls out of C: git log → structured trailers → summarize. Default to **Haiku** over a clean changelog; escalate to Sonnet only for judgment about what's *significant*. **Keep it deterministic-sourced and commit-cited** so it cannot report progress that didn't happen — a manager-facing report that hallucinates a shipped feature is the one failure mode that actually costs you. Harvest `superseded:` trailer events as the "we changed our mind about X" highlights — exactly the line items a manager wants.

**Recency-weighting note (for the record):** the only place recency legitimately enters is item B — as a hint for a human resolving a contradiction. There is no global recency weight at retrieval; the recency-trap eval (§5 case 2) is the standing guard against that regression.
