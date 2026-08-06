"""Scaffold a brand-new, empty wiki vault: folders, index, git repo, .gitignore,
and (for query-from-anywhere mode) the plugin-registration ``settings.json``.

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
import shutil
import subprocess
import sys
from pathlib import Path

import build_index

#: The plugin-fixed kind-folder set (wiki-conventions, "Vault structure").
KIND_FOLDERS = ("concept", "entity", "source", "synthesis")

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


def _run_git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise InitError(
            f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout


def _is_git_repo(root: Path) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def init_wiki(
    vault_root: Path | str,
    mode: str,
    plugin_root: str | None = None,
) -> Path:
    """Scaffold ``vault_root`` as a new vault. Returns the written index path.

    ``mode`` is ``"query-from-anywhere"`` (requires ``plugin_root``, the
    plugin's install directory) or ``"dedicated"`` (no ``settings.json``;
    the caller installs the plugin themselves).
    """
    if mode not in ("query-from-anywhere", "dedicated"):
        raise InitError(f"unknown mode {mode!r}")
    if mode == "query-from-anywhere" and not plugin_root:
        raise InitError("query-from-anywhere mode requires plugin_root")
    if shutil.which("git") is None:
        raise InitError("git is required but was not found on PATH")

    root = Path(vault_root)
    if is_vault(root):
        raise InitError(f"{root} already looks like a vault (wiki/ or .wiki-root exists)")

    root.mkdir(parents=True, exist_ok=True)

    for kind in KIND_FOLDERS:
        kind_dir = root / "wiki" / kind
        kind_dir.mkdir(parents=True, exist_ok=True)
        (kind_dir / ".gitkeep").touch()
    raw_dir = root / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / ".gitkeep").touch()

    index_path = build_index.write_index(root)

    (root / ".gitignore").write_text(_GITIGNORE, encoding="utf-8")

    wrote_settings = False
    if mode == "query-from-anywhere":
        claude_dir = root / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "settings.json").write_text(
            _settings_json(plugin_root), encoding="utf-8"
        )
        wrote_settings = True

    if not _is_git_repo(root):
        _run_git(root, "init")

    add_paths = ["wiki", ".gitignore", "raw/.gitkeep"]
    if wrote_settings:
        add_paths.append(".claude/settings.json")
    _run_git(root, "add", "--", *add_paths)
    _run_git(root, "commit", "-m", "Initialize wiki vault")

    return index_path


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
        index_path = init_wiki(args.path, args.mode, args.plugin_root)
    except InitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(Path(args.path).resolve())
    print(index_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
