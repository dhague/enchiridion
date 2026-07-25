# Handoff — reconcile closed ticket #4 with the frontmatter amendment

**Date:** 2026-07-18
**For:** the next session (likely a `/wayfinder` session on the [Wiki Knowledge Plugin map (#1)](https://github.com/dhague/enchiridion/issues/1))
**Focus (from the invoking user):** *"wayfinder thinks it finished #4 but needs to take into account these changes."*

## TL;DR

[#4 — Python script layer](https://github.com/dhague/enchiridion/issues/4) is **closed** (delivered in commit `8f6d93d`). That commit landed **before** this session amended the frontmatter schema (commit `19be866`). So the delivered scripts + tests were written against the **old** frontmatter shape. The schema is now different in ways that touch `links.py`, `normalize_raw.py` (not yet built), and every test fixture. #4 needs re-verification and top-up work, or a follow-up ticket — it is **not** actually done against the current contract.

## What changed in the frontmatter (this session)

Full detail lives in the artifacts — do not re-derive:
- Spec: [`wiki-plugin/skills/wiki-conventions/SKILL.md`](../wiki-plugin/skills/wiki-conventions/SKILL.md)
- Plan §3 mirror: `wiki-plugin-implementation-plan.md`
- Amendment record: comment on [#6](https://github.com/dhague/enchiridion/issues/6#issuecomment-5013030223)
- Diff: `git show 19be866`

Summary of the shape change (old → new):
- **Typed edges:** `links: [{to, type}]` list → **one YAML key per edge type** (`refines`/`contradicts`/`example-of`/`source`/`related`), each an optional **list of quoted relative-markdown links** (`- "[title](../concept/x.md)"`). Vocabulary unchanged.
- **`supersedes`:** bare-path list → **list of quoted markdown links**.
- **Raw-artifact pointer:** field renamed **`source:` → `raw_source:`** (to avoid clashing with the promoted `source` edge key) and its value is now a **single quoted markdown link**, title = the artifact's filename, pointing into `raw/`.

## Why this breaks / stresses #4

The delivered code is at commit `8f6d93d`; relevant files: `wiki-plugin/scripts/{vault,frontmatter,links,commit}.py`, `wiki-plugin/scripts/lib/md.py`, and `wiki-plugin/tests/test_*.py`.

1. **`links.py` frontmatter handling is now load-bearing but untested.**
   `links.py` finds links via `lib/md.py::iter_links`, which runs `_LINK_RE.finditer` over the **entire document** (frontmatter + body), skipping only fenced/indented code lines. Under the old schema, frontmatter contained **no** `[](…)` links, so this never mattered. Now frontmatter is full of markdown links, and `iter_links` will **incidentally** match and rewrite them on a move. That is probably *desirable* (inbound edges in other pages' frontmatter, and outbound links in a moved page's frontmatter, get fixed) — **but it is accidental, not designed, and there is no test for it.** Verify it actually holds, then make it deliberate (or explicitly scope it).
   - Confirm the move-invariant for **inbound** frontmatter edges: page B has `refines:\n  - "[A](../concept/a.md)"`; move A; assert B's edge is rewritten and still resolves.
   - Confirm **outbound** frontmatter links (incl. `raw_source` and `supersedes`) in the moved page are rewritten.
   - Confirm **`raw_source`** — target is under `raw/` and generally **not** a `.md` file. `apply_move` only rglobs `*.md`, and rewrites a link when its resolved target matches the move. Moving a `source/` page must keep its `raw_source` link pointing at the (unmoved) raw artifact from the new directory. Test this specific case; watch for `posixpath.relpath` correctness across the `wiki/ ↔ raw/` sibling boundary.

2. **`normalize_raw.py` is still not built.** It was handed to the script layer by [#5](https://github.com/dhague/enchiridion/issues/5) but is absent from #4's delivery (not in `wiki-plugin/scripts/`). Its job: rename a raw file to `YYYY-MM-DD-hhmm-…`, content-immutable, and drive `links.py` so the **`raw_source` markdown link** in the corresponding `source/` page follows the rename. It must now rewrite a **markdown link**, not a bare path. This is net-new work (a fog item, or a scope gap in #4 — decide which).

3. **Every test fixture predates the new shape.** `test_frontmatter.py`, `test_links.py`, `test_md.py` exercise the old schema. The two property tests (frontmatter round-trip byte-identity; move leaves only link-bearing lines changed) will **pass without exercising the new frontmatter links** unless fixtures are updated to include them. Add fixtures with `raw_source`, `supersedes`, and per-type edge keys.

4. **`commit.py` — check the `superseded:` trailer.** Trailers are manifest-driven (not read from frontmatter), so this is likely fine, but confirm nothing parses `supersedes` as bare paths now that it holds markdown links.

## Cross-ticket ripple (not #4's job, but flag on the map)

- **`build_index.py` ([#8](https://github.com/dhague/enchiridion/issues/8), not built, blocked by #4):** must read edges from the **per-type keys** (not `links:`) and **strip the markdown** to recover target paths for the index. Note this on #8 before it's worked.
- **Ingestion agent (Phase 3, still fogged):** must write the new per-key markdown-link frontmatter and set `raw_source` as a single filename-titled link.
- Any consumer calling `frontmatter.get(…, "supersedes")` or an edge key now receives **markdown-link strings**, not bare paths.

## Suggested next actions

1. **Decide the map bookkeeping** (this is a `/wayfinder` call): #4 is closed but incomplete against the amended contract. Either **reopen #4** with a short "reconcile with `19be866`" scope, or **create a follow-up child ticket** ("Reconcile script layer with per-key markdown-link frontmatter + build `normalize_raw.py`") and wire it. A follow-up ticket is cleaner (closed tickets record the route walked). Update the map's Decisions/fog accordingly.
2. **Work the reconciliation test-first** — add the failing frontmatter-link fixtures/tests described above, then make `links.py` (and `normalize_raw.py`) satisfy them.
3. **Verify end-to-end** on a fixture vault that contains at least one `source/` page (with `raw_source`), one `synthesis/` page (with `source` edges), and a supersession pair — then move/rename pages and confirm all frontmatter + body links still resolve.

## Suggested skills

- **`/wayfinder`** (arg: the map `#1`, or `#4`) — to make the map decision in action 1 above (reopen vs. follow-up ticket) and record it. This handoff's whole premise is a wayfinder map that mis-marked #4 done.
- **`/tdd`** — the reconciliation is a deterministic-script change; drive it red→green→refactor, extending the two existing property tests to the new frontmatter links.
- **`/verify`** — exercise `links.py` move/rename against a fixture vault with the new frontmatter, observing that inbound frontmatter edges and `raw_source` both survive.
- **`/code-review`** (optional) — review commit `8f6d93d` against the amended [`SKILL.md`](../wiki-plugin/skills/wiki-conventions/SKILL.md) to catch any other old-schema assumptions before topping up.

## Repo state at handoff

- Branch `main`. Commits this session: `4583e87` (spec), `dee7744` (plan pointer), `8f6d93d` (#4 script layer — *by the parallel #4 session*), `19be866` (frontmatter amendment). **None pushed.**
- Untracked/unrelated, leave alone: a pre-existing **unstaged** modification to `wiki-plugin/pyproject.toml` (not this work); a Resilio Sync temp file `wiki-plugin-implementation-plan.md.tmp.*.rsls`; `.claude/worktrees/`.
- Tracker: GitHub Issues via `gh` (see `docs/agents/issue-tracker.md`; "Wayfinding operations" section). Map = [#1](https://github.com/dhague/enchiridion/issues/1); [#6](https://github.com/dhague/enchiridion/issues/6) closed with the amendment comment; [#8](https://github.com/dhague/enchiridion/issues/8) open (blocked by #4).
