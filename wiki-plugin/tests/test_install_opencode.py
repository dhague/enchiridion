"""TDD for install-opencode.py — the per-vault OpenCode install (#91).

Covers target resolution (vault vs --global), model-config resolution
(interactive prompt vs override file), and the install orchestration (marker
file, session-tracker + wiki-enchiridion plugin copies, the config-dir
package.json declaring ``@opencode-ai/plugin``, generate invocation,
opencode.json merge). The real subprocess path is exercised in the CLI tests
against a stub generate-opencode.py in a fixture plugin root.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

# Load scripts/install-opencode.py by file path (hyphen, not underscore).
_HYPHEN_PATH = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "install-opencode.py",
)
_spec = importlib.util.spec_from_file_location(
    "install_opencode", os.path.abspath(_HYPHEN_PATH),
)
assert _spec is not None and _spec.loader is not None
install_opencode = importlib.util.module_from_spec(_spec)
sys.modules["install_opencode"] = install_opencode
_spec.loader.exec_module(install_opencode)


# The fixture's generate-opencode.py: a no-op except for --default-models (and
# --list-models), so the CLI tests exercise the real subprocess path hermetically.
_FIXTURE_GENERATE = """import json, sys
if "--default-models" in sys.argv:
    print(json.dumps({"sonnet": "anthropic/claude-sonnet-4-5"}))
elif "--list-models" in sys.argv:
    print("sonnet")
