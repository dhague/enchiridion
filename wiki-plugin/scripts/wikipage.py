"""WikiPage/Vault — the page model and all vault I/O (#29, #32).

Replaces frontmatter.py + lib/md.py + links.py's single-page logic. Two
responsibilities, split the same way the design in #29 asked for:

* :class:`WikiPage` — pure-functional: frontmatter get/set/merge, body access,
  outbound-link move-planning. Text in, a new :class:`WikiPage` out — no I/O,
  no mutation-in-place.
* :class:`Vault` — owns all I/O (load/write) and cross-page operations,
  notably :meth:`Vault.move_page`, which needs every other page's text to
  rewrite the links that point at the moved page.

``vault.py`` (root resolution) and ``commit.py`` (git orchestration) stay
separate — this module doesn't resolve a root or talk to git itself.

Only the frontmatter block is ever re-serialised (ruamel round-trip, pinned
``indent(mapping=2, sequence=4, offset=2)`` so the spec's edge-list
indentation round-trips byte-for-byte) — the body is spliced back verbatim.
Link rewriting on move never round-trips the document through a stringifier
either: destinations are spliced into the raw text back-to-front by exact
source offset, so every untouched byte — including frontmatter links, since
they're found by the same whole-document scan — survives byte-for-byte.

CLI::

    python wikipage.py get <file> <key>
    python wikipage.py set <file> <key> <value> [--json]
    python wikipage.py merge <file> <key> <json-list>
    python wikipage.py move <old_rel> <new_rel>   # resolves the vault, rewrites + renames on disk
"""
from __future__ import annotations

import posixpath
import re
import sys
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from markdown_it import MarkdownIt
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

_MD = MarkdownIt("commonmark")

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
    any title excluded), so ``text[start:end] == dest``. ``line`` is the
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
    absolute into ``text`` and point at the destination path itself. Scans the
    *whole* document — including any YAML frontmatter block — so per-key
    frontmatter links (typed edges, ``supersedes``, ``raw_source``) are found
    by the same rule as body links.
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
        yield LinkMatch(start=start, end=end, dest=dest, is_image=bool(m.group("img")), line=line)


def _is_relative_dest(path: str) -> bool:
    """True when ``path`` (the pre-anchor part) is a vault-relative reference."""
    if path == "":
        return False
    if path.startswith(("/", "#")):
        return False
    if "://" in path:
        return False
    return True


def _rewrite_text(
    text: str,
    file_old_rel: str,
    file_new_rel: str,
    old_rel: str,
    new_rel: str,
) -> str:
    """Return ``text`` with its links fixed for the move ``old_rel -> new_rel``.

    ``file_old_rel``/``file_new_rel`` are where *this* file sits before and
    after the move (equal for every file except the one being moved).
    """
    is_moved_file = file_old_rel == old_rel
    old_dir = posixpath.dirname(file_old_rel)
    new_dir = posixpath.dirname(file_new_rel)

    edits: list[tuple[int, int, str]] = []
    for lk in iter_links(text):
        path, sep, anchor = lk.dest.partition("#")
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
        new_dest = new_path + (sep + anchor if sep else "")
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
    if fm_text.strip() == "":
        return _yaml().load("{}\n")
    return _yaml().load(fm_text)


def _dump_yaml(data) -> str:
    buf = StringIO()
    _yaml().dump(data, buf)
    return buf.getvalue()


def _quote_links(value):
    """Wrap a fresh markdown-link scalar (or each item of a list of them) in
    :class:`DoubleQuotedScalarString`, so a value set for the first time —
    with no prior double-quoted style to round-trip from — still renders
    double-quoted per the conventions spec, not ruamel's default single-quote.
    Only strings that look like a link (``[label](dest)``) are touched —
    image embeds (``![…]``) never appear in frontmatter per the conventions
    spec, so that form isn't handled here.
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
        value = _quote_links(value)
        fm, body, _offset = split_frontmatter(self.text)
        if fm is None:
            # No frontmatter yet — mint a fresh block ahead of the untouched body.
            data = _load_yaml("")
            data[key] = value
            return WikiPage("---\n" + _dump_yaml(data) + "---\n" + self.text)
        data = _load_yaml(fm)
        data[key] = value
        return WikiPage("---\n" + _dump_yaml(data) + "---\n" + body)

    def merge(self, key: str, values: list) -> "WikiPage":
        """Return a new page with ``values`` unioned into ``key``'s existing list.

        Order-preserving: existing entries keep their position, new ones are
        appended, duplicates dropped. Equivalent to :meth:`set` when ``key``
        is absent. This replaces the get-then-union-then-set procedure a
        caller would otherwise have to perform by hand to avoid clobbering a
        list-valued key (``tags``, the typed-edge keys) that already has
        entries.
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

    def retarget(self, file_old_rel: str, file_new_rel: str, old_rel: str, new_rel: str) -> "WikiPage":
        """Return a new page with links fixed for the vault-wide move ``old_rel -> new_rel``.

        ``file_old_rel``/``file_new_rel`` are where *this* page sits before
        and after the move — equal unless this page is the one being moved.
        """
        return WikiPage(_rewrite_text(self.text, file_old_rel, file_new_rel, old_rel, new_rel))


