"""Shared markdown primitive: frontmatter split + AST-positioned link discovery.

Two jobs, both consumed by the rest of the deterministic layer:

* :func:`split_frontmatter` — peel a leading YAML frontmatter block off a
  document, returning the raw block, the body, and the body's char offset.
* link discovery — :func:`parse`, :func:`code_line_ranges`, and
  :func:`iter_links` locate every markdown link/image in a body **with exact
  source offsets**, so :mod:`links` can splice replacements into the raw source
  back-to-front without ever round-tripping through a stringifier (which would
  reformat untouched text).

The AST (markdown-it-py tokens, which carry a line ``map``) is used to know
*which lines are code* — so link-looking text inside a fenced block is never
rewritten. Precise column offsets, which markdown-it-py does not expose for
inline tokens, come from a scoped regex over the non-code lines.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark")

# Frontmatter is a `---` fence on the VERY FIRST line, closed by the next `---`
# line. Anything else (a `---` mid-document) is a thematic break, not metadata.
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?\n)?---[ \t]*(?:\n|\Z)", re.DOTALL)

# A markdown inline link or image: `[label](dest ...)` / `![label](dest ...)`.
# `label` tolerates one level of nested brackets (e.g. an image inside a link).
# `dest` is either `<...>` or a run without whitespace/`)`; an optional title
# (quoted or parenthesised) after the dest is matched but excluded from `dest`.
_LINK_RE = re.compile(
    r"""
    (?P<img>!?)
    \[ (?P<label> (?: [^\[\]] | \[[^\[\]]*\] )* ) \]
    \(
        [ \t]*
        (?P<dest> < [^<>\n]* > | [^)\s]* )
        (?: [ \t]+ (?: "[^"]*" | '[^']*' | \([^)]*\) ) )?
        [ \t]*
    \)
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class LinkMatch:
    """One link/image occurrence, positioned in the source body.

    ``start``/``end`` bracket the *destination path only* (angle brackets and
    any title excluded), so ``body[start:end] == dest``. ``line`` is the
    0-based line the destination sits on.
    """

    start: int
    end: int
    dest: str
    is_image: bool
    line: int


def split_frontmatter(text: str) -> tuple[str | None, str, int]:
    """Split a leading YAML frontmatter block off ``text``.

    Returns ``(frontmatter, body, body_offset)``. When there is no frontmatter,
    ``frontmatter`` is ``None``, ``body`` is ``text`` unchanged and
    ``body_offset`` is 0. ``text[body_offset:] == body`` always holds.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None, text, 0
    frontmatter = m.group(1) or ""
    body_offset = m.end()
    return frontmatter, text[body_offset:], body_offset


def parse(text: str):
    """Parse ``text`` to markdown-it tokens (block tokens carry a line ``map``)."""
    return _md.parse(text)


def code_line_ranges(text: str) -> set[int]:
    """Return the set of 0-based line indices that fall inside code blocks.

    Derived from the AST: fenced (``fence``) and indented (``code_block``)
    blocks expose their line span via ``token.map``. Callers use this to skip
    link-looking text that is really code.
    """
    lines: set[int] = set()
    for token in _md.parse(text):
        if token.type in ("fence", "code_block") and token.map is not None:
            start, end = token.map
            lines.update(range(start, end))
    return lines


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset)


def iter_links(text: str):
    """Yield a :class:`LinkMatch` for every link/image in ``text``, in order.

    Occurrences inside fenced/indented code blocks are skipped. Offsets are
    absolute into ``text`` and point at the destination path itself.
    """
    code_lines = code_line_ranges(text)
    for m in _LINK_RE.finditer(text):
        start, end = m.span("dest")
        dest = m.group("dest")
        # Unwrap an angle-bracketed destination: `<path>` -> `path`.
        if dest.startswith("<") and dest.endswith(">"):
            start, end = start + 1, end - 1
            dest = dest[1:-1]
        line = _line_of(text, start)
        if line in code_lines:
            continue
        yield LinkMatch(
            start=start,
            end=end,
            dest=dest,
            is_image=bool(m.group("img")),
            line=line,
        )
