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

_APOSTROPHE = re.compile(r"['’]")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    """Return ``title`` as a lowercase kebab-slug: apostrophes dropped (so
    "What's" -> "whats", not "what-s"), other punctuation and runs of
    whitespace/symbols collapsed to one hyphen, no leading/trailing hyphen.
    """
    slug = _APOSTROPHE.sub("", title.lower())
    slug = _NON_ALNUM.sub("-", slug)
    return slug.strip("-")


def path(kind: str, title: str) -> str:
    """Return the vault-relative path for a new page of ``kind`` titled ``title``."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; must be one of {KINDS}")
    return f"wiki/{kind}/{slugify(title)}.md"


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
