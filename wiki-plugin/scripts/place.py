"""Compute a new page's vault-relative path: kind-folder + kebab-slug of title.

*Which* kind a page belongs to is judgment (wiki-conventions' placement
algorithm) and stays with the ingesting agent. Turning a chosen kind + title
into `wiki/<kind>/<slug>.md` is mechanics, and lives here so filenames are
consistent regardless of who — or which model — is ingesting.

CLI::

    python place.py <kind> "<title>"   # prints the vault-relative path
"""
from __future__ import annotations

import re
import sys

#: The fixed kind-folder set (wiki-conventions, "Vault structure").
KINDS = ("source", "synthesis", "entity", "concept")

#: Cap on generated kebab-slug filenames — readability, plus headroom under
#: the Windows 255-char path limit (#70).
MAX_SLUG_LENGTH = 64

_APOSTROPHE = re.compile(r"['’]")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_MIN_WORD_CUT = 8


def _truncate_slug(slug: str, max_length: int) -> str:
    """Truncate *slug* to *max_length* at the last hyphen boundary, when that
    leaves at least ``_MIN_WORD_CUT`` chars. Otherwise a hard cut."""
    if len(slug) <= max_length:
        return slug
    cut = slug.rfind("-", 0, max_length)
    if cut >= _MIN_WORD_CUT:
        return slug[:cut].rstrip("-")
    return slug[:max_length].rstrip("-")


def slugify(title: str, max_length: int | None = None) -> str:
    """Return ``title`` as a lowercase kebab-slug. Apostrophes are dropped
    rather than hyphenated ("What's" -> "whats", not "what-s"); every other
    run of non-alphanumerics collapses to one hyphen; ends are stripped.
    *max_length*, if given, truncates via :func:`_truncate_slug`.
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
