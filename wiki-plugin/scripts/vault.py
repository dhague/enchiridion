"""The vault: where its root is, and everything that reads or writes inside it.

Two things live here:

* :func:`resolve_vault_root` — where the vault *is*. See
  docs/adr/0004-deployment-modes-and-vault-root-resolution.md for why. Order,
  highest priority first:

  1. ``$WIKI_ROOT`` if set — wins always (the query-from-anywhere mode).
  2. else the nearest ancestor of ``start`` containing a vault marker: a
     ``wiki/`` directory or a ``.wiki-root`` sentinel file.
  3. else ``start`` (the dedicated-mode default, ``cwd``).

  Every other script imports it; none hard-codes a path.

* :class:`Vault` — all vault I/O and every cross-page operation, notably
  :meth:`Vault.move_page`, which needs every other page's text to rewrite the
  links pointing at the moved page. Its counterpart :class:`wikipage.WikiPage`
  is pure-functional and does no I/O at all; that split is the design in #29.

The vault is also the entry point for the lexical search index (#39): the
:class:`SearchIndex` lives at ``.wiki-knowledge/index.db`` inside the vault,
and :meth:`Vault.search`/``reindex``/``index_status`` proxy through. Inline
updates fire from :meth:`Vault.write` (and the methods built on it: ``set``,
``merge``) — the staleness scan inside ``search()`` is the correctness path,
the inline update is a latency optimisation (so a write→search round-trip
doesn't re-read the file).

``commit.py`` (git orchestration) stays separate — this module doesn't talk to
git itself.
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import page_record
import search_index
from page_record import PageRecord
from search_index import IndexStats, IndexStatus, SearchHit
from wikipage import WikiPage, plan_move

#: A directory is a vault root if it contains any of these.
MARKERS = ("wiki", ".wiki-root")


def _has_marker(directory: Path) -> bool:
    return any((directory / marker).exists() for marker in MARKERS)


def resolve_vault_root(start: Path | str | None = None, env: dict | None = None) -> Path:
    """Return the resolved vault root per the §1 order.

    ``start`` defaults to the current working directory and ``env`` to
    ``os.environ``; both are injectable so the resolution logic is trivial to
    test without touching the real process environment.
    """
    if env is None:
        env = os.environ
    start_path = Path(start) if start is not None else Path.cwd()

    wiki_root = env.get("WIKI_ROOT")
    if wiki_root:  # set and non-empty wins always
        return Path(wiki_root).resolve()

    start_path = start_path.resolve()
    for directory in (start_path, *start_path.parents):
        if _has_marker(directory):
            return directory

    return start_path


class Vault:
    """Owns all vault I/O and cross-page operations over the pages at ``root``."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        # Lazy: the search index is opened on first use. Code paths that
        # never search (``load``, ``move_page``) don't pay the FTS5 probe
        # or schema-check cost. ``Vault.__init__`` stays cheap.
        self._index: search_index.SearchIndex | None = None

    def _get_index(self) -> search_index.SearchIndex:
        if self._index is None:
            self._index = search_index.SearchIndex(self.root)
        return self._index

    def load(self, rel: str) -> WikiPage:
        """Read the page at ``rel`` (vault-relative) into a :class:`WikiPage`."""
        return WikiPage((self.root / rel).read_text(encoding="utf-8"))

    def write(self, rel: str, page: WikiPage) -> None:
        """Write ``page`` to ``rel`` (vault-relative), creating parents as needed.

        Inline-updates the search index if it's been opened (latency
        optimisation — the next search won't have to re-stat this file).
        The staleness scan inside :meth:`search` is the correctness path,
        so the inline update is safe to skip.
        """
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page.text, encoding="utf-8")
        if self._index is not None:
            self._index.upsert_page(rel.removeprefix("wiki/"), page.text)

    def load_wiki_pages(self) -> dict[str, str]:
        """Every ``wiki/**`` page as a ``{rel: text}`` map. Never walks ``raw/``."""
        wiki_root = self.root / "wiki"
        pages: dict[str, str] = {}
        for path in wiki_root.rglob("*.md"):
            rel = path.relative_to(self.root).as_posix()
            pages[rel] = path.read_text(encoding="utf-8")
        return pages

    def pages(self) -> dict[str, PageRecord]:
        """Every ``wiki/**`` page as a ``{rel: PageRecord}`` map (#40, #41).

        ``rel`` is always vault-relative (e.g. ``"wiki/concept/a.md"``) — the
        one convention every :class:`Vault` enumeration method uses.
        ``_index.md`` is never a page; ``raw/`` is never walked. Records are
        decoded via :mod:`page_record`, whose edge-rebasing is wiki/-relative
        by contract; only the outward-facing ``rel`` is relabelled here.
        """
        wiki_relative = {
            rel.removeprefix("wiki/"): text
            for rel, text in self.load_wiki_pages().items()
        }
        records = page_record.load_records(wiki_relative)
        return {
            f"wiki/{rel}": replace(rec, rel=f"wiki/{rel}")
            for rel, rec in records.items()
        }

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

    def _write_changed(self, planned: dict[str, str], before: dict[str, str]) -> list[str]:
        """Write every page in ``planned`` whose text differs from ``before``.

        Returns the changed vault-relative paths, in ``planned`` order.
        """
        changed: list[str] = []
        for rel, text in planned.items():
            if text == before.get(rel):
                continue
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            changed.append(rel)
        return changed

    def move_page(self, old_rel: str, new_rel: str) -> list[str]:
        """Rewrite links across the vault's wiki pages and move the page on disk.

        Reads every ``wiki/**`` page (never ``raw/`` — its files aren't
        rewritten by a page move), plans the move, writes back only the pages
        whose text changed, then removes the original. Returns the changed
        vault-relative paths, sorted — which for a move that rewrites nothing
        at all (``old_rel == new_rel``) is empty.

        The search index is **not** inline-updated here — the next
        :meth:`search` call's staleness scan reconciles the move (inserting
        the new rel, removing the old, re-upserting the changed files),
        which is correct and cheaper than a per-file upsert.
        """
        files = self.load_wiki_pages()
        if old_rel not in files:
            raise FileNotFoundError(f"{old_rel} not found under {self.root}")

        # `planned` keys the moved page under new_rel, so writing every changed
        # page also lays down the moved file (with its outbound links fixed) —
        # all that's left is to drop the original.
        changed = self._write_changed(plan_move(files, old_rel, new_rel), files)
        old_path = self.root / old_rel
        if old_path.resolve() != (self.root / new_rel).resolve():
            old_path.unlink()
        return sorted(changed)

    def rewrite_inbound_links(self, old_rel: str, new_rel: str) -> list[str]:
        """Rewrite ``wiki/**`` pages' links pointing at ``old_rel`` to ``new_rel``.

        For a target that is not itself a wiki page — e.g. a ``raw/`` artifact
        renamed externally — ``old_rel``/``new_rel`` are never read, parsed,
        or written; only *other* pages' inbound links are fixed. Returns the
        changed vault-relative paths, sorted.
        """
        pages = self.load_wiki_pages()
        return sorted(self._write_changed(plan_move(pages, old_rel, new_rel), pages))

    # --- search / index facade -------------------------------------------

    def search(self, *args, **kwargs) -> list[SearchHit]:
        """Proxy to the search index. Rels in the returned hits are
        wiki-relative (``concept/foo.md``) so they match the convention in
        ``wiki/_index.md`` and :mod:`page_record` — read them as relative
        to ``wiki/``."""
        return self._get_index().search(*args, **kwargs)

    def reindex(self, *, full: bool = False) -> IndexStats:
        """Force a re-index. ``full=True`` wipes the db first; ``full=False``
        runs the staleness scan (the same scan a normal ``search`` runs)."""
        return self._get_index().reindex(full=full)

    def index_status(self) -> IndexStatus:
        """Page count, db size, backend (``fts5`` or ``re``), schema version."""
        return self._get_index().status()


if __name__ == "__main__":  # pragma: no cover - thin CLI for Bash callers
    print(resolve_vault_root())
    sys.exit(0)
