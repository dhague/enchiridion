"""Write one structured git commit per ingestion/edit (§4).

The commit message is a compounding asset — the audit log, the "what changed
this week" feed and the manager-report source — so it is emitted by this script
(never freehand by the agent) in a single parseable format::

    ingest: <source doc title>

    created: wiki/concept/prepared-statements.md
    updated: wiki/concept/db-connection-pooling.md
    superseded: wiki/source/deploy-capistrano.md -> wiki/source/deploy-github-actions.md
    source-date: 2026-03-01

Git is a **hard dependency**: if ``git`` is absent or the target isn't a git
work tree, :func:`commit` raises :class:`GitError` rather than silently
skipping — the time model and the roadmap features depend on the history being
complete.

A manifest that names a ``raw_source`` is also gated on the page -> stub ->
raw file chain: there must be a ``wiki/source/`` page whose ``raw_source``
points at that file, and every other page the commit stages must carry a
``source`` edge back to that stub. This is the hard block — the validate-time
check in :mod:`ingest` is the agent-time layer, but this is what stops a
violating commit from ever landing in history (#34 point 4). A violation
raises :class:`CommitGateError` before any file is staged.

CLI::

    python commit.py --manifest manifest.json   # commits against the resolved vault
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import chain_of_evidence
import wikipage


class GitError(RuntimeError):
    """Raised when git is unavailable or the target is not a git work tree."""


class CommitGateError(RuntimeError):
    """Raised when a manifest fails the chain-of-evidence gate (#34 point 4).

    Distinct from :class:`GitError` so a caller can tell "the gate rejected
    this manifest" from "git itself failed" — the former is a planning bug
    to fix, the latter is an environment failure to investigate.
    """


@dataclass
class Manifest:
    """The deterministic description of one ingestion/edit's touched files."""

    title: str
    action: str = "ingest"
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    superseded: list[tuple[str, str]] = field(default_factory=list)
    source_date: str | None = None
    #: The raw/ artifact this ingestion is sourced from, if any —
    #: staged automatically so the source document always lands in the same
    #: commit as the pages it produced, without the caller having to remember
    #: to fold it into extra_paths by hand.
    raw_source: str | None = None
    #: Extra paths to stage that aren't a page edit — e.g. the regenerated index.
    extra_paths: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Manifest":
        return cls(
            title=d["title"],
            action=d.get("action", "ingest"),
            created=list(d.get("created", [])),
            updated=list(d.get("updated", [])),
            superseded=[tuple(pair) for pair in d.get("superseded", [])],
            source_date=d.get("source_date"),
            raw_source=d.get("raw_source"),
            extra_paths=list(d.get("extra_paths", [])),
        )

    def staged_paths(self) -> list[str]:
        """Every path this manifest touches, de-duplicated, in a stable order."""
        paths: list[str] = []
        for rel in self.created:
            paths.append(rel)
        for rel in self.updated:
            paths.append(rel)
        for old, new in self.superseded:
            paths.append(old)
            paths.append(new)
        if self.raw_source:
            paths.append(self.raw_source)
        paths.extend(self.extra_paths)
        seen: set[str] = set()
        ordered: list[str] = []
        for rel in paths:
            if rel not in seen:
                seen.add(rel)
                ordered.append(rel)
        return ordered


def build_message(manifest: Manifest) -> str:
    """Render ``manifest`` to the §4 structured commit message. Deterministic."""
    lines = [f"{manifest.action}: {manifest.title}", ""]
    lines += [f"created: {rel}" for rel in manifest.created]
    lines += [f"updated: {rel}" for rel in manifest.updated]
    lines += [f"superseded: {old} -> {new}" for old, new in manifest.superseded]
    if manifest.source_date:
        lines.append(f"source-date: {manifest.source_date}")
    return "\n".join(lines) + "\n"


def _run(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout


def _ensure_git(root: Path) -> None:
    if shutil.which("git") is None:
        raise GitError("git is required but was not found on PATH")
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        raise GitError(f"{root} is not a git work tree")


def _check_chain_of_evidence(root: Path, manifest: Manifest) -> None:
    """Gate the commit on the page -> stub -> raw file chain (#34 point 4),
    via the shared :func:`chain_of_evidence.check`.

    A commit that stages a raw source must also stage a ``wiki/source/`` page
    whose ``raw_source`` points at that file, and every other page the commit
    stages must carry a ``source`` edge back to that stub. This is the hard
    block — the validate-time check in :mod:`ingest` is the agent-time layer,
    but this is what stops a violating commit from ever landing in history.
    Calling the same shared check as :mod:`ingest` (rather than a hand
    -mirrored copy) is what makes a divergence between the two impossible.

    When ``manifest.raw_source`` is unset (a synthesis save, or any other
    caller that has no raw artifact to demand a stub for), the gate is a
    no-op. Pages are read from disk: by the time :func:`commit` runs, the
    caller has already written them; a staged page missing from disk is
    silently skipped — the caller set the commit up wrong, and the missing
    -file signal belongs to the caller, not this gate.
    """
    if manifest.raw_source is None:
        return

    staged: dict[str, wikipage.WikiPage] = {}
    for rel in list(manifest.created) + list(manifest.updated):
        page_path = root / rel
        if page_path.is_file():
            staged[rel] = wikipage.WikiPage(page_path.read_text(encoding="utf-8"))

    errors = chain_of_evidence.check(staged, manifest.raw_source)
    if errors:
        raise CommitGateError("commit gated: " + "; ".join(errors))


def commit(vault_root: Path | str, manifest: Manifest) -> str:
    """Stage the manifest's paths and write one structured commit. Returns the SHA."""
    root = Path(vault_root)
    _ensure_git(root)

    paths = manifest.staged_paths()
    _check_chain_of_evidence(root, manifest)
    if paths:
        _run(root, "add", "--", *paths)
    _run(root, "commit", "-m", build_message(manifest))
    return _run(root, "rev-parse", "HEAD").strip()


def _main(argv=None) -> int:  # pragma: no cover - thin CLI wrapper
    import argparse
    import json

    import vault

    parser = argparse.ArgumentParser(description="Structured git commit per manifest.")
    parser.add_argument("--manifest", required=True, help="path to a manifest JSON file")
    args = parser.parse_args(argv)

    data = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    manifest = Manifest.from_dict(data)
    root = vault.resolve_vault_root()
    print(commit(root, manifest))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
