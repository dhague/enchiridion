"""Rewrite relative markdown links when a page moves or is renamed.

On a move ``old_rel -> new_rel`` (both vault-relative, ``/``-separated) two
kinds of link must be fixed:

* **inbound** — links in *other* pages that point at the moved page.
* **outbound** — every relative link *inside* the moved page, since it now
  resolves from a different directory (this includes image embeds and
  self-links).

Links are located by :func:`lib.md.iter_links`, which carries exact source
offsets, and spliced into the raw text **back-to-front** (highest offset
first, so earlier offsets stay valid). The document is never round-tripped
through a stringifier — only the destination substrings change, so every
untouched byte is preserved. Anchors (``path.md#frag``) ride along unchanged;
external (``scheme://``), absolute (``/…``) and anchor-only (``#frag``) links
are left alone.

CLI::

    python links.py move <old_rel> <new_rel>   # rewrites + renames on disk

operates on the vault resolved by :mod:`vault`.
"""
from __future__ import annotations

import posixpath
import sys
from pathlib import Path

from lib import md


def _is_relative_dest(path: str) -> bool:
    """True when ``path`` (the pre-anchor part) is a vault-relative reference."""
    if path == "":
        return False
    if path.startswith(("/", "#")):
        return False
    if "://" in path:
        return False
    return True


def _rewrite_text(
    text: str,
    file_old_rel: str,
    file_new_rel: str,
    old_rel: str,
    new_rel: str,
) -> str:
    """Return ``text`` with its links fixed for the move ``old_rel -> new_rel``.

    ``file_old_rel``/``file_new_rel`` are where *this* file sits before and
    after the move (equal for every file except the one being moved).
    """
    is_moved_file = file_old_rel == old_rel
    old_dir = posixpath.dirname(file_old_rel)
    new_dir = posixpath.dirname(file_new_rel)

    edits: list[tuple[int, int, str]] = []
    for lk in md.iter_links(text):
        path, sep, anchor = lk.dest.partition("#")
        if not _is_relative_dest(path):
            continue
        # Where this link pointed, resolved from the file's original location.
        target = posixpath.normpath(posixpath.join(old_dir or ".", path))
        # For pages other than the moved one, only links at the moved page change.
        if not is_moved_file and target != old_rel:
            continue
        # The moved page itself relocates the target of a self-link.
        moved_target = new_rel if target == old_rel else target
        new_path = posixpath.relpath(moved_target, new_dir or ".")
        new_dest = new_path + (sep + anchor if sep else "")
        if new_dest != lk.dest:
            edits.append((lk.start, lk.end, new_dest))

    for start, end, repl in sorted(edits, reverse=True):
        text = text[:start] + repl + text[end:]
    return text


def plan_move(files: dict[str, str], old_rel: str, new_rel: str) -> dict[str, str]:
    """Compute the post-move vault from ``files`` (a ``{rel: text}`` mapping).

    Pure — no disk I/O. The moved page appears under ``new_rel`` in the result;
    every other page keeps its key. Both inbound and outbound links are fixed.
    """
    result: dict[str, str] = {}
    for rel, text in files.items():
        if rel == old_rel:
            result[new_rel] = _rewrite_text(text, old_rel, new_rel, old_rel, new_rel)
        else:
            result[rel] = _rewrite_text(text, rel, rel, old_rel, new_rel)
    return result


def apply_move(vault_root: Path | str, old_rel: str, new_rel: str) -> list[str]:
    """Rewrite links across the on-disk vault and move the file. Returns changed rels.

    Reads every ``*.md`` page under ``vault_root``, plans the move, writes back
    only the pages whose text changed, then renames the moved file on disk.
    """
    root = Path(vault_root)
    files: dict[str, str] = {}
    for path in root.rglob("*.md"):
        rel = path.relative_to(root).as_posix()
        files[rel] = path.read_text(encoding="utf-8")
    if old_rel not in files:
        raise FileNotFoundError(f"{old_rel} not found under {root}")

    planned = plan_move(files, old_rel, new_rel)

    changed: list[str] = []
    for rel, new_text in planned.items():
        if rel == new_rel:
            continue  # handled by the rename below
        if new_text != files.get(rel):
            (root / rel).write_text(new_text, encoding="utf-8")
            changed.append(rel)

    # Move the file itself, writing its rewritten (outbound-fixed) content.
    dst = root / new_rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(planned[new_rel], encoding="utf-8")
    old_path = root / old_rel
    if old_path.resolve() != dst.resolve():
        old_path.unlink()
    changed.append(new_rel)
    return changed


def _main(argv=None) -> int:  # pragma: no cover - thin CLI wrapper
    import argparse

    import vault

    parser = argparse.ArgumentParser(description="Rewrite links on move/rename.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("move", help="move a page and fix all links")
    m.add_argument("old_rel")
    m.add_argument("new_rel")
    args = parser.parse_args(argv)

    root = vault.resolve_vault_root()
    changed = apply_move(root, args.old_rel, args.new_rel)
    for rel in changed:
        print(rel)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
