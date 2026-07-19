"""Normalize a ``raw/`` file's *name* and follow the rename in ``raw_source``.

Two, and only two, transforms are applied to the filename: a ``YYYY-MM-DD-hhmm-``
prefix (skipped if already present — idempotent) and spaces to underscores.
The file's *content* is never touched; a rename asserts the bytes are
identical before and after.

Any ``source/`` page whose ``raw_source`` link points at the old path is
updated to follow the rename. This reuses :func:`links.plan_move` directly:
the raw file is deliberately **not** added to the ``{rel: text}`` pages map
(it usually isn't markdown, and its content must never be parsed/rewritten),
so ``plan_move``'s per-file loop only ever touches *inbound* links at the old
raw path — exactly the ``raw_source`` follow-the-rename this script needs,
with no separate lookup of "which page references this file" required.

The datetime used for a not-yet-normalized name is a judgment call the script
cannot make on its own (a downloaded file's mtime is not necessarily the
artifact's real date) — callers with better information (e.g. an ingestion
agent reading the artifact's own date) should pass ``when`` explicitly. It
defaults to the file's mtime, a reasonable mechanical fallback.

CLI::

    python normalize_raw.py [raw/relative/path]   # omit to scan the whole raw/ inbox
    python normalize_raw.py --when 2026-03-01T14:05 raw/notes/notes.txt
"""
from __future__ import annotations

import posixpath
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import links

_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}-")

# `raw_source`'s label is, by convention, the artifact's filename — so unlike
# an ordinary link label (a human title, untouched by links.plan_move) it must
# track a rename too. links.plan_move already fixed the destination; this
# regex retargets just the label, and only on the `raw_source` line whose
# (already-rewritten) destination now resolves to the renamed file.
_RAW_SOURCE_RE = re.compile(r'(raw_source:\s*"\[)([^\]]*)(\]\()([^)]*)(\)")')


@dataclass(frozen=True)
class NormalizeResult:
    """The outcome of normalizing one raw file."""

    old_rel: str
    new_rel: str
    renamed: bool
    updated_pages: list[str] = field(default_factory=list)


def normalize_name(name: str, when: datetime) -> str:
    """Return ``name`` normalized: spaces→underscores, dated prefix if absent.

    Idempotent — a name that already carries a valid ``YYYY-MM-DD-hhmm-``
    prefix is returned unchanged (``when`` is ignored in that case).
    """
    spaced = name.replace(" ", "_")
    if _PREFIX_RE.match(spaced):
        return spaced
    return when.strftime("%Y-%m-%d-%H%M-") + spaced


def _fix_raw_source_label(text: str, page_rel: str, new_raw_rel: str, new_name: str) -> str:
    """Retarget a `raw_source` link's label to `new_name` if its destination is `new_raw_rel`."""
    page_dir = posixpath.dirname(page_rel)

    def _repl(m: re.Match) -> str:
        dest = m.group(4)
        target = posixpath.normpath(posixpath.join(page_dir or ".", dest.split("#", 1)[0]))
        if target != new_raw_rel:
            return m.group(0)
        return m.group(1) + new_name + m.group(3) + dest + m.group(5)

    return _RAW_SOURCE_RE.sub(_repl, text)


def _load_wiki_pages(root: Path) -> dict[str, str]:
    pages: dict[str, str] = {}
    for path in (root / "wiki").rglob("*.md"):
        rel = path.relative_to(root).as_posix()
        pages[rel] = path.read_text(encoding="utf-8")
    return pages


def apply_normalize(
    vault_root: Path | str, raw_rel: str, when: datetime | None = None
) -> NormalizeResult:
    """Normalize the raw file at ``raw_rel`` (vault-relative) on disk.

    Renames the file if its name isn't already normalized, asserts the
    content is byte-identical across the rename, and rewrites ``raw_source``
    (or any other link) in whichever pages under ``wiki/`` pointed at it.
    """
    root = Path(vault_root)
    raw_path = root / raw_rel
    if not raw_path.is_file():
        raise FileNotFoundError(f"{raw_rel} not found under {root}")

    effective_when = when or datetime.fromtimestamp(raw_path.stat().st_mtime)
    new_name = normalize_name(raw_path.name, effective_when)
    if new_name == raw_path.name:
        return NormalizeResult(raw_rel, raw_rel, renamed=False, updated_pages=[])

    new_raw_rel = posixpath.join(posixpath.dirname(raw_rel), new_name)

    pages = _load_wiki_pages(root)
    planned = links.plan_move(pages, raw_rel, new_raw_rel)

    old_bytes = raw_path.read_bytes()
    new_path = root / new_raw_rel
    raw_path.rename(new_path)
    if new_path.read_bytes() != old_bytes:
        raise AssertionError(f"content changed while renaming {raw_rel} -> {new_raw_rel}")

    updated: list[str] = []
    for rel, text in planned.items():
        text = _fix_raw_source_label(text, rel, new_raw_rel, new_name)
        if text != pages.get(rel):
            (root / rel).write_text(text, encoding="utf-8")
            updated.append(rel)

    return NormalizeResult(raw_rel, new_raw_rel, renamed=True, updated_pages=sorted(updated))


def scan_and_normalize(
    vault_root: Path | str, when: datetime | None = None
) -> list[NormalizeResult]:
    """Normalize every file under ``raw/``. Returns one :class:`NormalizeResult` each."""
    root = Path(vault_root)
    raw_root = root / "raw"
    results: list[NormalizeResult] = []
    for path in sorted(p for p in raw_root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        results.append(apply_normalize(root, rel, when=when))
    return results


def _main(argv=None) -> int:  # pragma: no cover - thin CLI wrapper
    import argparse

    import vault

    parser = argparse.ArgumentParser(description="Normalize raw/ filenames.")
    parser.add_argument(
        "raw_file", nargs="?", help="raw/-relative path; omit to scan the whole raw/ inbox"
    )
    parser.add_argument(
        "--when", help="ISO datetime for the prefix; defaults to the file's mtime"
    )
    args = parser.parse_args(argv)

    root = vault.resolve_vault_root()
    when = datetime.fromisoformat(args.when) if args.when else None

    if args.raw_file:
        results = [apply_normalize(root, args.raw_file, when=when)]
    else:
        results = scan_and_normalize(root, when=when)

    for result in results:
        if result.renamed:
            print(result.new_rel)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
