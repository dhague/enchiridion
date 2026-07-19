# CLAUDE.md

## Agent skills

### Issue tracker

Issues live in this repo's GitHub Issues (github.com/dhague/enchiridion), using the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical five-role vocabulary, each label string equal to its role name. See `docs/agents/triage-labels.md`. **Not all five exist yet in the tracker** — `docs/agents/triage-labels.md` documents the mapping as if the labels are already created, but as of 2026-07-19 only `ready-for-agent` exists (created ad hoc when first needed). Before applying `needs-triage`, `needs-info`, or `ready-for-human`, check `gh label list` and create the label first if missing.

### Domain docs

Single-context — one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Project: the wiki-knowledge plugin

This repo builds a Claude Code **wiki-knowledge plugin** (clean-room ingestion + retrieval over a git-backed markdown vault). There is **no separate planning doc** — `wiki-plugin-implementation-plan.md` has been retired, and its content is split across:

- **`CONTEXT.md`** — the domain glossary (page, kind, vault, typed edge, volatility, golden vault, deployment mode, …). Read this for vocabulary before writing anything that names a domain concept.
- **`docs/adr/`** — architectural decisions with their rationale (no MCP server, no embeddings, attribution from ingested content not git identity, the two deployment modes + vault-root resolution order, TDD-for-scripts/evals-for-agents). Read these before assuming *why* something is built the way it is.
- **`wiki-plugin/skills/wiki-conventions/SKILL.md`** — the authoritative **frontmatter schema, folder structure, relative-markdown link rules, and typed-edge vocabulary** (`refines`/`contradicts`/`example-of`/`source`/`related`). Written in #6. Non-obvious shape (a post-resolution amendment — see the [#6](https://github.com/dhague/enchiridion/issues/6) comments): typed edges **and** `supersedes` are each **one YAML key per relation, holding a list of clickable relative-markdown links**; the raw-artifact pointer is **`raw_source:`** (a single markdown link, title = filename) — renamed from `source:` so it can't collide with the `source` edge key.
- **GitHub issues** — the wayfinder map [Wiki Knowledge Plugin (#1)](https://github.com/dhague/enchiridion/issues/1) tracks phases as child tickets; each resolution is recorded in the map's *Decisions so far*, and *Not yet specified* / *Out of scope* track remaining and deferred work (including the old plan's §12 roadmap items). Before assuming how something works, check the map first.

If you're tempted to write a new standalone design doc for this project, don't — extend one of the four above instead, so there's never a second version of the truth to keep in sync.

- **Terminology: the wiki's units are "pages", not "notes".** Firm preference, standardized in `CONTEXT.md` and the conventions spec (#6). "Note" may still appear in old ticket prose — same thing.
- **Folder structure is plugin-fixed and kind-axed** (`wiki/{concept,entity,source,synthesis}/` + a user-extensible `raw/` inbox), decided in #5. Don't reinvent it.
- **Model assignment is a standing convention, not re-litigated per ticket:** deterministic work is a Python script (no model), comprehension (map-reading, link-following) is Haiku, judgment (semantic chunking, dedup, edge-typing, conflict resolution) is Sonnet. Start at the floor for any new capability and escalate only on a measured failure — see #11 for the current ingestion/retrieval assignment.
- **The plugin lives at `wiki-plugin/`** (repo root), scaffolded in #3. The plugin **`name` is `wiki-knowledge`** (in `.claude-plugin/plugin.json`) — deliberately different from the `wiki-plugin/` directory name. Empty dirs hold a `.gitkeep`; each fills in a later phase. **Everything under `wiki-plugin/` is implementation, not documentation** — don't treat its README or code comments as a substitute for `CONTEXT.md`/`docs/adr/`/the conventions spec.
- **The deterministic script layer is built and test-first.** `wiki-plugin/scripts/` holds `vault.py` (root resolution), `lib/md.py` (frontmatter split + AST-positioned link discovery), `frontmatter.py` (ruamel round-trip get/set), `links.py` (position-splice move/rename), `commit.py` (structured git commit per ingestion/edit — see [ADR-0003](docs/adr/0003-attribution-from-content-not-git-identity.md)) — built in #4 (`8f6d93d`) and reconciled to the frontmatter amendment in #9 (`f4f0cb2`). `normalize_raw.py` ([#10](https://github.com/dhague/enchiridion/issues/10)) and `build_index.py` ([#8](https://github.com/dhague/enchiridion/issues/8)) are **not yet built**.
  - **Dev setup / running tests:** deps live in `wiki-plugin/.venv` (gitignored), installed **directly** (`ruamel.yaml markdown-it-py pytest hypothesis`) — **not** `pip install -e .` (no package-discovery config, so editable install fails). Run the suite from `wiki-plugin/`: `.venv/Scripts/python.exe -m pytest`. `pyproject.toml` sets `pythonpath = ["scripts"]`, so tests import scripts flat (`import vault`, `from lib import md`).
  - **Two contracts a future edit must not break** (both property-tested): `frontmatter.py` pins ruamel `indent(mapping=2, sequence=4, offset=2)` so the spec's 2-space edge-list indentation round-trips **byte-for-byte**, and only the frontmatter block is ever re-serialised (body spliced back verbatim). `links.py` / `lib.md.iter_links` splice links across the **whole document**, so frontmatter markdown links (typed edges, `supersedes`, `raw_source`) are rewritten on move by the same rule as body links — deliberate and tested, not incidental.
- **Git hygiene:** the repo sits inside a Resilio Sync folder, so `git status` routinely shows Resilio temp files (`*.rsls`) and a `.claude/worktrees/` dir — **never stage these**; add paths explicitly rather than `git add -A`. The repo's `temp/` dir is scratch space (handoff docs, etc.) and is **not** gitignored — don't stage it unless asked. Commits go to `main` (solo repo); the harness will branch-first when asked to commit, then merge/delete on request.
- **Phase 3 (ingestion) + Phase 5 (retrieval) are specced, not yet built.** [#11](https://github.com/dhague/enchiridion/issues/11) is the `ready-for-agent` spec for both `wiki-ingest` (Sonnet) and `wiki-researcher` (Haiku) — read it first for scope/acceptance shape. `normalize_raw.py` ([#10](https://github.com/dhague/enchiridion/issues/10)) and `build_index.py` ([#8](https://github.com/dhague/enchiridion/issues/8)) are dependencies of #11, tracked separately, still open.
- **This project's tickets don't map 1:1 onto `/to-spec`'s default assumption of "one undiscussed feature per invocation."** It's wayfinder-mapped (phases charted as child tickets off #1), so when `/to-spec` is invoked without an explicit feature already established in conversation, expect to need a scope-clarifying question (which phase(s)?) before drafting — confirmed acceptable in [#11](https://github.com/dhague/enchiridion/issues/11)'s drafting session rather than skipped.
