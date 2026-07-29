"""PageRecord — the one module that reads the frontmatter schema.

Every caller that needs a page's frontmatter (``build_index.py``,
``Vault.pages()``, ``Vault.search()``) reads a :class:`PageRecord` instead
of re-parsing frontmatter keys itself, so the schema changes in exactly one
place. Frontmatter text in, one typed record out — this module owns
decoding it; nothing downstream re-derives a field from raw YAML.

``kind`` is derived from the page's folder (``wiki/<kind>/...``). ``edges``
recovers each of the seven link-valued keys' targets, rebased from the
page's own directory to be relative to ``wiki/`` (matching
``build_index.py``'s convention, since ``rel`` here is wiki/-relative).
``superseded_by`` is derived, not read from frontmatter: it is the inverse
of every other page's ``supersedes`` edge, so callers stop re-deriving it
themselves.
"""
from __future__ import annotations

import posixpath
from dataclasses import dataclass, field, replace

import wikipage

#: Order mirrors the frontmatter schema block in the conventions spec.
EDGE_KEYS = (
    "raw_source",
    "supersedes",
    "refines",
    "contradicts",
    "example-of",
    "source",
    "related",
)


@dataclass(frozen=True)
class PageRecord:
    """One page's frontmatter, decoded to plain values."""

    rel: str
    kind: str
    title: str
    summary: str
    tags: list[str]
    source_date: str
    volatility: str
    edges: list[tuple[str, list[str]]] = field(default_factory=list)
    superseded_by: list[str] = field(default_factory=list)


def _rebase_to_wiki_root(markdown_link: str, page_dir: str) -> str:
    """Resolve a quoted markdown link (``[title](path)``) to a decoded path
    relative to ``wiki/`` — ``page_dir`` is wiki-root-relative, matching this
    module's ``rel`` convention, so no ``wiki/`` prefix is added here.
    """
    dest = wikipage.link_dest(markdown_link)
    if dest is None:
        raise ValueError(f"not a markdown link: {markdown_link!r}")
    return wikipage.resolve_link_dest(dest, page_dir, prefix="")


def page_record(rel: str, text: str) -> PageRecord:
    """Decode one page's frontmatter. ``superseded_by`` is always empty here —
    it needs every other page in the vault, so it's only ever filled in by
    :func:`load_records`.
    """
    data = wikipage.WikiPage(text).frontmatter or {}
    page_dir = posixpath.dirname(rel)

    edges: list[tuple[str, list[str]]] = []
    for key in EDGE_KEYS:
        value = data.get(key)
        if not value:
            continue
        if key == "raw_source":
            targets = [_rebase_to_wiki_root(value, page_dir)]
        else:
            targets = [_rebase_to_wiki_root(item, page_dir) for item in value]
        edges.append((key, targets))

    return PageRecord(
        rel=rel,
        kind=rel.partition("/")[0],
        title=str(data.get("title", "")),
        summary=str(data.get("summary", "")),
        tags=list(data.get("tags") or []),
        source_date=str(data.get("source_date", "")),
        volatility=str(data.get("volatility", "")),
        edges=edges,
    )


def load_records(pages: dict[str, str]) -> dict[str, PageRecord]:
    """Decode every page in ``pages`` (a ``{rel: text}`` map, rel relative to
    ``wiki/``) and fill in each record's ``superseded_by`` by inverting every
    other page's ``supersedes`` edge. ``_index.md`` is never a page.
    """
    records = {
        rel: page_record(rel, text) for rel, text in pages.items() if rel != "_index.md"
    }

    superseded_by: dict[str, list[str]] = {}
    for rel, rec in records.items():
        for key, targets in rec.edges:
            if key != "supersedes":
                continue
            for target in targets:
                superseded_by.setdefault(target, []).append(rel)

    return {
        rel: replace(rec, superseded_by=superseded_by.get(rel, []))
        for rel, rec in records.items()
    }
