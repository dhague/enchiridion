"""TDD for assemble-opencode-package.py — the release-time npm package
assembly (#327).

Covers the layout contract (agents/commands/skills/plugins/runtime/templates
all present), version coupling (package.json == plugin.json), idempotence
(running twice yields the same tree), and failure propagation (missing
canonical source or a failing generator aborts the assembly). The real
subprocess path is exercised against a stub generate-opencode.py in a fixture
plugin root, mirroring test_install_opencode.py.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

# Load scripts/assemble-opencode-package.py by file path (hyphen, not
# underscore), matching the sibling test modules.
_HYPHEN_PATH = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "assemble-opencode-package.py",
)
_spec = importlib.util.spec_from_file_location(
    "assemble_opencode_package", os.path.abspath(_HYPHEN_PATH),
)
assert _spec is not None and _spec.loader is not None
assemble_opencode_package = importlib.util.module_from_spec(_spec)
sys.modules["assemble_opencode_package"] = assemble_opencode_package
_spec.loader.exec_module(assemble_opencode_package)

SKILLS = assemble_opencode_package.SKILLS

DEFAULT_MODELS = {
    "sonnet": "anthropic/claude-sonnet-4-5",
    "haiku": "anthropic/claude-haiku-4-5",
}


# The fixture's generate-opencode.py: writes a minimal agents/ + commands/
# tree into --output and answers --default-models with the default mapping.
# Errors (and thus the assembly's error propagation) are exercised by other
# fixtures that swap this file out.
def _fixture_generate_source() -> str:
    skills = ", ".join(f'"{s}"' for s in SKILLS)
    return "\n".join([
        "import json, sys",
        "from pathlib import Path",
        "args = sys.argv[1:]",
        'if "--default-models" in args:',
        "    print(%r)" % (json.dumps(DEFAULT_MODELS),),
        "    sys.exit(0)",
        'out = Path(args[args.index("--output") + 1])',
        'for name in ("wiki-ingest", "wiki-researcher"):',
        '    p = out / "agents" / f"{name}.md"',
        "    p.parent.mkdir(parents=True, exist_ok=True)",
        '    p.write_text(f"---\\nname: {name}\\n---\\n")',
        f"for skill in ({skills}):",
        '    p = out / "commands" / f"{skill}.md"',
        "    p.parent.mkdir(parents=True, exist_ok=True)",
        '    p.write_text(f"---\\ndescription: {skill}\\n---\\n")',
        "",
    ])


_FIXTURE_TRACKER = "export const SessionTracker: Plugin = () => ({})\n"
_FIXTURE_ENCHIRIDION = "export const WikiEnchiridion: Plugin = async () => ({})\n"
_FIXTURE_OPENCODE_PKG = {
    "name": "wiki-knowledge-opencode-wiring",
    "private": True,
    "version": "0.0.0",
    "devDependencies": {"@opencode-ai/plugin": "^1.18.15"},
}

_FIXTURE_PLUGIN_JSON = {
    "name": "wiki-knowledge",
    "version": "0.9.5",
    "description": "Fixture plugin description.",
    "author": {"name": "Fixture Author", "email": "fixture@example.com"},
}

# The committed durable source: the package.json the assembly script modifies
# in place (version only). Everything else in the package is generated.
_FIXTURE_PACKAGE_JSON = {
    "name": "@dhague/wiki-knowledge",
    "version": "0.0.0",
    "description": "Fixture package description.",
    "bin": {"wiki-knowledge": "bin/deploy.js"},
    "files": ["agents", "commands", "skills", "plugins", "wiki-knowledge", "templates", "bin"],
    "engines": {"node": ">=22.12.0"},
}


@pytest.fixture
def plugin_root(tmp_path):
    """A plugin-shaped fixture: minimal stub agents/skills/wiring, a stub
    generate-opencode.py, and a bundled runtime so assembly has every
    canonical source it validates."""
    root = tmp_path / "plugin"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "generate-opencode.py").write_text(
        _fixture_generate_source(), encoding="utf-8",
    )
    (scripts / "cli.cjs").write_bytes(b"// fixture cli.cjs\n")
    (scripts / "node-sqlite3-wasm.wasm").write_bytes(b"\x00asm fixture\n")

    for skill in SKILLS:
        skill_dir = root / "skills" / skill
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill}\ndescription: The {skill} capability.\n---\n\nBody.\n",
            encoding="utf-8",
        )

    wiring = root / "wiring" / "opencode" / "plugins"
    wiring.mkdir(parents=True)
    (wiring / "session-tracker.ts").write_text(_FIXTURE_TRACKER, encoding="utf-8")
    (wiring / "wiki-enchiridion.ts").write_text(_FIXTURE_ENCHIRIDION, encoding="utf-8")
    wiring_pkg = root / "wiring" / "opencode" / "package.json"
    wiring_pkg.write_text(json.dumps(_FIXTURE_OPENCODE_PKG), encoding="utf-8")

    plugin_json = root / ".claude-plugin" / "plugin.json"
    plugin_json.parent.mkdir(parents=True)
    plugin_json.write_text(json.dumps(_FIXTURE_PLUGIN_JSON), encoding="utf-8")
    return root


def _make_package_dir(tmp_path: Path, name: str = "pkg") -> Path:
    """Scaffold a package dir carrying the committed durable package.json —
    the state assembly runs against in the real repo (the committed source it
    modifies in place)."""
    pkg = tmp_path / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "package.json").write_text(
        json.dumps(_FIXTURE_PACKAGE_JSON) + "\n", encoding="utf-8",
    )
    return pkg


def _run_assemble(plugin_root, package_dir):
    return assemble_opencode_package.assemble(plugin_root, package_dir)


def _relpaths(package_dir: Path) -> set[str]:
    return {
        p.relative_to(package_dir).as_posix()
        for p in package_dir.rglob("*")
        if p.is_file()
    }


# ---------------------------------------------------------------------------
# layout — every generated/copied piece lands where the deployer expects
# ---------------------------------------------------------------------------


def test_assemble_produces_expected_layout(tmp_path, plugin_root):
    pkg = _make_package_dir(tmp_path)
    _run_assemble(plugin_root, pkg)

    assert {"agents/wiki-ingest.md", "agents/wiki-researcher.md"} <= _relpaths(pkg)
    for skill in SKILLS:
        assert f"commands/{skill}.md" in _relpaths(pkg)
        assert f"skills/{skill}/SKILL.md" in _relpaths(pkg)
    assert "plugins/session-tracker.ts" in _relpaths(pkg)
    assert "plugins/wiki-enchiridion.ts" in _relpaths(pkg)
    assert {"wiki-knowledge/cli.cjs", "wiki-knowledge/node-sqlite3-wasm.wasm"} <= _relpaths(pkg)
    assert "templates/config.json" in _relpaths(pkg)
    assert "templates/model-config.json" in _relpaths(pkg)
    assert "templates/opencode-deps.json" in _relpaths(pkg)


def test_assemble_copies_runtime_bytes_verbatim(tmp_path, plugin_root):
    pkg = _make_package_dir(tmp_path)
    _run_assemble(plugin_root, pkg)
    assert (pkg / "wiki-knowledge" / "cli.cjs").read_bytes() == b"// fixture cli.cjs\n"
    assert (pkg / "wiki-knowledge" / "node-sqlite3-wasm.wasm").read_bytes() == b"\x00asm fixture\n"


def test_assemble_copies_session_tracker_verbatim(tmp_path, plugin_root):
    pkg = _make_package_dir(tmp_path)
    _run_assemble(plugin_root, pkg)
    assert (pkg / "plugins" / "session-tracker.ts").read_text(encoding="utf-8") == _FIXTURE_TRACKER


def test_assemble_copies_wiki_enchiridion_verbatim(tmp_path, plugin_root):
    pkg = _make_package_dir(tmp_path)
    _run_assemble(plugin_root, pkg)
    assert (pkg / "plugins" / "wiki-enchiridion.ts").read_text(encoding="utf-8") == _FIXTURE_ENCHIRIDION


def test_assemble_writes_opencode_deps_template(tmp_path, plugin_root):
    pkg = _make_package_dir(tmp_path)
    _run_assemble(plugin_root, pkg)
    deps = json.loads((pkg / "templates" / "opencode-deps.json").read_text(encoding="utf-8"))
    assert deps == {"@opencode-ai/plugin": "^1.18.15"}


def test_assemble_writes_config_templates(tmp_path, plugin_root):
    pkg = _make_package_dir(tmp_path)
    _run_assemble(plugin_root, pkg)
    marker = json.loads((pkg / "templates" / "config.json").read_text(encoding="utf-8"))
    assert marker == {"plugin_root": "<filled at deploy time>"}
    models = json.loads((pkg / "templates" / "model-config.json").read_text(encoding="utf-8"))
    assert models == DEFAULT_MODELS


def test_assemble_generated_agents_match_shipped_model_config(tmp_path, plugin_root):
    # The generator is invoked with the package's own model-config template,
    # so the shipped agents and the shipped mapping can't disagree.
    pkg = _make_package_dir(tmp_path)
    calls = []

    def recording_generate(argv):
        calls.append(argv)

    assemble_opencode_package.assemble(plugin_root, pkg, run_generate=recording_generate)
    assert len(calls) == 1
    argv = calls[0]
    model_cfg = argv[argv.index("--model-config") + 1]
    assert model_cfg == str(pkg / "templates" / "model-config.json")
    assert argv[argv.index("--output") + 1] == str(pkg)


# ---------------------------------------------------------------------------
# version coupling — package.json follows plugin.json (the single source)
# ---------------------------------------------------------------------------


def test_package_version_matches_plugin_version(tmp_path, plugin_root):
    pkg = _make_package_dir(tmp_path)
    _run_assemble(plugin_root, pkg)
    pkg_version = json.loads((pkg / "package.json").read_text(encoding="utf-8"))["version"]
    plugin_version = json.loads(
        (plugin_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"),
    )["version"]
    assert pkg_version == plugin_version == "0.9.5"


def test_package_version_follows_a_new_plugin_version(tmp_path, plugin_root):
    plugin_json = plugin_root / ".claude-plugin" / "plugin.json"
    data = json.loads(plugin_json.read_text(encoding="utf-8"))
    data["version"] = "9.9.9"
    plugin_json.write_text(json.dumps(data), encoding="utf-8")

    pkg = _make_package_dir(tmp_path)
    _run_assemble(plugin_root, pkg)
    pkg_data = json.loads((pkg / "package.json").read_text(encoding="utf-8"))
    assert pkg_data["version"] == "9.9.9"
    # Every other committed field survives untouched.
    assert pkg_data["name"] == "@dhague/wiki-knowledge"


def test_assemble_preserves_committed_package_json_fields(tmp_path, plugin_root):
    pkg = _make_package_dir(tmp_path)
    _run_assemble(plugin_root, pkg)
    data = json.loads((pkg / "package.json").read_text(encoding="utf-8"))
    assert data["bin"] == {"wiki-knowledge": "bin/deploy.js"}
    assert "files" in data and "bin" in data["files"]
    assert data["engines"]["node"]


# ---------------------------------------------------------------------------
# idempotence — running twice yields the same tree
# ---------------------------------------------------------------------------


def test_assemble_is_idempotent(tmp_path, plugin_root):
    pkg = _make_package_dir(tmp_path)
    _run_assemble(plugin_root, pkg)
    first = {
        p.relative_to(pkg).as_posix(): p.read_bytes()
        for p in pkg.rglob("*")
        if p.is_file()
    }
    _run_assemble(plugin_root, pkg)
    second = {
        p.relative_to(pkg).as_posix(): p.read_bytes()
        for p in pkg.rglob("*")
        if p.is_file()
    }
    assert second == first


def test_assemble_rerun_overwrites_stale_generated_content(tmp_path, plugin_root):
    # A file left over from a previous assembly (e.g. a skill that has since
    # been renamed) must be cleared by the re-run, not silently kept.
    pkg = _make_package_dir(tmp_path)
    _run_assemble(plugin_root, pkg)
    stale = pkg / "skills" / "stale-skill" / "SKILL.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale\n", encoding="utf-8")

    _run_assemble(plugin_root, pkg)
    assert "skills/stale-skill/SKILL.md" not in _relpaths(pkg)


# ---------------------------------------------------------------------------
# failure propagation — a missing source or failing generator aborts the run
# ---------------------------------------------------------------------------


def test_assemble_missing_skill_raises(tmp_path, plugin_root):
    shutil.rmtree(plugin_root / "skills" / "wiki-init")
    with pytest.raises(assemble_opencode_package.AssemblyError, match="wiki-init"):
        _run_assemble(plugin_root, _make_package_dir(tmp_path))


def test_assemble_missing_runtime_raises(tmp_path, plugin_root):
    (plugin_root / "scripts" / "cli.cjs").unlink()
    with pytest.raises(assemble_opencode_package.AssemblyError, match="cli.cjs"):
        _run_assemble(plugin_root, _make_package_dir(tmp_path))


def test_assemble_missing_tracker_raises(tmp_path, plugin_root):
    (plugin_root / "wiring" / "opencode" / "plugins" / "session-tracker.ts").unlink()
    with pytest.raises(assemble_opencode_package.AssemblyError, match="session-tracker"):
        _run_assemble(plugin_root, _make_package_dir(tmp_path))


def test_assemble_missing_wiki_enchiridion_raises(tmp_path, plugin_root):
    (plugin_root / "wiring" / "opencode" / "plugins" / "wiki-enchiridion.ts").unlink()
    with pytest.raises(assemble_opencode_package.AssemblyError, match="wiki-enchiridion"):
        _run_assemble(plugin_root, _make_package_dir(tmp_path))


def test_assemble_missing_opencode_wiring_package_json_raises(tmp_path, plugin_root):
    (plugin_root / "wiring" / "opencode" / "package.json").unlink()
    with pytest.raises(assemble_opencode_package.AssemblyError, match="wiring/opencode/package.json"):
        _run_assemble(plugin_root, _make_package_dir(tmp_path))


def test_assemble_generator_failure_propagates(tmp_path, plugin_root):
    (plugin_root / "scripts" / "generate-opencode.py").write_text(
        "import sys\nsys.exit(3)\n", encoding="utf-8",
    )
    with pytest.raises(assemble_opencode_package.AssemblyError, match="generate-opencode"):
        _run_assemble(plugin_root, _make_package_dir(tmp_path))


def test_assemble_missing_plugin_json_raises(tmp_path, plugin_root):
    (plugin_root / ".claude-plugin" / "plugin.json").unlink()
    with pytest.raises(assemble_opencode_package.AssemblyError, match="plugin.json"):
        _run_assemble(plugin_root, _make_package_dir(tmp_path))


# ---------------------------------------------------------------------------
# CLI — exit codes and the bare-run defaults
# ---------------------------------------------------------------------------


def test_main_assembles_and_prints_written_paths(tmp_path, plugin_root, capsys):
    pkg = _make_package_dir(tmp_path)
    rc = assemble_opencode_package.main(
        ["--plugin-root", str(plugin_root), "--output", str(pkg)],
    )
    assert rc == 0
    printed = capsys.readouterr().out.splitlines()
    assert any(p.endswith("package.json") for p in printed)
    assert any(p.endswith("plugins/session-tracker.ts") for p in printed)
    assert any(p.endswith("wiki-knowledge/cli.cjs") for p in printed)


def test_main_error_exits_nonzero(tmp_path, plugin_root, capsys):
    (plugin_root / "scripts" / "cli.cjs").unlink()
    rc = assemble_opencode_package.main(
        ["--plugin-root", str(plugin_root), "--output", str(_make_package_dir(tmp_path))],
    )
    assert rc != 0
    assert "error:" in capsys.readouterr().err