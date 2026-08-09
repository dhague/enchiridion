"""Per-vault OpenCode install for the wiki-knowledge plugin (#91).

Claude Code discovers a plugin through a marketplace; OpenCode has no
equivalent, so installing wiki-knowledge on OpenCode is an explicit per-vault
step. This script writes the whole surface the vault needs:

- ``<target>/.opencode/agents/*.md`` + ``.opencode/commands/*.md`` —
  generated from the canonical Claude Code agent files by
  ``scripts/generate-opencode.py`` (invoked as a subprocess, so this module
  never needs to import a hyphen-named script).
- ``<target>/wiki-knowledge/config.json`` — the marker file carrying
  ``plugin_root``, OpenCode's replacement for CC's ``${CLAUDE_PLUGIN_ROOT}``
  substitution (the host-neutral skills locate the plugin root via this
  file, #90).
- ``<target>/wiki-knowledge/model-config.json`` — the model mapping used for
  this install, persisted so it doubles as the documented override path:
  edit it and re-run with ``--model-config <path>``.
- ``<target>/plugins/session-tracker.ts`` — copied from the plugin's canonical
  source (``wiring/opencode/plugins/``, #92).
- the vault's ``opencode.json`` — ``skills.paths`` pointing at the plugin's
  ``skills/`` directory (shared, not copied), merged into any existing
  config rather than clobbering it.

``<target>`` is the vault's ``.opencode/`` by default (dedicated mode); the
``--global`` flag targets ``~/.config/opencode/`` instead (query-from-anywhere
mode, ADR-0004).

CLI::

    python install-opencode.py --plugin-root <plugin-dir>
    python install-opencode.py --plugin-root <plugin-dir> --global
    python install-opencode.py --plugin-root <plugin-dir> --model-config models.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


class InstallError(RuntimeError):
    """Raised when the install can't proceed (bad plugin root, bad config, …)."""


def home_dir() -> Path:
    """The user's home directory — a function so tests can monkeypatch it."""
    return Path.home()


def install_targets(
    global_mode: bool,
    cwd: Path | str | None = None,
    home: Path | str | None = None,
) -> tuple[Path, Path]:
    """Return ``(dotdir, config_file)`` for the install.

    Dedicated mode installs into the vault's own ``.opencode/`` and merges the
    vault's ``opencode.json``; ``--global`` (query-from-anywhere) uses
    ``~/.config/opencode/`` for both.
    """
    if global_mode:
        base = Path(home if home is not None else home_dir()) / ".config" / "opencode"
        return base, base / "opencode.json"
    cwd_path = Path(cwd if cwd is not None else Path.cwd())
    return cwd_path / ".opencode", cwd_path / "opencode.json"


