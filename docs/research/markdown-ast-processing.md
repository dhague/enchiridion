# Markdown AST parsing for position-splice body editing

Research for [#48](https://github.com/dhague/wiki-knowledge/issues/48). The question: is `mistune`'s AST mode a better fit than the current `markdown-it-py` dependency for `wikipage.py`'s job — finding headings/links/code-fences in a page body and splicing edits back into the *original source text* without disturbing any untouched byte?

Sources are primary: each library's own docs site, PyPI package metadata, and GitHub source/issues (read directly, including maintainer comments). Claims are labelled **[verified]** where read from a primary source, **[measured]** where read directly from this repo's own code, and **[inferred]** where reasoned from those.

---

## Verdict

**Keep `markdown-it-py`. Do not switch to `mistune`.** Mistune's AST mode cannot do the one thing this codebase needs it for — it does not carry source position/span information on its tokens, so there's nothing to splice against. This isn't a version-vintage gap that a newer mistune release might close: it's a **maintainer-declined feature request open since 2020** ([lepture/mistune#218](https://github.com/lepture/mistune/issues/218)), and lepture's own reason for declining is squarely relevant to this codebase's performance posture — he doesn't want to slow mistune down to carry it. When a later issue asked essentially the same question, lepture pointed the reporter away from mistune entirely, at his own new project ([lepture/mistune#402](https://github.com/lepture/mistune/issues/402)).

**Current design is already sound, and better-understood by this research than it was before**: `wikipage.py` (and its predecessor `lib/md.py`) does not actually use markdown-it-py's AST for link *positions* at all — only for **which lines are code fences** (`token.map` on `fence`/`code_block` block tokens), because markdown-it-py itself doesn't expose column-precision inline positions either. Link discovery and splicing is a hand-rolled regex (`_LINK_RE`) scoped to non-code lines. That's not a workaround to be embarrassed about; it's the correct shape given what *no* general markdown AST library in the Python ecosystem currently offers (see §3), and mistune would leave this codebase in a strictly worse position: no code-fence line-map at all, so even that part would need reinventing.

**No candidate beats the status quo.** `commonmark.py` is dead — its own PyPI page says so, recommending markdown-it-py by name. `mdformat` is real, well-built, and round-trip-safe by design, but it's a *whole-document reformatter*, not a library for surgical single-edit splicing, and it uses markdown-it-py under the hood anyway. `wenmode` — a brand-new project from mistune's own author, explicitly built to add the position tracking mistune refuses to — is the one thing worth watching, but it's six weeks old (created 2026-06-17) and still in beta; not something to depend on today.

---

## 1. What `wikipage.py` actually does with markdown-it-py today

**[measured]**, read directly from `wiki-plugin/scripts/wikipage.py` (and its predecessor `wiki-plugin/scripts/lib/md.py`, replaced in [#32](https://github.com/dhague/wiki-knowledge/issues/32) / commit `ced8ec9`, which has the same logic and a more explicit docstring about *why*):

```python
_MD = MarkdownIt("commonmark")

def _code_line_ranges(text: str) -> set[int]:
    """Return the set of 0-based line indices that fall inside code blocks."""
    lines: set[int] = set()
    for token in _MD.parse(text):
        if token.type in ("fence", "code_block") and token.map is not None:
            start, end = token.map
            lines.update(range(start, end))
    return lines
```

That's the entire surface markdown-it-py's AST is used for: parse to block tokens, read `token.map` (a `[start_line, end_line]` pair markdown-it-py attaches to **block**-level tokens) off `fence`/`code_block` tokens, and build a set of line numbers to exclude. Everything else — finding links, computing exact start/end character offsets for their destination text, splicing replacements back in — is `_LINK_RE`, a hand-written regex, applied only to lines outside that set (`lib/md.py`'s own docstring: *"Precise column offsets, which markdown-it-py does not expose for inline tokens, come from a scoped regex over the non-code lines."*).

**[verified]** This matches markdown-it-py's own documented behavior: block tokens carry a `map` (`[start_line, end_line]`), but inline tokens do not carry equivalent position data by default — the [markdown-it-py "using it" docs](https://markdown-it-py.readthedocs.io/en/latest/using.html) show inline-token examples with `map=None`, and position tracking below the block level requires the separate `SyntaxTreeNode` tree-conversion API, which this codebase doesn't use.

So the baseline mistune has to beat is narrower than "full AST-positioned link discovery" (the issue title's framing) — it's specifically **"does the AST tell me which lines are code, so a regex-based link/edit scanner can skip them."** That's a modest bar. Mistune doesn't clear it.

---

## 2. mistune's AST mode

**[verified]**, from the [official advanced-usage docs](https://mistune.lepture.com/en/latest/advanced.html): AST mode is `mistune.create_markdown(renderer='ast')` (current API, mistune 3.x — the old `renderer=mistune.AstRenderer()` shown in older issues is the pre-3.0 API). It returns a list of token dicts:

```python
{'type': 'heading', 'children': [...], 'attrs': {'level': int}}
{'type': 'block_code', 'raw': str, 'style': 'fenced', 'marker': str, 'attrs': {'info': str}}
```

Fields per token: `type` (always), `children` (nested tokens, where applicable), `raw` (unparsed content, e.g. code-fence body), `attrs` (type-specific metadata), `style`.

**Code-fence awareness: yes.** **[verified]** Fenced code block content lands in a `block_code` token's `raw` field and is not walked for nested markdown constructs — mistune does not treat link-looking text inside a fence as a real link. This part is fine and comparable to markdown-it-py's `fence` token.

**Source position/span: no, by design, and not accidentally missing.** **[verified]**, straight from mistune's own `core.py` (`BlockState`/`InlineState`): a `BlockState` does track a transient `cursor`/`cursor_max` over `src` *while parsing*, but that cursor position is never written into the emitted tokens — tokens end up as bare `{type, ...}` dicts with no line/column/offset field. This is confirmed by mistune's own issue tracker, not just an absence in the docs:

- **[verified]** [lepture/mistune#218](https://github.com/lepture/mistune/issues/218), "Parse line numbers," opened 2020-02-10, closed with no fix landed. Six different users describe needing exactly this codebase's use case — a Sphinx/docutils bridge, an LSP, in-place checkbox toggling from a rendered page (i.e. *edit-in-place by source position*, precisely this project's requirement), a linter, a link-checker. The maintainer's own reply: *"I'm not sure if it is possible to add line numbers in AstRenderer... there are other markdown parsers supporting line numbers. I would like to keep mistune faster, since adding line numbers would make the parser slower."* One commenter, **Chris Sewell — the maintainer of markdown-it-py itself** — wrote: *"Note that, I gave up waiting on this lol, and implemented markdown-it-py instead which at least captures line numbers for blocks."* That is precisely the migration this codebase already made, independently, for the same reason.
- **[verified]** [lepture/mistune#402](https://github.com/lepture/mistune/issues/402), opened later, asks for the start position of tokens to reconstruct raw source spans — the exact splice-editing use case here. lepture's only reply: *"If you want position support, please try https://wenmode.lepture.com/"* — i.e., not in mistune, use my other project instead.

**Round-tripping**: not discussed anywhere in the docs, and there's nothing to round-trip *from* — with no position data, there is no way to map an AST node back to the byte range it came from, so "parse, edit one token, splice the rest of the original source back in unchanged" is not an operation mistune's AST supports. It supports parse → render (to HTML or, via a custom renderer, to something else built from scratch), not parse → targeted in-place edit.

**Conclusion for §2's question**: mistune's AST is render-only. It is not a candidate for this codebase's splice-editing job, independent of version or configuration — the gap is structural and the maintainer has declined to close it for four-plus years running, on stated performance grounds.

---

## 3. Other candidates

### `commonmark.py` — dead, ruled out immediately

**[verified]** [PyPI's own project page](https://pypi.org/project/commonmark/) (latest 0.9.2, 2026-05-28) carries the banner: *"commonmark.py is now deprecated. We recommend using markdown-it-py for a commonmark parser going forward."* Not a candidate.

### `mdformat` — the closest thing to "structural, round-trip-safe editing" that exists, and still the wrong shape

**[verified]** [PyPI](https://pypi.org/project/mdformat/) (latest 1.0.0, 2025-10-16): *"an opinionated Markdown formatter"*, usable as both a CLI and a Python library, built on **markdown-it-py** as its parser. It is explicitly round-trip-safe in the sense that matters to formatters: parse → reformat → the output is a canonical, stable rendering of the same semantic content.

That's a different guarantee than this codebase needs. `mdformat`'s round-trip promise is *"reformats to one canonical style, idempotently"* — it is a whole-document rewriter. This codebase's requirement (stated in `wikipage.py`'s own docstring and enforced by property tests) is **byte-for-byte preservation of every region the edit didn't touch** — `_quote_links`, the frontmatter's pinned `indent(mapping=2, sequence=4, offset=2)`, and `_rewrite_text`'s back-to-front offset splicing all exist specifically so an edit to one link or one frontmatter key never reformats anything else. Feeding a page through `mdformat` to change one link would rewrite line wraps, list markers, emphasis characters, etc. across the whole document — the opposite of what's wanted. It's evidence that markdown-it-py is the right *parser* choice (a second, independent, actively-maintained project reached for it for a similar structural-fidelity problem), not evidence to add `mdformat` itself as a dependency.

### `wenmode` — worth watching, not worth depending on yet

**[verified]** [wenmode.lepture.com](https://wenmode.lepture.com/) and its [PyPI page](https://pypi.org/project/wenmode/): a new project *from mistune's own author*, explicitly pitched as mistune "redesigned for applications that need to own the full Markdown pipeline," producing `mdast`-compatible nodes, with a documented `positions=True` option: *"Set positions=True to include source ranges for editor integration, diagnostics, or AST-based tooling."* This is a direct, deliberate answer to the #218/#402 gap — built as a separate project rather than retrofitted into mistune.

**[verified]** GitHub repo metadata (`lepture/wenmode`): created **2026-06-17**, i.e. about six weeks old at the time of this research; latest PyPI release **0.13.1** (2026-07-27, the day before this research); PyPI classifies it **"Development Status :: 4 - Beta"**; 20 releases in that month suggest active but very early churn; 22 GitHub stars. Requires Python ≥3.10.

Genuinely the most interesting result of this research, but not actionable now: a beta project six weeks old, with a release cadence suggesting the API is still moving, is not something to hang a "hard requirement" (byte-for-byte preservation, per this codebase's own ADR-0005 test discipline) on. **Worth a calendar reminder to re-check in 6–12 months once it has a 1.0 and a stable position-object schema**, not worth adopting or even prototyping against today.

### Python-Markdown (`markdown`) with an AST extension, `panflute`/pandoc-based approaches

Not pursued in depth — briefly: Python-Markdown's extension API (`Treeprocessor`) exposes an `xml.etree` tree, not source positions either, and adding it as a second markdown implementation alongside markdown-it-py (already a dependency, already proven for the code-fence job) for no positional capability gain isn't a serious contender. Pandoc/`panflute` is a much heavier dependency (a Haskell binary) for the same shortfall — pandoc's own AST is also not designed for splice-back-into-original-bytes editing. Neither changes the verdict.

---

## 4. Recommendation

**No change to `wiki-plugin/pyproject.toml`.** `markdown-it-py` stays the sole markdown dependency (alongside `ruamel.yaml` for frontmatter, which already does the real AST/round-trip job well, per `CLAUDE.md`'s own framing of this codebase).

Concretely:

1. **Do not add `mistune`.** Its AST carries no source position data, this is a known and maintainer-declined gap (not a bug that a version bump fixes), and it would not even replace markdown-it-py's narrow current job (code-fence line detection) without inventing that capability from scratch on top of `raw`/cursor internals mistune doesn't expose publicly.
2. **The regex-plus-block-AST hybrid in `wikipage.py` is the correct architecture, not a stopgap.** No library surveyed here — mistune, markdown-it-py's own inline layer, Python-Markdown — exposes column-precise inline-element source spans. `SyntaxTreeNode` (markdown-it-py's tree API) is the one avenue not currently used that could plausibly extend `token.map`-style coverage further into nested block structures (e.g. list items, blockquotes) if the code-fence detector ever needs to reason about nesting it doesn't today; that's a possible narrow follow-up, not a library swap.
3. **Revisit if `wenmode` reaches a stable 1.0 with a settled `positions` schema.** If it does, it's a genuine candidate specifically *because* it was built to close the exact gap this research identifies — evaluate it then against the same bar (does splicing an edit back into untouched source, byte-for-byte, actually work end-to-end), not against docs-page claims alone.
4. **No action needed on issue #48 beyond recording this finding and closing it** — there's no follow-up ticket to spin out, since the recommendation is "keep the current dependency," not "adopt something new."

---

## Appendix: sources

**mistune** (primary): [Advanced usage / AST mode docs](https://mistune.lepture.com/en/latest/advanced.html) · [Guide](https://mistune.lepture.com/en/latest/guide.html) · [`core.py` source](https://raw.githubusercontent.com/lepture/mistune/master/src/mistune/core.py) (`BlockState`/`InlineState`) · [PyPI](https://pypi.org/project/mistune/) (3.3.4, 2026-07-22) · [lepture/mistune#218 "Parse line numbers"](https://github.com/lepture/mistune/issues/218) (opened 2020-02-10, closed unresolved; maintainer + 6 other users' comments) · [lepture/mistune#402](https://github.com/lepture/mistune/issues/402) (maintainer redirects to wenmode)

**markdown-it-py** (primary, the current dependency): [`using.html` docs](https://markdown-it-py.readthedocs.io/en/latest/using.html) (`token.map`, `SyntaxTreeNode`) · [PyPI](https://pypi.org/project/markdown-it-py/) (4.2.0, 2026-05-07, no hard dependencies, maintained by Chris Sewell)

**Other candidates** (primary): [`commonmark` PyPI](https://pypi.org/project/commonmark/) (0.9.2, deprecated, points to markdown-it-py) · [`mdformat` PyPI](https://pypi.org/project/mdformat/) (1.0.0, 2025-10-16) · [`mdformat` intro docs](https://mdformat.readthedocs.io/en/stable/users/introduction.html) · [wenmode.lepture.com](https://wenmode.lepture.com/) · [`wenmode` PyPI](https://pypi.org/project/wenmode/) (0.13.1, 2026-07-27, Beta) · [`lepture/wenmode` GitHub repo metadata](https://github.com/lepture/wenmode) (created 2026-06-17)

**This repo**: `wiki-plugin/scripts/wikipage.py` · `wiki-plugin/scripts/lib/md.py` (predecessor, read via `git show ced8ec9^:wiki-plugin/scripts/lib/md.py`) · `wiki-plugin/pyproject.toml` · commit `ced8ec9` ("Build WikiPage/Vault library, replacing frontmatter.py/links.py/lib/md.py (#32)") · `CLAUDE.md`
