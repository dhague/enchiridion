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
  is pure-functional and does no I/O at all.

The vault also fronts the lexical index at ``.wiki-knowledge/index.db``:
:meth:`Vault.search`/``reindex``/``index_status`` proxy to
:class:`search_index.SearchIndex`, and :meth:`Vault.write` inline-updates it.
That inline update is a latency optimisation only — see :mod:`search_index`
for why correctness lives in the staleness scan instead.

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
    """Return the resolved vault root, per the order in the module docstring.

    ``start`` defaults to cwd and ``env`` to ``os.environ``; both are
    injectable so the resolution logic is testable without touching the real
    process environment.
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
        # Lazy, so paths that never search (`load`, `move_page`) don't pay
        # the FTS5 probe and schema check.
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

        Inline-updates the search index if it's already open. Safe to skip —
        :meth:`search`'s staleness scan is the correctness path.
        """
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page.text, encoding="utf-8")
        if self._index is not None:
            self._index.upsert_page(rel.removeprefix("wiki/"), page.text)

    def load_wiki_pages(self, *, include_index: bool = False) -> dict[str, str]:
        """Every ``wiki/**`` page as a ``{rel: text}`` map. Never walks ``raw/``.

        ``_index.md`` is a generated file, not a page, so it's excluded by
        default. The rare caller that wants it too (a page-move rewriting
        the index's own links, say) opts in with ``include_index=True``.
        """
        wiki_root = self.root / "wiki"
        pages: dict[str, str] = {}
        for path in wiki_root.rglob("*.md"):
            rel = path.relative_to(self.root).as_posix()
            if not include_index and rel == "wiki/_index.md":
                continue
            pages[rel] = path.read_text(encoding="utf-8")
        return pages

    def pages_with_text(self) -> dict[str, tuple[PageRecord, str]]:
        """Every ``wiki/**`` page as a ``{rel: (PageRecord, text)}`` map.

        Same ``rel`` convention as :meth:`pages`, but keeps the raw text
        alongside the record for callers (the raw/ sweep's back-pointer
        resolution) that need both without re-reading the file.
        """
        wiki_relative = {
            rel.removeprefix("wiki/"): text
            for rel, text in self.load_wiki_pages().items()
        }
        records = page_record.load_records(wiki_relative)
        return {
            f"wiki/{rel}": (replace(rec, rel=f"wiki/{rel}"), wiki_relative[rel])
            for rel, rec in records.items()
        }

    def pages(self) -> dict[str, PageRecord]:
        """Every ``wiki/**`` page as a ``{rel: PageRecord}`` map.

        ``rel`` is always vault-relative (e.g. ``"wiki/concepts/a.md"``) — the
        one convention every :class:`Vault` enumeration method uses.
        ``_index.md`` is never a page; ``raw/`` is never walked.
        """
        return {rel: rec for rel, (rec, _text) in self.pages_with_text().items()}

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
        vault-relative paths, sorted; empty for ``old_rel == new_rel``.

        The index is deliberately **not** inline-updated here: letting the
        next :meth:`search`'s staleness scan reconcile the whole move is
        cheaper than a per-file upsert.
        """
        files = self.load_wiki_pages(include_index=True)
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
        pages = self.load_wiki_pages(include_index=True)
        return sorted(self._write_changed(plan_move(pages, old_rel, new_rel), pages))

    # --- search / index facade -------------------------------------------

    def search(self, *args, **kwargs) -> list[SearchHit]:
        """Proxy to the search index, verbatim. Rels in the returned hits stay
        **wiki-relative** (``concepts/foo.md``), matching ``wiki/_index.md`` and
        :mod:`page_record` — not the vault-relative rels the rest of this class
        returns."""
        return self._get_index().search(*args, **kwargs)

    def reindex(self, *, full: bool = False) -> IndexStats:
        """Force a re-index. ``full=True`` wipes the db first; ``full=False``
        runs the same staleness scan a normal ``search`` runs."""
        return self._get_index().reindex(full=full)

    def index_status(self) -> IndexStatus:
        """Page count, db size, backend (``fts5`` or ``re``), schema version."""
        return self._get_index().status()

    def tag_vocabulary(self) -> list[tuple[str, int]]:
        """Every tag in the vault with its usage count, most-used first."""
        return self._get_index().tag_counts()


# --- CLI ---------------------------------------------------------------------

def _main(argv=None) -> int:  # pragma: no cover - thin CLI wrapper
    """Bare ``vault.py`` prints the resolved root. That form is a documented
    surface (wiki-retrieval's SKILL.md calls it), so it is dispatched before
    argparse sees ``argv`` rather than being a subcommand with a default.
    """
    import argparse

    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv:
        print(resolve_vault_root())
        return 0

    parser = argparse.ArgumentParser(description="Vault root resolution and vault-wide operations.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("root", help="print the resolved vault root (the no-argument default)")

    mv = sub.add_parser(
        "move",
        help="move a page within the vault and fix every link, inbound and outbound",
    )
    mv.add_argument("old_rel", help="vault-relative path of the page to move")
    mv.add_argument("new_rel", help="vault-relative path to move it to")

    args = parser.parse_args(argv)
    root = resolve_vault_root()

    if args.cmd == "root":
        print(root)
        return 0

    for rel in Vault(root).move_page(args.old_rel, args.new_rel):
        print(rel)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