def model_config_from_file(path: Path | str) -> dict:
    """Read a ``--model-config`` JSON object. Raises :class:`InstallError` on
    anything that isn't a JSON object. May be partial — models omitted fall
    back to the documented defaults at generation time.
    """
    config_path = Path(path)
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InstallError(f"model config {config_path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise InstallError(f"model config {config_path} must be a JSON object")
    return data


def prompt_model_config(
    models: list[str],
    defaults: dict,
    prompt=input,  # noqa: A002 - injectable for tests
) -> dict:
    """Ask the user for one ``provider/model-id`` per canonical model name.
    Blank input falls back to the documented default for that model.
    """
    mapping = {}
    for model in models:
        default = defaults.get(model, "")
        answer = prompt(
            f"OpenCode model id for {model!r} (default {default!r}): ",
        ).strip()
        mapping[model] = answer or default
    return mapping


def resolve_models(
    defaults: dict,
    config_path: Path | str | None,
    prompt=input,  # noqa: A002 - injectable for tests
) -> dict:
    """The model mapping for this install: the ``--model-config`` file when
    given (the documented override path), otherwise an interactive prompt over
    the canonical model names with the documented defaults.
    """
    if config_path is not None:
        return model_config_from_file(config_path)
    return prompt_model_config(list(defaults), defaults, prompt=prompt)


def merge_opencode_config(existing: dict, skills_dir: str) -> dict:
    """Merge ``skills.paths`` (plus the schema marker) into an existing
    OpenCode config without touching anything else. Idempotent: a second
    merge doesn't add a duplicate path.
    """
    config = dict(existing)
    skills = config.setdefault("skills", {})
    if not isinstance(skills, dict):
        raise InstallError("existing opencode.json 'skills' must be an object")
    paths = skills.setdefault("paths", [])
    if not isinstance(paths, list):
        raise InstallError("existing opencode.json 'skills.paths' must be an array")
    if skills_dir not in paths:
        paths.append(skills_dir)
    config.setdefault("$schema", "https://opencode.ai/config.json")
    return config


def _run_generate(argv: list[str]) -> None:
    """Run ``generate-opencode.py`` (full argv incl. interpreter + script path);
    raise :class:`InstallError` on a non-zero exit."""
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        raise InstallError(
            f"generate-opencode.py failed ({proc.returncode}): {proc.stderr.strip()}"
        )


def _fetch_default_models(plugin_root: Path) -> dict:
    """The documented model defaults, from generate-opencode.py — the single
    source of truth for which canonical model names exist and their mappings."""
    proc = subprocess.run(
        [
            sys.executable,
            str(plugin_root / "scripts" / "generate-opencode.py"),
            "--default-models",
            "--plugin-root",
            str(plugin_root),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise InstallError(
            f"generate-opencode.py --default-models failed ({proc.returncode}): "
            f"{proc.stderr.strip()}"
        )
    data = json.loads(proc.stdout.strip())
    if not isinstance(data, dict):
        raise InstallError("generate-opencode.py --default-models returned a non-object")
    return data


def install(
    plugin_root: Path | str,
    models: dict,
    *,
    global_mode: bool = False,
    cwd: Path | str | None = None,
    home: Path | str | None = None,
    run_generate=_run_generate,
) -> Path:
    """Install the OpenCode wiring into the vault (or, with ``global_mode``,
    into ``~/.config/opencode/``). Returns the target directory.

    Runs the generator first (so a generation failure aborts before anything
    is written), then writes the marker, copies the session-tracker plugin,
    and merges ``skills.paths`` into ``opencode.json``.
    """
    root = Path(plugin_root)
    if not (root / "scripts" / "generate-opencode.py").is_file():
        raise InstallError(
            f"no scripts/generate-opencode.py under plugin root {root}"
        )
    tracker = root / "wiring" / "opencode" / "plugins" / "session-tracker.ts"
    if not tracker.is_file():
        raise InstallError(
            f"no wiring/opencode/plugins/session-tracker.ts under plugin root {root}"
        )

    dotdir, config_file = install_targets(global_mode, cwd=cwd, home=home)
    for subdir in ("agents", "commands", "plugins", "wiki-knowledge"):
        (dotdir / subdir).mkdir(parents=True, exist_ok=True)

    model_config_path = dotdir / "wiki-knowledge" / "model-config.json"
    model_config_path.write_text(
        json.dumps(models, indent=2) + "\n", encoding="utf-8",
    )

    generate_argv = [
        sys.executable,
        str(root / "scripts" / "generate-opencode.py"),
        "--plugin-root", str(root),
        "--model-config", str(model_config_path),
        "--output", str(dotdir),
    ]
    run_generate(generate_argv)

    marker = dotdir / "wiki-knowledge" / "config.json"
    marker.write_text(
        json.dumps({"plugin_root": str(root.resolve())}, indent=2) + "\n",
        encoding="utf-8",
    )

    shutil.copy2(tracker, dotdir / "plugins" / "session-tracker.ts")

    existing: dict = {}
    if config_file.is_file():
        try:
            existing = json.loads(config_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InstallError(
                f"existing {config_file} is not valid JSON "
                f"(JSONC configs can't be merged safely): {exc}"
            ) from exc
        if not isinstance(existing, dict):
            raise InstallError(f"existing {config_file} must be a JSON object")

    merged = merge_opencode_config(existing, str(root / "skills"))
    config_file.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")

    return dotdir


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    parser = argparse.ArgumentParser(
        description="Install the wiki-knowledge plugin's OpenCode wiring into "
        "a vault (or ~/.config/opencode with --global).",
    )
    parser.add_argument(
        "--plugin-root", required=True,
        help="the wiki-knowledge plugin directory (contains scripts/, skills/, wiring/)",
    )
    parser.add_argument(
        "--global", dest="global_mode", action="store_true",
        help="install into ~/.config/opencode/ (query-from-anywhere mode)",
    )
    parser.add_argument(
        "--model-config",
        help="JSON file mapping canonical model names to provider/model-id; "
        "overrides the interactive prompt (edit .opencode/wiki-knowledge/"
        "model-config.json from a previous install and pass it here)",
    )
    args = parser.parse_args(argv)

    try:
        root = Path(args.plugin_root)
        defaults = _fetch_default_models(root)
        models = resolve_models(defaults, args.model_config, prompt=input)
        dotdir = install(
            root, models,
            global_mode=args.global_mode,
            run_generate=_run_generate,
        )
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Installed wiki-knowledge OpenCode wiring into {dotdir}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
