"""PageRecord — the one module that reads the frontmatter schema.

Frontmatter text in, one typed record out. Every caller that needs a page's
frontmatter (``Vault.pages()``, ``search_index.upsert_page``) goes through
here rather than re-parsing keys, so the schema changes in exactly one place.

Every path this module touches is vault-relative — a page reference
(``wiki/concepts/a.md``), ADR-0009. ``kind`` is derived from the page's
folder via :func:`place.folder_to_kind` (ADR-0008 singularization rule):
canonical folders resolve from :data:`place.FOLDER_KINDS`; custom folders are
singularized and used verbatim. Pages in any ``wiki/<folder>/`` are supported
without a skip-and-warn guard. ``edges`` recovers each of :data:`EDGE_KEYS`'
targets, resolved from the page's own directory to true vault-relative by
construction; ``superseded_by`` is derived by inverting every other page's
``supersedes`` edge, never read from frontmatter.
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

    page_ref: str
    kind: str
    title: str
    summary: str
    tags: list[str]
    source_date: str
    volatility: str
    edges: list[tuple[str, list[str]]] = field(default_factory=list)
    superseded_by: list[str] = field(default_factory=list)


def _link_target(markdown_link: str, page_dir: str) -> str:
    """Resolve a markdown link (``[title](path)``) to a decoded vault-relative
    path. ``page_dir`` is the vault-relative directory the link lives in, so
    the target resolves true vault-relative by construction."""
    dest = wikipage.link_dest(markdown_link)
    if dest is None:
        raise ValueError(f"not a markdown link: {markdown_link!r}")
    return wikipage.resolve_link_dest(dest, page_dir)


def page_record(page_ref: str, text: str) -> PageRecord:
    """Decode one page's frontmatter. ``superseded_by`` is always empty here —
    it needs every other page, so only :func:`load_records` fills it in.
    """
    data = wikipage.WikiPage(text).frontmatter or {}
    page_dir = posixpath.dirname(page_ref)

    edges: list[tuple[str, list[str]]] = []
    for key in EDGE_KEYS:
        value = data.get(key)
        if not value:
            continue
        if key == "raw_source":
            targets = [_link_target(value, page_dir)]
        else:
            targets = [_link_target(item, page_dir) for item in value]
        edges.append((key, targets))

    # The kind-folder is the directory directly under `wiki/` that holds this
    # page (`wiki/concepts/a.md` → folder `concepts`). A page not at that
    # exact depth (e.g. `wiki/foo.md` or `wiki/concepts/nested/deep.md`) is a
    # structural error. Canonical folders resolve from FOLDER_KINDS; any other
    # folder is singularized via ADR-0008's rule (strip trailing `s`).
    folder = posixpath.basename(posixpath.dirname(page_ref))
    grandparent = posixpath.dirname(posixpath.dirname(page_ref))
    if grandparent != "wiki":
        raise ValueError(f"{page_ref!r}: not directly under a wiki kind-folder")
    kind = place.FOLDER_KINDS.get(folder, place.folder_to_kind(folder))
    return PageRecord(
        page_ref=page_ref,
        kind=kind,
        title=str(data.get("title", "")),
        summary=str(data.get("summary", "")),
        tags=list(data.get("tags") or []),
        source_date=str(data.get("source_date", "")),
        volatility=str(data.get("volatility", "")),
        edges=edges,
    )


def load_records(pages: dict[str, str]) -> dict[str, PageRecord]:
    """Decode every page in ``pages`` (``{page_ref: text}``, keys vault-
    relative), filling in ``superseded_by`` by inverting the ``supersedes``
    edges.

    Pages in any ``wiki/<folder>/`` are decoded and included; custom
    kind-folders are fully supported via :func:`place.folder_to_kind`.
    Pages at the wrong depth (not directly under a kind-folder) raise
    :class:`ValueError`.
    """
    records: dict[str, PageRecord] = {}
    for page_ref, text in pages.items():
        records[page_ref] = page_record(page_ref, text)

    superseded_by: dict[str, list[str]] = {}
    for page_ref, rec in records.items():
        for key, targets in rec.edges:
            if key != "supersedes":
                continue
            for target in targets:
                superseded_by.setdefault(target, []).append(page_ref)

    return {
        page_ref: replace(rec, superseded_by=superseded_by.get(page_ref, []))
        for page_ref, rec in records.items()
    }
