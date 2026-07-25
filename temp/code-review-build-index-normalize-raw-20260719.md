# Code review: build_index.py / normalize_raw.py

**Fixed point:** `0013afa` → `HEAD` (`git diff 0013afa...HEAD`)
**Commits:**
- `825935d` Build build_index.py — generate wiki/_index.md from page frontmatter (TDD) — spec: [#8](https://github.com/dhague/enchiridion/issues/8)
- `110eaa4` Build normalize_raw.py — raw rename drives raw_source rewrite (TDD) — spec: [#10](https://github.com/dhague/enchiridion/issues/10)

## Standards

Checked against `CLAUDE.md`, `docs/adr/0005-tdd-for-scripts-evals-for-agents.md`, `wiki-plugin/skills/wiki-conventions/SKILL.md`, and the existing script-layer style (`vault.py`, `lib/md.py`, `links.py`, `commit.py`). No lint tooling configured in this repo.

**No hard violations.** The two pinned `frontmatter.py` contracts (byte-for-byte round-trip, frontmatter-only re-serialization) are untouched — the new `load()` only reads via the existing tested `_load`. ADR-0005's hypothesis-property-test requirement is correctly scoped to frontmatter round-trip and link-splicing only; neither new file needed one. Terminology, folder singular/plural rules, and the `raw_source` single-link-vs-list-edges distinction all check out. `_index.md` generation correctly walks `wiki/**` only.

**Judgement-call smells (not violations):**
1. **Duplicated Code** — `normalize_raw.py`'s `_load_wiki_pages` and `build_index.py`'s inline walk in `write_index` are both new near-copies of the walk-and-read-into-`{rel:text}` loop already in `links.py::apply_move`. Three variants of the same shape now exist in the layer.
2. **Divergent from the layer's own pattern** — `normalize_raw.py`'s `_RAW_SOURCE_RE` rewrites the `raw_source` label with a bare regex over raw text, with no code-fence exclusion — unlike `lib/md.py`, whose whole purpose is avoiding exactly that class of false positive. Low practical risk, but a parallel, less-safe mechanism next to the hardened one.
3. **Minor Primitive Obsession** — `PageRecord.edges: list[tuple[str, list[str]]]`, an anonymous pair unpacked positionally in `_render_row`. Cosmetic.

## Spec

Verified against issues #8, #10 (plus the schema-ripple amendment in #8's comments) and the actual code/tests, not just the resolution-comment claims. Suite run: 1 failed, 87 passed — the failure is the pre-existing, unrelated hypothesis-strategy bug already filed separately as [#12](https://github.com/dhague/enchiridion/issues/12), not a regression from this diff.

**(a) Missing/partial:** `/wiki-reindex` wiring and "runs as last ingestion step" (#8) aren't implemented — but the resolution comment discloses this itself, and it's legitimately deferred to #11 (not yet built). Not a defect.

**(b) Scope creep:** None. The GFM table format, edges-cell ordering, `_fix_raw_source_label`, and the `when`→mtime fallback are all either directly required by the ticket text (the amendment explicitly requires the link title to update alongside the path) or well-justified judgment calls documented for future use (#11).

**(c) Claims verified against code:** `_index.md` self-exclusion, `wiki/**`-only walk (raw/ fixture proves exclusion), path re-basing math (hand-verified both typed-edge and wiki-boundary cases), byte-identical assertion, unconditional idempotency (test uses an absurd future `when` to prove existing prefix wins), and CLI single-file-vs-scan branching — all confirmed correct in the actual code.

No incorrect-but-claimed-correct behavior found.

## Summary

- **Standards:** 0 hard violations, 3 judgement-call smells (worst: duplicated file-walk shape across three sites in the script layer).
- **Spec:** 0 missing requirements (one deferral, correctly disclosed and out-of-scope-for-now), 0 scope creep, 0 wrong-but-claimed-correct implementations.
