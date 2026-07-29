"""The ingestion sweep — scan ``raw/`` for files that need ingestion.

Per [#26](https://github.com/dhague/enchiridion/issues/26): a sweep over
``raw/`` gated by two independent signals — derived done-state (this
module computes) and declared policy (a human-authored ``.ingestignore``).
A raw file is *offered* for ingestion when (a) no wiki page's
``raw_source`` points at it, **or** (b) one does but the raw file is
strictly newer than that page's ``git_date``, or ``git status
--porcelain`` reports it dirty. ``.ingestignore`` is per-folder, in its
own folder only, with no ancestor walk — exactly [#13]'s rule for
``INGESTION.md``, kept that way so the policy file can never start
acting like a machine-written done-list (which the ticket is the
deliberate alternative to).

This is the deterministic layer the ``wiki-ingest`` skill shells out to.
The interactive half — the per-file three-way ``yes / skip / never``
prompt — lives in ``wiki-ingest/SKILL.md`` and runs in the *invoking*
session, not a subagent, per [#18]'s finding that a subagent has no
channel to the user.

CLI::

    python ingest_scan.py                # scan all of raw/
    python ingest_scan.py <folder>       # scan raw/<folder>/
    python ingest_scan.py --json
"""
from __future__ import annotations

import fnmatch
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import page_record
import vault as vault_mod
import wikipage


# --- public types --------------------------------------------------------


@dataclass(frozen=True)
class IngestCandidate:
    """One raw file the sweep wants to offer for ingestion.

    ``raw_rel`` is vault-relative (``raw/notes/foo.md``). ``reason`` is
    one of:

    * ``"never-ingested"`` — no page's ``raw_source`` points at it
      (back-pointers, by construction, are empty).
    * ``"changed-since-ingestion"`` — at least one page does, but the
      raw file is strictly newer than the page's ``git_date`` **or**
      ``git status --porcelain`` reports it dirty.
      ``back_pointers`` lists every such page (vault-relative) so the
      invoking session can pass them to ``wiki-ingest`` as a
      reconciliation hint.
    """

    raw_rel: str
    reason: str
    back_pointers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScanResult:
    """The sweep's verdict on one (vault, folder) pair.

    ``eligible`` is in the order ``walk_raw`` yields files. ``ignored``
    is files ``.ingestignore`` matched, reported separately so the
    sweep can say "3 ignored" rather than silently omitting them.
    """

    eligible: list[IngestCandidate]
    ignored: list[str]


# --- .ingestignore parse -------------------------------------------------


