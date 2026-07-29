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

import posixpath
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

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


def _link_path(link: str) -> str | None:
    """Return a markdown link's decoded, anchor-free destination path.

    ``None`` when ``link`` isn't a markdown link at all. Decoded rather than
    raw: a preserved raw filename with a space or paren in it is
    percent-encoded in the destination, and only the decoded form compares
    against a path on disk. Mirrors :func:`ingest._link_path` so the gate
    agrees with the validator.
    """
    match = next(iter(wikipage.iter_links(link)), None)
    return match.decoded_path if match is not None else None


def _check_chain_of_evidence(root: Path, manifest: Manifest) -> None:
    """Gate the commit on the page -> stub -> raw file chain (#34 point 4).

    A commit that stages a raw source must also stage a ``wiki/source/`` page
    whose ``raw_source`` points at that file, and every other page the commit
    stages must carry a ``source`` edge back to that stub. This is the hard
    block — the validate-time check in :mod:`ingest` is the agent-time layer,
    but this is what stops a violating commit from ever landing in history.

    When ``manifest.raw_source`` is unset (a synthesis save, or any other
    caller that has no raw artifact to demand a stub for), the gate is a
    no-op. Pages are read from disk: by the time :func:`commit` runs, the
    caller has already written them.
    """
    if manifest.raw_source is None:
        return

    raw = posixpath.normpath(manifest.raw_source)
    staged = list(manifest.created) + list(manifest.updated)

    # (a) Find the stub: the first staged page in `wiki/source/` whose
    # on-disk `raw_source` resolves to `manifest.raw_source`. A missing
    # stub is a hard block.
    stub_rel: str | None = None
    for rel in staged:
        if posixpath.dirname(rel) != "wiki/source":
            continue
        page_path = root / rel
        if not page_path.is_file():
            continue
        page = wikipage.WikiPage(page_path.read_text(encoding="utf-8"))
        link = page.get("raw_source")
        if not isinstance(link, str):
            continue
        dest = _link_path(link)
        if dest is None:
            continue
        if posixpath.normpath(posixpath.join(posixpath.dirname(rel), dest)) == raw:
            stub_rel = rel
            break

    if stub_rel is None:
        raise CommitGateError(
            f"commit gated: manifest.raw_source {manifest.raw_source!r} needs a "
            f"wiki/source/ page whose raw_source points at it — every ingested "
            f"raw file gets a stand-in, even a thin stub"
        )

    # (b) Every other staged page must carry a `source` edge to the stub.
    # The stub is exempt — nothing sources itself.
    for rel in staged:
        if rel == stub_rel:
            continue
        page_path = root / rel
        if not page_path.is_file():
            # The page this commit stages should be on disk by the time we
            # get here. If it isn't, the caller has set up the commit wrong;
            # the gate is not the place to police that — the missing-file
            # signal belongs to the caller, not the commit.
            continue
        page = wikipage.WikiPage(page_path.read_text(encoding="utf-8"))
        source_edges = page.get("source")
        page_dir = posixpath.dirname(rel)
        targets: set[str] = set()
        if isinstance(source_edges, list):
            for link in source_edges:
                dest = _link_path(link) if isinstance(link, str) else None
                if dest is not None:
                    targets.add(
                        posixpath.normpath(posixpath.join(page_dir, dest))
                    )
        if stub_rel not in targets:
            raise CommitGateError(
                f"commit gated: {rel} needs a source edge to the stub {stub_rel}"
            )


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
