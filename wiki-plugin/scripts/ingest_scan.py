"""The ingestion sweep — scan ``raw/`` for files that need ingestion.

Two independent gates: derived done-state (computed here) and declared
policy (a human-authored ``.ingestignore``). A raw file is *offered* when
(a) no wiki page's ``raw_source`` points at it, or (b) one does but the raw
file is strictly newer than that page's ``git_date``, or ``git status
--porcelain`` reports it dirty.

``.ingestignore`` is read from the file's own folder only, with **no
ancestor walk** — the same rule ``INGESTION.md`` follows, and what keeps a
hand-written policy file from drifting into a machine-written done-list.

This is the deterministic layer the ``wiki-ingest`` skill shells out to. The
interactive half — the per-file ``yes / skip / never`` prompt — lives in
``wiki-ingest/SKILL.md`` and must run in the *invoking* session: a subagent
has no channel to the user.

CLI::

    python ingest_scan.py                # scan all of raw/
    python ingest_scan.py <folder>       # scan raw/<folder>/
    python ingest_scan.py --json
"""
from __future__ import annotations

import fnmatch
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import page_record
import vault as vault_mod
from vault_git import VaultGit


# --- public types --------------------------------------------------------


@dataclass(frozen=True)
class IngestCandidate:
    """One raw file the sweep wants to offer. ``raw_rel`` is vault-relative.

    ``reason`` is either ``"never-ingested"`` (no page's ``raw_source``
    points at it, so ``back_pointers`` is empty by construction) or
    ``"changed-since-ingestion"`` (pages do point at it, and
    ``back_pointers`` lists them vault-relative — the invoking session
    passes them to ``wiki-ingest`` as a reconciliation hint).
    """

    raw_rel: str
    reason: str
    back_pointers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScanResult:
    """The sweep's verdict on one (vault, folder) pair.

    ``eligible`` is in ``walk_raw`` order. ``ignored`` holds
    ``.ingestignore`` matches, reported rather than silently dropped so the
    sweep can say "3 ignored".
    """

    eligible: list[IngestCandidate]
    ignored: list[str]


# --- .ingestignore parse -------------------------------------------------


def parse_ingestignore(text: str) -> list[str]:
    """Parse ``.ingestignore`` text into its patterns, in order.

    Strips ``#`` comments (full-line and trailing) and blank lines; the rest
    goes to :func:`fnmatch.fnmatchcase` verbatim. ``/``, ``!`` and ``**`` are
    rejected outright, so a bare filename (``literal.md``) and a simple glob
    (``*.tmp``) are the only supported shapes — deliberately, since anything
    richer would raise precedence questions a per-folder policy file has no
    way to answer.
    """
    patterns: list[str] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if "/" in line or "!" in line or "**" in line:
            raise ValueError(
                f".ingestignore patterns must be bare filename globs "
                f"(no '/', no '!', no '**'): {line!r}"
            )
        patterns.append(line)
    return patterns


def load_ingestignore(folder: Path) -> list[str]:
    """Read the ``.ingestignore`` in ``folder``, if any. Empty list when
    absent. This folder only — **no ancestor walk**, so a parent's policy
    never bleeds into a child's files."""
    path = folder / ".ingestignore"
    if not path.is_file():
        return []
    return parse_ingestignore(path.read_text(encoding="utf-8"))


def _matches_ingestignore(filename: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(filename, p) for p in patterns)


# --- raw walk ------------------------------------------------------------


_SKIP_NAMES = frozenset({"INGESTION.md", ".ingestignore"})


def walk_raw(root: Path, folder: str | None = None) -> Iterable[Path]:
    """Yield every file under ``root/raw/`` (or ``root/raw/<folder>``), as
    absolute paths in filesystem order.

    Skips ``INGESTION.md`` and ``.ingestignore`` — instructions and policy,
    not content. A nonexistent folder yields nothing rather than raising.
    """
    raw_root = root / "raw"
    if folder:
        raw_root = raw_root / folder
    if not raw_root.is_dir():
        return
    for path in raw_root.rglob("*"):
        if path.is_file() and path.name not in _SKIP_NAMES:
            yield path


# --- append-an-entry helper ---------------------------------------------


def append_ignore_entry(
    folder: Path,
    pattern: str,
    comment: str | None = None,
) -> None:
    """Append ``pattern`` to ``folder/.ingestignore``, creating it if absent.

    Idempotent — a pattern already present isn't re-added, so a sweep run
    twice doesn't double-list. ``comment``, if given, goes on the same line
    after two spaces. Backs the sweep's ``never`` answer.
    """
    path = folder / ".ingestignore"
    existing = parse_ingestignore(path.read_text(encoding="utf-8")) if path.is_file() else []
    if pattern in existing:
        return
    line = f"{pattern}  # {comment}" if comment else pattern
    if path.is_file():
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    else:
        path.write_text(line + "\n", encoding="utf-8")


# --- back-pointer map ----------------------------------------------------


def _back_pointers_by_raw(
    pages: dict[str, tuple["page_record.PageRecord", str]],
) -> dict[str, list[str]]:
    """``{raw_rel: [page_ref, …]}`` for every page with a ``raw_source``.
    Both sides vault-relative.

    ``page_record`` hands back each ``raw_source`` target already resolved to
    vault-relative by construction (ADR-0009), so there is no re-resolution
    step to write here — unlike the old wiki-relative convention, whose
    ``prefix="wiki"`` cancelling dance used to live in this function.
    """
    out: dict[str, list[str]] = {}
    for page_ref, (rec, _text) in pages.items():
        for key, targets in rec.edges:
            if key != "raw_source":
                continue
            for target in targets:
                out.setdefault(target, []).append(page_ref)
    return out


