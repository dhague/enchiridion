"""Resolve the vault root.

Order (§1 of the implementation plan), highest priority first:

1. ``$WIKI_ROOT`` if set — wins always (the query-from-anywhere mode).
2. else the nearest ancestor of ``start`` containing a vault marker: a ``wiki/``
   directory or a ``.wiki-root`` sentinel file.
3. else ``start`` (the dedicated-mode default, ``cwd``).

Every other script imports :func:`resolve_vault_root`; none hard-codes a path.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

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


if __name__ == "__main__":  # pragma: no cover - thin CLI for Bash callers
    print(resolve_vault_root())
    sys.exit(0)