"""

_FIXTURE_TRACKER = "export const SessionTracker: Plugin = () => ({})\n"

_FIXTURE_PLUGIN = "export const WikiEnchiridion: Plugin = () => ({})\n"

# The wiring package.json — the single source of truth for the
# @opencode-ai/plugin version the install writes into the config dir.
_FIXTURE_WIRING_PKG = json.dumps(
    {
        "name": "wiki-knowledge-opencode-wiring",
        "private": True,
        "version": "0.0.0",
        "type": "module",
        "devDependencies": {
            "@opencode-ai/plugin": "^1.18.15",
            "typescript": "^5.6.0",
        },
    }
)


@pytest.fixture
def plugin_root(tmp_path):
    root = tmp_path / "plugin"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "generate-opencode.py").write_text(_FIXTURE_GENERATE, encoding="utf-8")
    wiring = root / "wiring" / "opencode" / "plugins"
    wiring.mkdir(parents=True)
    (wiring / "session-tracker.ts").write_text(_FIXTURE_TRACKER, encoding="utf-8")
    (wiring / "wiki-enchiridion.ts").write_text(_FIXTURE_PLUGIN, encoding="utf-8")
    (root / "wiring" / "opencode" / "package.json").write_text(
        _FIXTURE_WIRING_PKG, encoding="utf-8",
    )
    return root


MODELS = {"sonnet": "anthropic/claude-sonnet-4-5"}

DEFAULTS = {
    "sonnet": "anthropic/claude-sonnet-4-5",
    "haiku": "anthropic/claude-haiku-4-5",
}

_HOME = Path("/nonexistent/home")


def _noop_generate(argv):
    pass


# ---------------------------------------------------------------------------
# seam 6 — install_targets: vault-local vs --global
# ---------------------------------------------------------------------------


def test_install_targets_local(tmp_path):
    dot, cfg = install_opencode.install_targets(
        global_mode=False, cwd=tmp_path, home=_HOME,
    )
    assert dot == tmp_path / ".opencode"
    assert cfg == tmp_path / "opencode.json"


def test_install_targets_global(tmp_path):
    vault = tmp_path / "some" / "vault"
    dot, cfg = install_opencode.install_targets(
        global_mode=True, cwd=vault, home=tmp_path / "home",
    )
    assert dot == tmp_path / "home" / ".config" / "opencode"
    assert cfg == tmp_path / "home" / ".config" / "opencode" / "opencode.json"


# ---------------------------------------------------------------------------
# seam 7 — model config resolution
# ---------------------------------------------------------------------------


def _fake_prompt(answers):
    def prompt(_message):
        return answers.pop(0)
    return prompt


def test_prompt_model_config_asks_each_model_and_returns_mapping():
    asked = []

    def prompt(message):
        asked.append(message)
        return "openai/gpt-5" if "sonnet" in message else "openai/gpt-5-mini"

    out = install_opencode.prompt_model_config(
        ["sonnet", "haiku"], DEFAULTS, prompt=prompt,
    )
    assert out == {"sonnet": "openai/gpt-5", "haiku": "openai/gpt-5-mini"}
    assert len(asked) == 2
    assert "sonnet" in asked[0]


def test_prompt_model_config_blank_input_falls_back_to_default():
    out = install_opencode.prompt_model_config(
        ["sonnet"], DEFAULTS, prompt=_fake_prompt([""]),
    )
    assert out == {"sonnet": "anthropic/claude-sonnet-4-5"}


def test_model_config_from_file_returns_partial_object(tmp_path):
    cfg = tmp_path / "models.json"
    cfg.write_text(json.dumps({"sonnet": "openai/gpt-5"}), encoding="utf-8")
    assert install_opencode.model_config_from_file(cfg) == {"sonnet": "openai/gpt-5"}


def test_model_config_from_file_invalid_json_raises(tmp_path):
    cfg = tmp_path / "models.json"
    cfg.write_text("nope", encoding="utf-8")
    with pytest.raises(install_opencode.InstallError, match="models.json"):
        install_opencode.model_config_from_file(cfg)


def test_model_config_from_file_non_object_raises(tmp_path):
    cfg = tmp_path / "models.json"
    cfg.write_text(json.dumps(["sonnet"]), encoding="utf-8")
    with pytest.raises(install_opencode.InstallError, match="JSON object"):
        install_opencode.model_config_from_file(cfg)


def test_resolve_models_prefers_config_file_over_prompt(tmp_path):
    cfg = tmp_path / "models.json"
    cfg.write_text(json.dumps({"sonnet": "openai/gpt-5"}), encoding="utf-8")
    out = install_opencode.resolve_models(
        DEFAULTS, cfg, prompt=_fake_prompt(["should-not-run"]),
    )
    assert out == {"sonnet": "openai/gpt-5"}


def test_resolve_models_prompts_when_no_config_file():
    defaults = {"sonnet": "anthropic/claude-sonnet-4-5"}
    out = install_opencode.resolve_models(
        defaults, None, prompt=_fake_prompt(["openai/gpt-5"]),
    )
    assert out == {"sonnet": "openai/gpt-5"}


# ---------------------------------------------------------------------------
# seam 8 — install orchestration
# ---------------------------------------------------------------------------


def _run_install(tmp_path, plugin_root, **kwargs):
    return install_opencode.install(
        plugin_root, MODELS,
        global_mode=kwargs.get("global_mode", False),
        cwd=kwargs.get("cwd", tmp_path),
        home=kwargs.get("home", _HOME),
        run_generate=kwargs.get("run_generate", _noop_generate),
    )


def test_install_writes_marker_with_plugin_root(tmp_path, plugin_root):
    dot = _run_install(tmp_path, plugin_root)
    marker = json.loads((dot / "wiki-knowledge" / "config.json").read_text(encoding="utf-8"))
    assert marker == {"plugin_root": str(plugin_root.resolve())}


def test_install_copies_session_tracker_into_plugins(tmp_path, plugin_root):
    dot = _run_install(tmp_path, plugin_root)
    assert (dot / "plugins" / "session-tracker.ts").read_text(encoding="utf-8") == _FIXTURE_TRACKER


def test_install_copies_wiki_enchiridion_into_plugins(tmp_path, plugin_root):
    dot = _run_install(tmp_path, plugin_root)
    assert (dot / "plugins" / "wiki-enchiridion.ts").read_text(encoding="utf-8") == _FIXTURE_PLUGIN


def test_install_writes_package_json_with_plugin_dependency(tmp_path, plugin_root):
    dot = _run_install(tmp_path, plugin_root)
    pkg = json.loads((dot / "package.json").read_text(encoding="utf-8"))
    expected = json.loads(_FIXTURE_WIRING_PKG)["devDependencies"]["@opencode-ai/plugin"]
    assert pkg["dependencies"]["@opencode-ai/plugin"] == expected


def test_install_merges_existing_package_json_without_clobbering(tmp_path, plugin_root):
    (tmp_path / ".opencode").mkdir(exist_ok=True)
    (tmp_path / ".opencode" / "package.json").write_text(
        json.dumps({"name": "vault", "dependencies": {"other-pkg": "^1.0.0"}}),
        encoding="utf-8",
    )
    dot = _run_install(tmp_path, plugin_root)
    pkg = json.loads((dot / "package.json").read_text(encoding="utf-8"))
    assert pkg["name"] == "vault"
    assert pkg["dependencies"]["other-pkg"] == "^1.0.0"
    assert pkg["dependencies"]["@opencode-ai/plugin"] == json.loads(_FIXTURE_WIRING_PKG)["devDependencies"]["@opencode-ai/plugin"]


def test_install_invalid_existing_package_json_raises(tmp_path, plugin_root):
    (tmp_path / ".opencode").mkdir(exist_ok=True)
    (tmp_path / ".opencode" / "package.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(install_opencode.InstallError, match="package.json"):
        _run_install(tmp_path, plugin_root)


def test_install_persists_model_config_for_override_path(tmp_path, plugin_root):
    dot = _run_install(tmp_path, plugin_root)
    saved = json.loads(
        (dot / "wiki-knowledge" / "model-config.json").read_text(encoding="utf-8"),
    )
    assert saved == MODELS


def test_install_runs_generate_with_output_and_model_config(tmp_path, plugin_root):
    calls = []
    _run_install(tmp_path, plugin_root, run_generate=calls.append)
    assert len(calls) == 1
    argv = calls[0]
    assert str(plugin_root / "scripts" / "generate-opencode.py") in argv
    assert argv[argv.index("--output") + 1] == str(tmp_path / ".opencode")
    model_cfg = argv[argv.index("--model-config") + 1]
    assert json.loads(Path(model_cfg).read_text(encoding="utf-8")) == MODELS


def test_install_writes_opencode_json_with_skills_paths(tmp_path, plugin_root):
    _run_install(tmp_path, plugin_root)
    config = json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8"))
    assert str(plugin_root / "skills") in config["skills"]["paths"]
    assert config["$schema"] == "https://opencode.ai/config.json"


def test_install_merges_existing_opencode_json_without_clobbering(tmp_path, plugin_root):
    (tmp_path / "opencode.json").write_text(
        json.dumps({"model": "anthropic/claude-sonnet-4-5", "skills": {"paths": ["/other/skills"]}}),
        encoding="utf-8",
    )
    _run_install(tmp_path, plugin_root)
    config = json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8"))
    assert config["model"] == "anthropic/claude-sonnet-4-5"
    assert set(config["skills"]["paths"]) == {"/other/skills", str(plugin_root / "skills")}


def test_install_rerun_does_not_duplicate_skills_path(tmp_path, plugin_root):
    _run_install(tmp_path, plugin_root)
    _run_install(tmp_path, plugin_root)
    config = json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8"))
    assert config["skills"]["paths"].count(str(plugin_root / "skills")) == 1


def test_install_global_writes_into_home_config(tmp_path, plugin_root):
    home = tmp_path / "home"
    _run_install(tmp_path, plugin_root, global_mode=True, home=home)
    dot = home / ".config" / "opencode"
    assert (dot / "wiki-knowledge" / "config.json").is_file()
    assert (dot / "plugins" / "session-tracker.ts").is_file()
    config = json.loads((home / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8"))
    assert str(plugin_root / "skills") in config["skills"]["paths"]


def test_install_missing_session_tracker_source_raises(tmp_path, plugin_root):
    (plugin_root / "wiring" / "opencode" / "plugins" / "session-tracker.ts").unlink()
    with pytest.raises(install_opencode.InstallError, match="session-tracker"):
        _run_install(tmp_path, plugin_root)


def test_install_missing_wiki_enchiridion_source_raises(tmp_path, plugin_root):
    (plugin_root / "wiring" / "opencode" / "plugins" / "wiki-enchiridion.ts").unlink()
    with pytest.raises(install_opencode.InstallError, match="wiki-enchiridion"):
        _run_install(tmp_path, plugin_root)


def test_install_missing_generate_script_raises(tmp_path, plugin_root):
    (plugin_root / "scripts" / "generate-opencode.py").unlink()
    with pytest.raises(install_opencode.InstallError, match="generate-opencode"):
        _run_install(tmp_path, plugin_root)


def test_install_invalid_existing_opencode_json_raises(tmp_path, plugin_root):
    (tmp_path / "opencode.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(install_opencode.InstallError, match="opencode.json"):
        _run_install(tmp_path, plugin_root)


# ---------------------------------------------------------------------------
# seam 9 — CLI
# ---------------------------------------------------------------------------


def test_main_requires_plugin_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        install_opencode.main([])
    assert exc.value.code != 0


def test_main_bad_default_models_output_raises(tmp_path, monkeypatch, capsys):
    # A generate-opencode.py that prints garbage for --default-models must
    # surface as a clean InstallError, not an uncaught JSONDecodeError.
    plugin = tmp_path / "bad-plugin"
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "generate-opencode.py").write_text(
        "print('not json')\n", encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    rc = install_opencode.main(["--plugin-root", str(plugin)])
    assert rc != 0
    assert "invalid JSON" in capsys.readouterr().err


def test_main_rejects_unrecognized_flag(tmp_path, plugin_root, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        install_opencode.main(["--plugin-root", str(plugin_root), "--bogus"])
    assert exc.value.code != 0


def test_main_runs_end_to_end_into_vault(tmp_path, plugin_root, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", _fake_prompt(["openai/gpt-5"]))
    install_opencode.main(["--plugin-root", str(plugin_root)])
    out = capsys.readouterr().out
    assert "wiki-knowledge" in out
    marker = json.loads(
        (tmp_path / ".opencode" / "wiki-knowledge" / "config.json").read_text(encoding="utf-8"),
    )
    assert marker["plugin_root"] == str(plugin_root.resolve())
    assert (tmp_path / "opencode.json").is_file()


def test_main_global_flag_targets_home(tmp_path, plugin_root, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", _fake_prompt(["openai/gpt-5"]))
    monkeypatch.setattr(install_opencode, "home_dir", lambda: tmp_path / "home")
    install_opencode.main(["--plugin-root", str(plugin_root), "--global"])
    dot = tmp_path / "home" / ".config" / "opencode"
    assert (dot / "wiki-knowledge" / "config.json").is_file()
    assert (dot / "plugins" / "session-tracker.ts").is_file()


def test_main_model_config_file_skips_prompt(tmp_path, plugin_root, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "models.json"
    cfg.write_text(json.dumps({"sonnet": "openai/gpt-5"}), encoding="utf-8")

    def _should_not_be_called(_message):
        raise AssertionError("interactive prompt must not run with --model-config")

    monkeypatch.setattr("builtins.input", _should_not_be_called)
    install_opencode.main(
        ["--plugin-root", str(plugin_root), "--model-config", str(cfg)],
    )
    saved = json.loads(
        (tmp_path / ".opencode" / "wiki-knowledge" / "model-config.json").read_text(encoding="utf-8"),
    )
    assert saved == {"sonnet": "openai/gpt-5"}
