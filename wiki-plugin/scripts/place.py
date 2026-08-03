"""Compute a new page's vault-relative path: kind-folder + kebab-slug of title.

*Which* kind a page belongs to is judgment (the placement algorithm in
wiki-conventions) and stays with the ingesting agent. Once a kind is chosen,
turning a title into `wiki/<kind>/<slug>.md` is pure mechanics — this module
makes it a deterministic function instead of prose the agent executes by hand,
so filenames are consistent regardless of who (or what model) is ingesting.

CLI::

    python place.py <kind> "<title>"   # prints the vault-relative path
"""
from __future__ import annotations

import re
import sys

#: The fixed kind-folder set (wiki-conventions §Folder structure).
KINDS = ("source", "synthesis", "entity", "concept")

#: Maximum length for generated kebab-slug filenames, to stay readable and
#: leave headroom under the Windows 255-char path limit (#70).
MAX_SLUG_LENGTH = 64

_APOSTROPHE = re.compile(r"['’]")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_MIN_WORD_CUT = 8


def _truncate_slug(slug: str, max_length: int) -> str:
    """Truncate *slug* to *max_length*, cutting at the last hyphen boundary
    when there is a reasonable word break (≥ *min_cut* chars). Falls back to a
    hard cut at *max_length*."""
    if len(slug) <= max_length:
        return slug
    cut = slug.rfind("-", 0, max_length)
    if cut >= _MIN_WORD_CUT:
        return slug[:cut].rstrip("-")
    return slug[:max_length].rstrip("-")


def slugify(title: str, max_length: int | None = None) -> str:
    """Return ``title`` as a lowercase kebab-slug: apostrophes dropped (so
    "What's" -> "whats", not "what-s"), other punctuation and runs of
    whitespace/symbols collapsed to one hyphen, no leading/trailing hyphen.

    When *max_length* is given, the slug is truncated to at most that many
    characters, at a hyphen boundary when possible (see :func:`_truncate_slug`).
    """
    slug = _APOSTROPHE.sub("", title.lower())
    slug = _NON_ALNUM.sub("-", slug)
    slug = slug.strip("-")
    if max_length is not None:
        slug = _truncate_slug(slug, max_length)
    return slug


def path(kind: str, title: str) -> str:
    """Return the vault-relative path for a new page of ``kind`` titled ``title``."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; must be one of {KINDS}")
    return f"wiki/{kind}/{slugify(title, MAX_SLUG_LENGTH)}.md"


def _main(argv=None) -> int:  # pragma: no cover - thin CLI wrapper
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=KINDS)
    parser.add_argument("title")
    args = parser.parse_args(argv)

    print(path(args.kind, args.title))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