# --- eligibility ---------------------------------------------------------


def _is_strictly_newer(raw_date: str | None, page_date: str | None) -> bool:
    """``True`` iff ``raw_date > page_date`` (``YYYY-MM-DD`` lexicographic).

    A ``None`` page date — never committed, or not a git repo — fails toward
    ``True``, so the file is offered rather than silently skipped.
    """
    if not page_date:
        return True
    if not raw_date:
        return False
    return raw_date > page_date


# --- main scan -----------------------------------------------------------


def scan(root: Path, folder: str | None = None, git: VaultGit | None = None) -> ScanResult:
    """Walk ``root/raw/`` and return the sweep's verdict (see the module
    docstring for the eligibility rule).

    Policy trumps the eligibility signal: a file matching its own folder's
    ``.ingestignore`` lands in ``ignored`` without being evaluated.

    ``git`` is injectable for tests (an in-memory :class:`VaultGit` fake); it
    defaults to real git over ``root``. The absent-git policy — **fail toward
    offering** — is read off :meth:`VaultGit.last_commit_date` /
    :meth:`VaultGit.porcelain_mentions`, whose lenient defaults this sweep
    relies on.
    """
    pages = vault_mod.Vault(root).pages_with_text()
    back_pointers = _back_pointers_by_raw(pages)
    git = git or VaultGit(root)

    eligible: list[IngestCandidate] = []
    ignored: list[str] = []

    for path in walk_raw(root, folder):
        rel = path.relative_to(root).as_posix()
        # Own folder, no ancestor walk: a raw/emails/.ingestignore does not
        # govern raw/emails/sub/ — that folder needs its own.
        patterns = load_ingestignore(path.parent)
        if _matches_ingestignore(path.name, patterns):
            ignored.append(rel)
            continue

        pointing = back_pointers.get(rel, [])
        if not pointing:
            eligible.append(IngestCandidate(rel, "never-ingested", []))
            continue

        # Ingested at least once. Offer it again only if it has moved on:
        # dirty working tree, or newer than a back-pointer page.
        if git.porcelain_mentions(rel):
            eligible.append(IngestCandidate(rel, "changed-since-ingestion", pointing))
            continue

        raw_date = git.last_commit_date(rel)
        any_strictly_older = any(
            _is_strictly_newer(raw_date, git.last_commit_date(page_rel))
            for page_rel in pointing
        )
        if any_strictly_older:
            eligible.append(IngestCandidate(rel, "changed-since-ingestion", pointing))

    return ScanResult(eligible=eligible, ignored=ignored)


# --- Sweep coordinator ----------------------------------------------------


class Sweep:
    """An in-process facade over one vault's raw/ sweep.

    Depends on :class:`vault.Vault`, never the reverse: ``Vault`` stays pure
    vault I/O, and the sweep's own state (which vault, which folder) lives
    here. Don't reintroduce sweep methods on ``Vault`` — that needs a
    deferred import and puts the dependency back the wrong way round.
    """

    def __init__(self, vault: "vault_mod.Vault"):
        self.vault = vault

    def scan(self, folder: str | None = None) -> ScanResult:
        return scan(self.vault.root, folder)

    def append_ignore_entry(
        self, folder: str, pattern: str, comment: str | None = None
    ) -> None:
        append_ignore_entry(self.vault.root / "raw" / folder, pattern, comment)


# --- CLI -----------------------------------------------------------------


def _record_to_dict(cand: IngestCandidate) -> dict:
    return {
        "kind": "eligible",
        "raw_rel": cand.raw_rel,
        "reason": cand.reason,
        "back_pointers": list(cand.back_pointers),
    }


def _ignored_to_dict(raw_rel: str) -> dict:
    return {"kind": "ignored", "raw_rel": raw_rel}


def _print_table(result: ScanResult) -> None:
    if not result.eligible and not result.ignored:
        print("no eligible files; 0 ignored")
        return
    width = max(
        [len(c.raw_rel) for c in result.eligible] + [len(r) for r in result.ignored] + [10]
    )
    for cand in result.eligible:
        print(f"{cand.raw_rel.ljust(width)}  {cand.reason}")
    if result.ignored:
        print(f"\n{len(result.ignored)} ignored by .ingestignore:")
        for raw_rel in result.ignored:
            print(f"  {raw_rel}")


def _folder_arg(arg: str | None) -> str | None:
    """Normalise a CLI folder argument. ``None`` and ``""`` both mean all of
    ``raw/``; a ``raw/`` prefix is stripped, so ``notes`` and ``raw/notes``
    are interchangeable."""
    if arg is None:
        return None
    if arg in ("", "raw/"):
        return None
    if arg.startswith("raw/"):
        arg = arg[len("raw/"):]
    return arg or None


def _main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "folder", nargs="?", default=None,
        help="scan a single raw/ subfolder; omit to scan all of raw/",
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help="emit JSON Lines (one eligible or ignored record per line)",
    )
    args = parser.parse_args(argv)

    root = vault_mod.resolve_vault_root()
    result = scan(Path(root), _folder_arg(args.folder))

    if args.as_json:
        for cand in result.eligible:
            print(json.dumps(_record_to_dict(cand), ensure_ascii=False))
        for raw_rel in result.ignored:
            print(json.dumps(_ignored_to_dict(raw_rel), ensure_ascii=False))
    else:
        _print_table(result)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
