"""One-off migration: move pages out of the old singular kind-folders
(``wiki/concept/``, ``wiki/entity/``, ``wiki/source/``) into their plural
replacements (``wiki/concepts/``, ``wiki/entities/``, ``wiki/sources/``),
per ADR-0008 (#114). ``wiki/synthesis/`` is unchanged and untouched.

Scope is plugin-managed vaults only — a vault this plugin created and has
always owned the folder structure of. Not a general importer for arbitrary
external vault layouts.

Not a permanent ``vault.py`` subcommand: run once against a vault, then
delete this file.

CLI::

    python migrate_kind_folders_0114.py [vault_root] [--dry-run]

``vault_root`` defaults to :func:`vault.resolve_vault_root`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import place
import vault as vault_module
from vault import Vault

#: Old singular folder -> new plural folder. `synthesis` is deliberately
#: absent: its folder name doesn't change.
_RENAMES = {
    kind: (kind, place.KIND_FOLDERS[kind])
    for kind in ("source", "entity", "concept")
}


class MigrationError(Exception):
    """Raised when a planned move would collide with an existing page."""


def plan(root: Path) -> list[tuple[str, str]]:
    """Return ``[(old_rel, new_rel), ...]`` for every page that needs moving.

    Scans each old singular folder that still exists on disk; a kind whose
    old folder is absent (already migrated, or never used) contributes
    nothing. Raises :class:`MigrationError` if any planned destination
    already exists — a same-slug collision between the singular and plural
    folder, which this script refuses to silently overwrite or merge.
    """
    moves: list[tuple[str, str]] = []
    for _kind, (old_folder, new_folder) in _RENAMES.items():
        old_dir = root / "wiki" / old_folder
        if not old_dir.is_dir():
            continue
        for path in sorted(old_dir.glob("*.md")):
            old_rel = f"wiki/{old_folder}/{path.name}"
            new_rel = f"wiki/{new_folder}/{path.name}"
            if (root / new_rel).exists():
                raise MigrationError(
                    f"collision: {old_rel} and {new_rel} both exist — "
                    "resolve by hand before re-running"
                )
            moves.append((old_rel, new_rel))
    return moves


def migrate(root: Path, *, dry_run: bool = False) -> list[tuple[str, str]]:
    """Execute :func:`plan` against *root*, moving each page in turn.

    Each move goes through :meth:`Vault.move_page`, so inbound links across
    the vault are fixed as a side effect. Returns the executed ``(old_rel,
    new_rel)`` pairs; empty in dry-run mode, which only validates and prints
    the plan. Empties old singular folders are removed once drained.
    """
    moves = plan(root)
    if dry_run:
        return []

    v = Vault(root)
    for old_rel, new_rel in moves:
        v.move_page(old_rel, new_rel)

    for old_folder, _new_folder in _RENAMES.values():
        old_dir = root / "wiki" / old_folder
        if not old_dir.is_dir():
            continue
        gitkeep = old_dir / ".gitkeep"
        if gitkeep.is_file():
            gitkeep.unlink()
        if not any(old_dir.iterdir()):
            old_dir.rmdir()

    return moves


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI wrapper
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "vault_root", nargs="?", default=None, help="defaults to the resolved vault root"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the plan without moving anything"
    )
    args = parser.parse_args(argv)

    root = Path(args.vault_root) if args.vault_root else vault_module.resolve_vault_root()

    try:
        if args.dry_run:
            moves = plan(root)
            if not moves:
                print("nothing to migrate")
            for old_rel, new_rel in moves:
                print(f"{old_rel} -> {new_rel}")
            return 0

        moves = migrate(root)
    except MigrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not moves:
        print("nothing to migrate")
        return 0

    for old_rel, new_rel in moves:
        print(f"{old_rel} -> {new_rel}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
