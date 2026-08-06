"""PageRecord — the one module that reads the frontmatter schema.

Frontmatter text in, one typed record out. Every caller that needs a page's
frontmatter (``build_index.py``, ``Vault.pages()``,
``search_index.upsert_page``) goes through here rather than re-parsing keys,
so the schema changes in exactly one place.

``rel`` is wiki/-relative throughout this module. ``kind`` is derived from the
page's folder via :data:`place.FOLDER_KINDS` (kind-folders pluralize, kind
values stay singular — ADR-0008); ``edges`` recovers each of
:data:`EDGE_KEYS`' targets, rebased from the page's own directory to be
relative to ``wiki/``; ``superseded_by`` is derived by inverting every other
page's ``supersedes`` edge, never read from frontmatter.
"""
from __future__ import annotations

import posixpath
from dataclasses import dataclass, field, replace

import place
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
    """Resolve a markdown link (``[title](path)``) to a decoded path relative
    to ``wiki/``. ``page_dir`` is wiki-root-relative, so no ``wiki/`` prefix
    is added here.

    **Trap:** the result is one ``..`` short of true vault-relative — a
    ``raw_source`` link from ``source/foo.md`` decodes to ``raw/foo.md``, not
    ``../raw/foo.md``. That's only safe because consumers re-resolve through
    :func:`wikipage.resolve_link_dest` with ``prefix="wiki"`` (see
    ``ingest_scan._back_pointers_by_raw``), and the prefix they add cancels the
    ``..`` missing here. Adding the prefix on this side without dropping it on
    the consumer side doubles the path up.
    """
    dest = wikipage.link_dest(markdown_link)
    if dest is None:
        raise ValueError(f"not a markdown link: {markdown_link!r}")
    return wikipage.resolve_link_dest(dest, page_dir, prefix="")


def page_record(rel: str, text: str) -> PageRecord:
    """Decode one page's frontmatter. ``superseded_by`` is always empty here —
    it needs every other page, so only :func:`load_records` fills it in.
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

    folder = rel.partition("/")[0]
    if folder not in place.FOLDER_KINDS:
        raise ValueError(f"{rel!r}: unknown kind-folder {folder!r}")
    return PageRecord(
        rel=rel,
        kind=place.FOLDER_KINDS[folder],
        title=str(data.get("title", "")),
        summary=str(data.get("summary", "")),
        tags=list(data.get("tags") or []),
        source_date=str(data.get("source_date", "")),
        volatility=str(data.get("volatility", "")),
        edges=edges,
    )


def load_records(pages: dict[str, str]) -> dict[str, PageRecord]:
    """Decode every page in ``pages`` (``{rel: text}``, rel wiki/-relative),
    filling in ``superseded_by`` by inverting the ``supersedes`` edges.
    ``_index.md`` is never a page.
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
