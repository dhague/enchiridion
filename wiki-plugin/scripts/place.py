"""Compute a new page's vault-relative path: kind-folder + kebab-slug of title.

*Which* kind a page belongs to is judgment (wiki-conventions' placement
algorithm) and stays with the ingesting agent. Turning a chosen kind + title
into `wiki/<kind-folder>/<slug>.md` is mechanics, and lives here so filenames
are consistent regardless of who — or which model — is ingesting. Kind
*values* stay singular (`concept`); kind *folders* pluralize (`concepts/`),
except `synthesis` — see :data:`KIND_FOLDERS` (ADR-0008).

Custom kind-folders (any `wiki/<folder>/` that exists in the vault beyond the
four canonical ones) are first-class: :func:`folder_to_kind` derives their
kind via the same ADR-0008 singularization rule. The vault-level I/O scan that
enumerates them lives in :meth:`vault.Vault.discovered_kinds`; :func:`path`
accepts the result via ``extra_kind_folders``.

CLI::

    python place.py <kind> "<title>"   # prints the vault-relative path
"""
from __future__ import annotations

import re
import sys

#: Kind value -> its `wiki/` folder name (ADR-0008: folders pluralize, values
#: stay singular — `synthesis` has no distinct plural, so it's unchanged).
#: The single source of truth for the mapping; no other module may hardcode
#: a kind-folder string.
KIND_FOLDERS = {
    "source": "sources",
    "synthesis": "synthesis",
    "entity": "entities",
    "concept": "concepts",
}

#: Folder name -> kind value, for readers going the other direction
#: (:mod:`page_record` deriving a page's kind from its path).
FOLDER_KINDS = {folder: kind for kind, folder in KIND_FOLDERS.items()}

#: The fixed kind-value set (wiki-conventions, "Vault structure").
KINDS = tuple(KIND_FOLDERS)

#: Cap on generated kebab-slug filenames — readability, plus headroom under
#: the Windows 255-char path limit (#70).
MAX_SLUG_LENGTH = 64

_APOSTROPHE = re.compile("['\\u2018\\u2019]")
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


def folder_to_kind(folder: str) -> str:
    """Derive a kind value from a folder name using the ADR-0008 rule.

    Strips a trailing ``s`` if present (``decisions`` → ``decision``);
    otherwise returns the folder name verbatim (``people`` → ``people``).
    Intended for custom kind-folders not already in :data:`FOLDER_KINDS` —
    canonical folders should be looked up there directly.
    """
    if folder.endswith("s"):
        return folder[:-1]
    return folder


def path(kind: str, title: str, *, extra_kind_folders: dict[str, str] | None = None) -> str:
    """Return the vault-relative path for a new page of ``kind`` titled ``title``.

    Canonical kinds are resolved from :data:`KIND_FOLDERS`. Custom (discovered)
    kinds are resolved from ``extra_kind_folders`` (a ``{kind: folder}`` map
    from :func:`discovered_kinds`). Raises :class:`ValueError` when ``kind``
    is unknown in both.
    """
    if kind in KIND_FOLDERS:
        folder = KIND_FOLDERS[kind]
    elif extra_kind_folders is not None and kind in extra_kind_folders:
        folder = extra_kind_folders[kind]
    else:
        raise ValueError(f"unknown kind {kind!r}; must be one of {KINDS}")
    return f"wiki/{folder}/{slugify(title, MAX_SLUG_LENGTH)}.md"


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
