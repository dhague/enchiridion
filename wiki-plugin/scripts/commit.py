"""Write one structured git commit per ingestion/edit.

The commit message is a compounding asset — audit log, "what changed this
week" feed, manager-report source — so it is emitted here, never freehand by
the agent. This docstring is the format's only specification::

    ingest: <source doc title>

    created: wiki/concept/prepared-statements.md
    updated: wiki/concept/db-connection-pooling.md
    superseded: wiki/source/deploy-capistrano.md -> wiki/source/deploy-github-actions.md
    source-date: 2026-03-01

Git is a **hard dependency**: absent ``git``, or a target that isn't a work
tree, raises :class:`GitError` rather than silently skipping — the time model
depends on the history being complete.

A manifest naming a ``raw_source`` is additionally gated on
:func:`chain_of_evidence.check`, raising :class:`CommitGateError` before
anything is staged. This is the hard block; :mod:`ingest` runs the same check
earlier, at plan-validation time, as a courtesy to the agent.

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
    """Raised when a manifest fails the chain-of-evidence gate.

    Distinct from :class:`GitError`: a rejected manifest is a planning bug,
    a git failure is an environment problem.
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
    #: The raw/ artifact this ingestion is sourced from, if any. Staged
    #: automatically, so the source document always lands in the same commit
    #: as the pages it produced.
    raw_source: str | None = None
    #: Extra paths to stage that aren't a page edit — e.g. the regenerated index.
    #: (``raw_source`` is staged automatically; it needn't be repeated here.)
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
    """Render ``manifest`` to the structured commit message (see module
    docstring for the format). Deterministic."""
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
    """Gate the commit on :func:`chain_of_evidence.check`.

    No-op when ``manifest.raw_source`` is unset (a synthesis save has no raw
    artifact to demand a stub for). Pages are read from disk — the caller has
    already written them by the time :func:`commit` runs. A staged page
    missing from disk is silently skipped: that's the caller's bug to report,
    not this gate's.
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
