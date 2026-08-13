"""Scaffold a brand-new, empty wiki vault: folders, git repo, .gitignore,
and (for query-from-anywhere mode) the plugin-registration ``settings.json``.

**Superseded by `enchiridion init`** (#150): `/wiki-init` now calls the Go
binary via `bin/enchiridion`, not this script. It is kept until the Python
layer is retired wholesale — see ADR-0011.

One-time setup, distinct from ``wiki-ingest``, which fills a vault that
already exists — :func:`init_wiki` refuses to run against a directory that
already looks like one (:func:`is_vault`).

Deployment mode (ADR-0004) is the caller's judgment call, never inferred
here: ``query-from-anywhere`` writes ``.claude/settings.json`` registering
``plugin_root`` as a local-directory marketplace; ``dedicated`` skips that
write, since installing a plugin project-scope into someone else's directory
isn't this script's job.

CLI::

    python init_wiki.py <path> --mode query-from-anywhere --plugin-root <dir>
    python init_wiki.py <path> --mode dedicated
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import place
from vault_git import GitError, VaultGit

#: A directory is already a vault if either marker is present.
_MARKERS = ("wiki", ".wiki-root")

_GITIGNORE = (
    "*.rsls\n"
    ".claude/wiki-knowledge/sessions/\n"
    # Search index, gitignored per ADR-0006. Must ALSO be added to Resilio
    # Sync's own ignore list — a gitignore doesn't propagate to the syncer,
    # and a synced SQLite sidecar corrupts.
    ".wiki-knowledge/\n"
)


class InitError(RuntimeError):
    """Raised when scaffolding can't proceed (already a vault, git missing, …)."""


def is_vault(root: Path | str) -> bool:
    root = Path(root)
    return any((root / marker).exists() for marker in _MARKERS)


def _settings_json(plugin_root: str) -> str:
    return json.dumps(
        {
            "extraKnownMarketplaces": {
                "wiki-knowledge-plugin": {
                    "source": {"source": "directory", "path": plugin_root}
                }
            },
            "enabledPlugins": {"wiki-knowledge@wiki-knowledge-plugin": True},
        },
        indent=2,
    ) + "\n"


def init_wiki(
    vault_root: Path | str,
    mode: str,
    plugin_root: str | None = None,
) -> Path:
    """Scaffold ``vault_root`` as a new vault. Returns the vault root.

    ``mode`` is ``"query-from-anywhere"`` (requires ``plugin_root``, the
    plugin's install directory) or ``"dedicated"`` (no ``settings.json``;
    the caller installs the plugin themselves).

    Git comes from :class:`VaultGit` (the one module that shells out to git,
    #126). Its absent-git policy here is a hard dependency: git missing on
    PATH raises :class:`InitError` before any scaffolding, and a git command
    that fails mid-scaffold is translated from :class:`GitError` into
    :class:`InitError` so the CLI reports it cleanly.
    """
    if mode not in ("query-from-anywhere", "dedicated"):
        raise InitError(f"unknown mode {mode!r}")
    if mode == "query-from-anywhere" and not plugin_root:
        raise InitError("query-from-anywhere mode requires plugin_root")

    root = Path(vault_root)
    git = VaultGit(root)
    if not git.available():
        raise InitError("git is required but was not found on PATH")

    if is_vault(root):
        raise InitError(f"{root} already looks like a vault (wiki/ or .wiki-root exists)")

    root.mkdir(parents=True, exist_ok=True)

    for folder in place.KIND_FOLDERS.values():
        kind_dir = root / "wiki" / folder
        kind_dir.mkdir(parents=True, exist_ok=True)
        (kind_dir / ".gitkeep").touch()
    raw_dir = root / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / ".gitkeep").touch()

    (root / ".gitignore").write_text(_GITIGNORE, encoding="utf-8")

    wrote_settings = False
    if mode == "query-from-anywhere":
        assert plugin_root is not None  # guarded above: query-from-anywhere requires plugin_root
        claude_dir = root / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "settings.json").write_text(
            _settings_json(plugin_root), encoding="utf-8"
        )
        wrote_settings = True

    add_paths = ["wiki", ".gitignore", "raw/.gitkeep"]
    if wrote_settings:
        add_paths.append(".claude/settings.json")

    try:
        if not git.is_work_tree():
            git.init()
        git.add(*add_paths)
        git.commit("Initialize wiki vault")
    except GitError as exc:
        raise InitError(str(exc)) from exc

    return root


def _main(argv=None) -> int:  # pragma: no cover - thin CLI wrapper
    import argparse

    parser = argparse.ArgumentParser(description="Scaffold a brand-new wiki vault.")
    parser.add_argument("path", help="target vault directory")
    parser.add_argument(
        "--mode", required=True, choices=("query-from-anywhere", "dedicated")
    )
    parser.add_argument(
        "--plugin-root", help="this plugin's install dir (required for query-from-anywhere)"
    )
    args = parser.parse_args(argv)

    try:
        init_wiki(args.path, args.mode, args.plugin_root)
    except InitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(Path(args.path).resolve())
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
