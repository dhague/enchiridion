"""WikiPage — the page model, pure-functional.

No I/O at all: frontmatter get/set/merge, body access, and outbound-link
move-planning. Text in, a new :class:`WikiPage` out, never a mutation in
place. :func:`plan_move` is the same over a whole ``{rel: text}`` vault.

:class:`vault.Vault` owns every read and write. The dependency runs one way,
``vault -> wikipage``, with no deferred import on either side — and that
holds for the CLI below, whose subcommands all take a *file path* and never
resolve a vault root. Moving a page is ``vault.py move`` for exactly that
reason: it's the one operation needing the whole vault.

**Two byte-preservation contracts, both property-tested — don't break
either.** (1) Only the frontmatter block is re-serialised, via a ruamel
round-trip pinned to ``indent(mapping=2, sequence=4, offset=2)`` so the
spec's edge-list indentation survives; the body is spliced back verbatim.
(2) Link rewriting never round-trips the document through a stringifier:
destinations are spliced into the raw text back-to-front by exact source
offset, so every untouched byte survives — including frontmatter links,
which the same whole-document scan finds.

CLI::

    python wikipage.py get <file> <key>
    python wikipage.py set <file> <key> <value> [--json]
    python wikipage.py merge <file> <key> <json-list>
"""
from __future__ import annotations

import posixpath
import re
import sys
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from urllib.parse import unquote

from markdown_it import MarkdownIt
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

_MD = MarkdownIt("commonmark")

# Frontmatter is a `---` fence on the VERY FIRST line, closed by the next `---`
# line. Anything else (a `---` mid-document) is a thematic break, not metadata.
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?\n)?---[ \t]*(?:\n|\Z)", re.DOTALL)

# The minimal charset that makes a raw/ filename linkable. Everything else —
# unicode, &, ', ,, + — stays literal, deliberately.
_ENCODE_CHARS = {" ", "#", "%", "(", ")", "<", ">"}


def percent_encode(path: str) -> str:
    """Percent-encode :data:`_ENCODE_CHARS` in a path; all else stays literal."""
    return "".join(f"%{ord(c):02X}" if c in _ENCODE_CHARS else c for c in path)


def percent_decode(path: str) -> str:
    """Percent-decode a path encoded by percent_encode()."""
    return unquote(path)


def split_dest(dest: str) -> tuple[str, str]:
    """Split an encoded link destination into ``(decoded_path, decoded_anchor)``.

    **Order matters:** split on the literal ``#`` first, decode each half
    after. Decoding up front would turn an encoded ``#`` in a filename
    (``%23``) into a false anchor separator. This is the single decode
    boundary — callers get decoded strings and never redo the split.
    """
    encoded_path, sep, encoded_anchor = dest.partition("#")
    path = percent_decode(encoded_path)
    anchor = percent_decode(encoded_anchor) if sep else ""
    return path, anchor

def _nested_paren_dest(depth: int) -> str:
    """Build a regex fragment for an unbracketed link destination.

    Per CommonMark, a destination without `<>` ends at the first
    *unbalanced* `)` — `(draft)` inside one doesn't terminate it. `re` has no
    recursion, so nesting is bounded at ``depth`` levels: plenty for a real
    filename or URL, and cheaper than a `regex` package dependency.
    """
    frag = r"[^()\s]*"
    for _ in range(depth):
        frag = rf"(?:[^()\s]|\({frag}\))*"
    return frag