def plan_move(pages: dict[str, str], old_rel: str, new_rel: str) -> dict[str, str]:
    """Compute the post-move vault from ``pages`` (a ``{rel: text}`` mapping).

    Pure — no disk I/O. The moved page appears under ``new_rel`` in the
    result; every other page keeps its key. Both inbound and outbound links
    are fixed. ``old_rel`` need not be a key of ``pages`` — a caller
    retargeting links at a non-page file (e.g. a ``raw/`` artifact renamed by
    ``normalize_raw.py``) can pass a ``pages`` map that only contains the
    markdown pages whose *inbound* links should follow the rename.
    """
    result: dict[str, str] = {}
    for rel, text in pages.items():
        page = WikiPage(text)
        if rel == old_rel:
            result[new_rel] = page.retarget(old_rel, new_rel, old_rel, new_rel).text
        else:
            result[rel] = page.retarget(rel, rel, old_rel, new_rel).text
    return result


class Vault:
    """Owns all vault I/O and cross-page operations over the pages at ``root``."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def load(self, rel: str) -> WikiPage:
        """Read the page at ``rel`` (vault-relative) into a :class:`WikiPage`."""
        return WikiPage((self.root / rel).read_text(encoding="utf-8"))

    def write(self, rel: str, page: WikiPage) -> None:
        """Write ``page`` to ``rel`` (vault-relative), creating parents as needed."""
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page.text, encoding="utf-8")

    def load_wiki_pages(self) -> dict[str, str]:
        """Every ``wiki/**`` page as a ``{rel: text}`` map. Never walks ``raw/``."""
        wiki_root = self.root / "wiki"
        pages: dict[str, str] = {}
        for path in wiki_root.rglob("*.md"):
            rel = path.relative_to(self.root).as_posix()
            pages[rel] = path.read_text(encoding="utf-8")
        return pages

    def set(self, rel: str, key: str, value) -> WikiPage:
        """Load, :meth:`WikiPage.set`, and write back the page at ``rel``."""
        page = self.load(rel).set(key, value)
        self.write(rel, page)
        return page

    def merge(self, rel: str, key: str, values: list) -> WikiPage:
        """Load, :meth:`WikiPage.merge`, and write back the page at ``rel``."""
        page = self.load(rel).merge(key, values)
        self.write(rel, page)
        return page

    def move_page(self, old_rel: str, new_rel: str) -> list[str]:
        """Rewrite links across the whole vault and move the page on disk.

        Reads every ``*.md`` page under the vault root, plans the move, writes
        back only the pages whose text changed, then renames the moved file.
        Returns the changed vault-relative paths.
        """
        files: dict[str, str] = {}
        for path in self.root.rglob("*.md"):
            rel = path.relative_to(self.root).as_posix()
            files[rel] = path.read_text(encoding="utf-8")
        if old_rel not in files:
            raise FileNotFoundError(f"{old_rel} not found under {self.root}")

        planned = plan_move(files, old_rel, new_rel)

        changed: list[str] = []
        for rel, new_text in planned.items():
            if rel == new_rel:
                continue  # handled by the rename below
            if new_text != files.get(rel):
                (self.root / rel).write_text(new_text, encoding="utf-8")
                changed.append(rel)

        # Move the file itself, writing its rewritten (outbound-fixed) content.
        dst = self.root / new_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(planned[new_rel], encoding="utf-8")
        old_path = self.root / old_rel
        if old_path.resolve() != dst.resolve():
            old_path.unlink()
        changed.append(new_rel)
        return changed

    def rewrite_inbound_links(self, old_rel: str, new_rel: str) -> list[str]:
        """Rewrite ``wiki/**`` pages' links pointing at ``old_rel`` to ``new_rel``.

        For a target that is not itself a wiki page — e.g. a ``raw/`` artifact
        being renamed by ``normalize_raw.py`` — ``old_rel``/``new_rel`` are
        never read, parsed, or written; only *other* pages' inbound links are
        fixed. Returns the changed vault-relative paths, sorted.
        """
        pages = self.load_wiki_pages()
        planned = plan_move(pages, old_rel, new_rel)
        changed: list[str] = []
        for rel, text in planned.items():
            if text != pages.get(rel):
                (self.root / rel).write_text(text, encoding="utf-8")
                changed.append(rel)
        return sorted(changed)


# --- CLI ---------------------------------------------------------------------

def _main(argv=None) -> int:  # pragma: no cover - thin CLI wrapper
    import argparse
    import json

    import vault as vault_mod

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

    mv = sub.add_parser("move", help="move a page and fix all links")
    mv.add_argument("old_rel")
    mv.add_argument("new_rel")

    args = parser.parse_args(argv)

    if args.cmd == "move":
        root = vault_mod.resolve_vault_root()
        v = Vault(root)
        for rel in v.move_page(args.old_rel, args.new_rel):
            print(rel)
        return 0

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