def parse_ingestignore(text: str) -> list[str]:
    """Read an ``.ingestignore`` text and return its patterns, in order.

    Strips ``#`` comments (full-line and trailing) and blank lines. The
    patterns are passed to :func:`fnmatch.fnmatchcase` verbatim — and
    only filenames: ``/`` (path separators), ``!`` (negation) and
    ``**`` (recursive globs) are rejected outright so there is never a
    precedence question to resolve. A single bare-filename rule
    (``literal.md``) and a simple glob (``*.tmp``) are the only shapes
    the syntax supports, by design — anything richer, the ticket notes,
    has nothing to match against a per-folder policy file.
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
    """Read the ``.ingestignore`` in ``folder``, if any. Empty list when absent.

    Lookup is the file's own folder only — there is **no ancestor walk**
    (per [#13]'s rule, shared with ``INGESTION.md``), so a parent's
    policy never bleeds into a child's files.
    """
    path = folder / ".ingestignore"
    if not path.is_file():
        return []
    return parse_ingestignore(path.read_text(encoding="utf-8"))


def _matches_ingestignore(filename: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(filename, p) for p in patterns)


# --- raw walk ------------------------------------------------------------


_SKIP_NAMES = frozenset({"INGESTION.md", ".ingestignore"})


def walk_raw(root: Path, folder: str | None = None) -> Iterable[Path]:
    """Yield every file under ``root/raw/`` (or ``root/raw/<folder>`` if given).

    Skips ``INGESTION.md`` and ``.ingestignore`` — both are instructions /
    policy, not content. Yields absolute :class:`Path`s in filesystem
    order. A nonexistent folder yields nothing rather than raising — the
    CLI's "scan a folder" form is a no-op when the folder is absent.
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
    """Append ``pattern`` to ``folder/.ingestignore`` (creating it if absent).

    Idempotent: a pattern already present is not added again (so a
    sweep run twice doesn't double-list). An optional ``comment`` is
    appended on the same line, separated by two spaces — the standard
    ``fnmatch``/``gitignore`` comment style. Used by ``never`` answers
    in the sweep interaction, with ``# ingested before back-pointers
    were mandatory`` on the legacy set.
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


# --- git helpers ---------------------------------------------------------


def _git_last_commit_date(repo: Path, rel: str) -> str | None:
    """The last commit date of ``rel`` in ``repo`` (``YYYY-MM-DD``), or ``None``.

    ``None`` when git is unavailable, ``rel`` has never been committed,
    or git returns non-zero. Callers treat a missing date as "fail
    toward offering" — the only safe direction for a per-run signal
    that has to err without losing data.
    """
    if shutil.which("git") is None:
        return None
    proc = subprocess.run(
        [
            "git", "-C", str(repo),
            "log", "-1", "--format=%ad", "--date=short", "--", rel,
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout.strip().splitlines()[-1]


def _git_porcelain_mentions(repo: Path, rel: str) -> bool:
    """``True`` iff ``git status --porcelain`` reports ``rel`` modified or untracked.

    The cheapest "edited since last commit" signal — one subprocess per
    offered file at most, called only after the file has cleared the
    back-pointer and the ``.ingestignore`` checks. Untracked files
    (``??``) count too: a brand-new file isn't in git's index at all,
    but the sweep is precisely there to find it.
    """
    if shutil.which("git") is None:
        return False
    proc = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--", rel],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return False
    return bool(proc.stdout.strip())


# --- back-pointer map ----------------------------------------------------


def _back_pointers_by_raw(
    pages: dict[str, tuple["page_record.PageRecord", str]],
) -> dict[str, list[str]]:
    """``{raw_rel: [page_rel, …]}`` for every page with a ``raw_source``.

    ``raw_rel`` is vault-relative (``raw/notes/foo.md``); the page rels
    (``pages``' keys, already ``wiki/``-prefixed per
    :meth:`vault.Vault.pages_with_text`) are too. ``page_record`` already
    hands back each ``raw_source`` target decoded and wiki-root-relative
    (e.g. ``raw/foo (draft).md``); :func:`wikipage.resolve_link_dest` with
    ``prefix="wiki"`` is the only step left to reach the vault-relative
    form this map is keyed by.
    """
    out: dict[str, list[str]] = {}
    for rel, (rec, _text) in pages.items():
        for key, targets in rec.edges:
            if key != "raw_source":
                continue
            for target in targets:
                raw_rel = wikipage.resolve_link_dest(target, "", prefix="wiki")
                out.setdefault(raw_rel, []).append(rel)
    return out


# --- eligibility ---------------------------------------------------------


def _is_strictly_newer(raw_date: str | None, page_date: str | None) -> bool:
    """``True`` iff ``raw_date > page_date`` (``YYYY-MM-DD`` lexicographic).

    ``None`` for the page — a page that has never been committed, or
    lives in a vault that isn't a git repo — fails toward ``True``:
    the only safe direction, so the file is offered rather than
    silently skipped.
    """
    if not page_date:
        return True
    if not raw_date:
        return False
    return raw_date > page_date


# --- main scan -----------------------------------------------------------


def scan(root: Path, folder: str | None = None) -> ScanResult:
    """Walk ``root/raw/`` and return the sweep's verdict.

    A file is **eligible** when (a) no page's ``raw_source`` points at
    it (``never-ingested``), or (b) one does but the raw file is
    strictly newer than at least one back-pointer page's ``git_date``,
    or ``git status --porcelain`` reports it dirty
    (``changed-since-ingestion``). A file matching its *own folder's*
    ``.ingestignore`` is in ``ignored`` — the policy trumps the
    eligibility signal. ``.ingestignore`` is the file's own folder
    only; a parent's policy never bleeds in.
    """
    pages = vault_mod.Vault(root).pages_with_text()
    back_pointers = _back_pointers_by_raw(pages)

    eligible: list[IngestCandidate] = []
    ignored: list[str] = []

    for path in walk_raw(root, folder):
        rel = path.relative_to(root).as_posix()
        # Per-folder policy: the file's *own* folder, no ancestor walk.
        # A ``raw/emails/.ingestignore`` does not govern files in
        # ``raw/emails/sub/``; that folder would need its own.
        patterns = load_ingestignore(path.parent)
        if _matches_ingestignore(path.name, patterns):
            ignored.append(rel)
            continue

        pointing = back_pointers.get(rel, [])
        if not pointing:
            eligible.append(IngestCandidate(rel, "never-ingested", []))
            continue

        # Signal 2: back-pointers exist, so the file has been ingested
        # at least once. Offer it when its raw state is strictly newer
        # than the page's, or the working tree is dirty.
        if _git_porcelain_mentions(root, rel):
            eligible.append(IngestCandidate(rel, "changed-since-ingestion", pointing))
            continue

        raw_date = _git_last_commit_date(root, rel)
        any_strictly_older = any(
            _is_strictly_newer(raw_date, _git_last_commit_date(root, page_rel))
            for page_rel in pointing
        )
        if any_strictly_older:
            eligible.append(IngestCandidate(rel, "changed-since-ingestion", pointing))

    return ScanResult(eligible=eligible, ignored=ignored)


# --- Sweep coordinator ----------------------------------------------------


class Sweep:
    """An in-process facade over one vault's raw/ sweep (#59).

    Depends on :class:`vault.Vault`, not the other way around — closing
    the seam #41 left open, where ``Vault`` carried ``scan_raw`` and
    ``append_ignore_entry`` as thin wrappers around this module via a
    deferred import. ``Vault`` stays pure vault I/O; this is where the
    raw/ sweep's own state (which vault, which folder) lives.
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
    """Normalise a CLI folder argument.

    ``None`` and the empty string both mean "all of ``raw/``". A
    ``raw/`` prefix is stripped so callers can pass either ``notes``
    (the natural form) or ``raw/notes`` (the absolute-vault form)
    interchangeably.
    """
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