# A markdown inline link or image: `[label](dest ...)` / `![label](dest ...)`.
# `label` tolerates one level of nested brackets (e.g. an image inside a link).
# `dest` is either `<...>` or a whitespace-free run that may contain balanced
# parens (`_nested_paren_dest`); an optional title (quoted or parenthesised)
# after the dest is matched but excluded from `dest`.
_LINK_RE = re.compile(
    rf"""
    (?P<img>!?)
    \[ (?P<label> (?: [^\[\]] | \[[^\[\]]*\] )* ) \]
    \(
        [ \t]*
        (?P<dest> < [^<>\n]* > | {_nested_paren_dest(4)} )
        (?: [ \t]+ (?: "[^"]*" | '[^']*' | \([^)]*\) ) )?
        [ \t]*
    \)
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class LinkMatch:
    """One link/image occurrence, positioned in the source text.

    ``start``/``end`` bracket the *encoded* destination only — angle brackets
    and any title excluded — so ``text[start:end] == dest`` always holds.
    ``line`` is 0-based.
    """

    start: int
    end: int
    dest: str  # the encoded destination (text[start:end] == dest, invariant)
    decoded_path: str  # split_dest(dest)[0] — decoded, anchor-free, for logic
    decoded_anchor: str  # split_dest(dest)[1] — decoded, "" if no anchor
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


def _code_line_ranges(text: str) -> set[int]:
    """Return the set of 0-based line indices that fall inside code blocks."""
    lines: set[int] = set()
    for token in _MD.parse(text):
        if token.type in ("fence", "code_block") and token.map is not None:
            start, end = token.map
            lines.update(range(start, end))
    return lines


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset)


def iter_links(text: str):
    """Yield a :class:`LinkMatch` for every link/image in ``text``, in order.

    Occurrences inside fenced/indented code blocks are skipped. Offsets are
    absolute into ``text``. Scans the *whole* document, frontmatter included,
    so typed edges, ``supersedes`` and ``raw_source`` are found by the same
    rule as body links.
    """
    code_lines = _code_line_ranges(text)
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
        decoded_path, decoded_anchor = split_dest(dest)
        yield LinkMatch(
            start=start, end=end, dest=dest,
            decoded_path=decoded_path, decoded_anchor=decoded_anchor,
            is_image=bool(m.group("img")), line=line
        )


def link_dest(link: str) -> str | None:
    """Extract a whole markdown-link scalar's destination, decoded.

    ``link`` is a full ``"[label](dest)"`` (or image) scalar, as stored in
    frontmatter or found in body text — not a bare destination. ``None``
    when ``link`` isn't a markdown link at all.
    """
    match = next(iter(iter_links(link)), None)
    return match.decoded_path if match is not None else None


def resolve_link_dest(dest: str, page_dir: str, prefix: str = "wiki") -> str:
    """Resolve an already-decoded link destination to a normalized path.

    ``page_dir`` is the directory the link lives in. Default
    ``prefix="wiki"`` suits a wiki-root-relative ``page_dir``
    (``page_record.py``'s ``rel`` convention); pass ``prefix=""`` when
    ``page_dir`` is already vault-relative. The one place that owns these
    ``normpath``/``join`` quirks — don't reimplement it at a call site.
    """
    base = posixpath.join(prefix, page_dir) if prefix else (page_dir or ".")
    return posixpath.normpath(posixpath.join(base or ".", dest))


def compose_link(title: str, target_rel: str, page_dir: str) -> str:
    """Compose a markdown link to ``target_rel`` from a page in ``page_dir``.

    Both are vault-relative (``"wiki/concept/foo.md"`` /
    ``"wiki/synthesis"``); ``page_dir`` may be ``""`` for a page at the vault
    root. Relativises the target and percent-encodes the destination — never
    the label. YAML quoting is not done here: :meth:`WikiPage.set`/``merge``
    already double-quote a fresh ``[...]`` scalar.
    """
    dest = posixpath.relpath(posixpath.normpath(target_rel), page_dir or ".")
    return f"[{title}]({percent_encode(dest)})"


def _is_relative_dest(path: str) -> bool:
    """True when ``path`` (the pre-anchor part) is a vault-relative reference.

    Excludes the empty destination, absolute paths, bare anchors, and any
    scheme-qualified URL.
    """
    return bool(path) and not path.startswith(("/", "#")) and "://" not in path


def _rewrite_text(text: str, file_rel: str, old_rel: str, new_rel: str) -> str:
    """Return ``text`` with its links fixed for the move ``old_rel -> new_rel``.

    ``file_rel`` is where *this* file sits before the move; only the moved
    file itself (``file_rel == old_rel``) also changes its own location, so
    where it ends up is derived rather than passed in.
    """
    is_moved_file = file_rel == old_rel
    old_dir = posixpath.dirname(file_rel)
    new_dir = posixpath.dirname(new_rel if is_moved_file else file_rel)

    edits: list[tuple[int, int, str]] = []
    for lk in iter_links(text):
        path, anchor = lk.decoded_path, lk.decoded_anchor

        if not _is_relative_dest(path):
            continue
        # Where this link pointed, resolved from the file's original location.
        target = posixpath.normpath(posixpath.join(old_dir or ".", path))
        # For pages other than the moved one, only links at the moved page change.
        if not is_moved_file and target != old_rel:
            continue
        # The moved page itself relocates the target of a self-link.
        moved_target = new_rel if target == old_rel else target
        new_path = posixpath.relpath(moved_target, new_dir or ".")
        # Re-encode before splicing.
        new_encoded_path = percent_encode(new_path)
        new_encoded_anchor = percent_encode(anchor) if anchor else ""
        new_dest = new_encoded_path + ("#" + new_encoded_anchor if new_encoded_anchor else "")
        if new_dest != lk.dest:
            edits.append((lk.start, lk.end, new_dest))

    for start, end, repl in sorted(edits, reverse=True):
        text = text[:start] + repl + text[end:]
    return text


def normalize_body_links(text: str) -> str:
    """Re-encode every relative link/image destination in ``text``.

    An author (human or agent) may write a destination unencoded — a raw
    filename with a space or paren, taken verbatim. This normalises each one,
    via the same offset-based splice :func:`_rewrite_text` uses, so untouched
    bytes survive. Idempotent. Absolute paths, scheme-qualified URLs, and
    bare anchors are left alone.
    """
    edits: list[tuple[int, int, str]] = []
    for lk in iter_links(text):
        if not _is_relative_dest(lk.decoded_path):
            continue
        new_encoded_path = percent_encode(lk.decoded_path)
        new_encoded_anchor = percent_encode(lk.decoded_anchor) if lk.decoded_anchor else ""
        new_dest = new_encoded_path + ("#" + new_encoded_anchor if new_encoded_anchor else "")
        if new_dest != lk.dest:
            edits.append((lk.start, lk.end, new_dest))

    for start, end, repl in sorted(edits, reverse=True):
        text = text[:start] + repl + text[end:]
    return text


def _yaml() -> YAML:
    y = YAML()  # typ='rt' — round-trip, preserves formatting
    y.preserve_quotes = True
    y.width = 4096  # never line-wrap long scalars
    # Match the conventions-spec indentation so block sequences (the per-type
    # edge keys, `supersedes`, …) round-trip byte-for-byte: `  - "[t](p.md)"`.
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _load_yaml(fm_text: str):
    return _yaml().load(fm_text if fm_text.strip() else "{}\n")


def _dump_yaml(data) -> str:
    buf = StringIO()
    _yaml().dump(data, buf)
    return buf.getvalue()


def _quote_links(value):
    """Wrap a fresh markdown-link scalar (or each item of a list) in
    :class:`DoubleQuotedScalarString`.

    A first-time value has no prior style for ruamel to round-trip from, and
    would otherwise render single-quoted against the conventions spec. Only
    strings starting ``[`` are touched; image embeds (``![…]``) never appear
    in frontmatter, so that form isn't handled.
    """
    if isinstance(value, str):
        if value.startswith("["):
            return DoubleQuotedScalarString(value)
        return value
    if isinstance(value, list):
        return [_quote_links(item) for item in value]
    return value


class WikiPage:
    """One page's frontmatter + body. Pure-functional — no I/O, no mutation.

    ``set``/``merge``/``retarget`` return a *new* :class:`WikiPage`; the
    original is never modified in place.
    """

    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text

    @property
    def frontmatter(self) -> dict | None:
        """The full frontmatter mapping, or ``None`` if this page has none."""
        fm, _body, _offset = split_frontmatter(self.text)
        if fm is None:
            return None
        return _load_yaml(fm)

    @property
    def body(self) -> str:
        """The document body — everything after the frontmatter block."""
        _fm, body, _offset = split_frontmatter(self.text)
        return body

    def get(self, key: str):
        """Return the value of ``key`` in this page's frontmatter, or ``None``."""
        data = self.frontmatter
        if data is None:
            return None
        return data.get(key)

    def set(self, key: str, value) -> "WikiPage":
        """Return a new page with frontmatter ``key`` set to ``value``.

        Mints a frontmatter block if this page has none. Only the block is
        reformatted; the body is preserved exactly.
        """
        # When there's no frontmatter yet, `fm` is None (so `_load_yaml` mints
        # an empty mapping) and `body` is the whole text — i.e. a fresh block
        # is prepended to the untouched document by the same expression.
        fm, body, _offset = split_frontmatter(self.text)
        data = _load_yaml(fm or "")
        data[key] = _quote_links(value)
        return WikiPage("---\n" + _dump_yaml(data) + "---\n" + body)

    def merge(self, key: str, values: list) -> "WikiPage":
        """Return a new page with ``values`` unioned into ``key``'s existing list.

        Order-preserving: existing entries hold their position, new ones
        append, duplicates drop. Equivalent to :meth:`set` when ``key`` is
        absent. Use this, not get-union-set by hand, for any list-valued key
        (``tags``, the typed-edge keys) that may already have entries.
        """
        existing = self.get(key)
        merged = list(existing) if existing else []
        for value in values:
            if value not in merged:
                merged.append(value)
        return self.set(key, merged)

    def links(self) -> list[LinkMatch]:
        """Every link/image in this page, body and frontmatter alike, in order."""
        return list(iter_links(self.text))

    def retarget(self, file_rel: str, old_rel: str, new_rel: str) -> "WikiPage":
        """Return a new page with links fixed for the vault-wide move ``old_rel -> new_rel``.

        ``file_rel`` is where *this* page sits before the move; pass
        ``file_rel == old_rel`` when this page is the one being moved, so its
        own outbound links are rebased onto ``new_rel``'s folder too.
        """
        return WikiPage(_rewrite_text(self.text, file_rel, old_rel, new_rel))


def plan_move(pages: dict[str, str], old_rel: str, new_rel: str) -> dict[str, str]:
    """Compute the post-move vault from ``pages`` (a ``{rel: text}`` mapping).

    Pure. The moved page appears under ``new_rel``; every other page keeps
    its key. Inbound and outbound links are both fixed.

    ``old_rel`` need not be a key of ``pages``: a caller retargeting links at
    a non-page file (a ``raw/`` artifact, say) passes only the markdown pages
    whose *inbound* links should follow the rename.
    """
    return {
        (new_rel if rel == old_rel else rel): WikiPage(text).retarget(rel, old_rel, new_rel).text
        for rel, text in pages.items()
    }


# --- CLI ---------------------------------------------------------------------

def _main(argv=None) -> int:  # pragma: no cover - thin CLI wrapper
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("get", help="print a frontmatter value")
    g.add_argument("file")
    g.add_argument("key")

    s = sub.add_parser("set", help="set a frontmatter value in place")
    s.add_argument("file")
    s.add_argument("key")
    s.add_argument("value")
    s.add_argument("--json", action="store_true", help="parse value as JSON")

    m = sub.add_parser(
        "merge",
        help="union a JSON list into an existing list-valued key (tags, edge keys)",
    )
    m.add_argument("file")
    m.add_argument("key")
    m.add_argument("value", help="JSON list of values to union in")

    args = parser.parse_args(argv)

    path = Path(args.file)
    page = WikiPage(path.read_text(encoding="utf-8"))

    if args.cmd == "get":
        value = page.get(args.key)
        if value is None:
            return 1
        print(value)
        return 0

    if args.cmd == "merge":
        values = json.loads(args.value)
        path.write_text(page.merge(args.key, values).text, encoding="utf-8")
        return 0

    value = json.loads(args.value) if args.json else args.value
    path.write_text(page.set(args.key, value).text, encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
