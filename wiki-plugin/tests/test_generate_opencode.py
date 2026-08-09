"""TDD for generate-opencode.py — OpenCode agent/command generation from the
canonical Claude Code agent files (#91).

Tests the pure seams only (frontmatter split/parse, agent translation, render,
command wrappers); the one I/O seam (`generate`) is exercised against a tmp
plugin root so nothing touches the real repo.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

# The production script lives at scripts/generate-opencode.py — a hyphen, not
# an underscore, so the import system can't pick it up directly. Load it by
# file path, mirroring test_save_session_opencode.py.
_HYPHEN_PATH = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "generate-opencode.py",
)
_spec = importlib.util.spec_from_file_location(
    "generate_opencode", os.path.abspath(_HYPHEN_PATH),
)
assert _spec is not None and _spec.loader is not None  # the file above always exists
generate_opencode = importlib.util.module_from_spec(_spec)
sys.modules["generate_opencode"] = generate_opencode
_spec.loader.exec_module(generate_opencode)


# ---------------------------------------------------------------------------
# fixtures — canonical Claude Code agent text
# ---------------------------------------------------------------------------


WIKI_INGEST_CC = """---
name: wiki-ingest
description: Turns one raw document into one or more well-formed wiki pages — chunked, placed by the kind-axed folder algorithm, tagged, linked, and committed. Invoke whenever a document needs to be ingested, added, or filed into the wiki vault.
model: sonnet
tools: Read, Write, Grep, Glob, Bash
skills: [wiki-conventions, wiki-ingest]
---

<!-- Plugin subagents ignore mcpServers/hooks/permissionMode frontmatter — omitted deliberately, not missing. -->
<!-- On non-Anthropic providers, wire `model:` through `fallbackModel` / `modelOverrides` / `ANTHROPIC_DEFAULT_*_MODEL` — see https://code.claude.com/docs/en/model-config -->

You are the `wiki-ingest` agent. You are given the path to one raw document and you turn it into one or more schema-valid `wiki/` pages.
"""


WIKI_RESEARCHER_CC = """---
name: wiki-researcher
description: Answers a question from the wiki vault — query-expanded, BM25-ranked, frontmatter-first, budget-bounded, and cited with each page's age and volatility. Invoke whenever the vault should be asked something rather than read page by page.
model: haiku
tools: Read, Grep, Glob, Bash
skills: [wiki-conventions, wiki-retrieval]
---

You are the `wiki-researcher` agent. You are given a question and you answer it from the vault's pages, following the `wiki-retrieval` skill procedure preloaded into your context above.
"""


# ---------------------------------------------------------------------------
# seam 1 — split_frontmatter / parse_frontmatter: CC md -> (yaml, body)
# ---------------------------------------------------------------------------


def test_split_frontmatter_splits_yaml_block_and_body():
    yaml_text, body = generate_opencode.split_frontmatter(WIKI_INGEST_CC)
    assert "name: wiki-ingest" in yaml_text
    assert "tools: Read, Write, Grep, Glob, Bash" in yaml_text
    assert body.startswith("\n<!-- Plugin subagents")
    assert body.endswith("You are the `wiki-ingest` agent. You are given the path to one raw document and you turn it into one or more schema-valid `wiki/` pages.\n")


def test_split_frontmatter_keeps_skills_line_in_yaml():
    yaml_text, _body = generate_opencode.split_frontmatter(WIKI_RESEARCHER_CC)
    assert "skills: [wiki-conventions, wiki-retrieval]" in yaml_text


def test_split_frontmatter_missing_frontmatter_raises():
    with pytest.raises(ValueError, match="frontmatter"):
        generate_opencode.split_frontmatter("plain markdown, no frontmatter\n")


def test_parse_frontmatter_yields_cc_keys():
    yaml_text, _body = generate_opencode.split_frontmatter(WIKI_INGEST_CC)
    data = generate_opencode.parse_frontmatter(yaml_text)
    assert data["name"] == "wiki-ingest"
    assert data["model"] == "sonnet"
    assert data["tools"] == "Read, Write, Grep, Glob, Bash"
    assert data["skills"] == ["wiki-conventions", "wiki-ingest"]


# ---------------------------------------------------------------------------
# seam 2 — translate_agent: CC frontmatter -> OpenCode frontmatter
# ---------------------------------------------------------------------------


MODEL_MAP = {
    "sonnet": "anthropic/claude-sonnet-4-5",
    "haiku": "anthropic/claude-haiku-4-5",
}


def _cc_ingest_frontmatter() -> dict:
    yaml_text, _ = generate_opencode.split_frontmatter(WIKI_INGEST_CC)
    return generate_opencode.parse_frontmatter(yaml_text)


def _cc_researcher_frontmatter() -> dict:
    yaml_text, _ = generate_opencode.split_frontmatter(WIKI_RESEARCHER_CC)
    return generate_opencode.parse_frontmatter(yaml_text)


def test_translate_agent_maps_tools_to_permission_with_catch_all_deny():
    out = generate_opencode.translate_agent(_cc_ingest_frontmatter(), MODEL_MAP)
    assert out["permission"] == {
        "*": "deny",
        "read": "allow",
        "edit": "allow",
        "grep": "allow",
        "glob": "allow",
        "bash": "allow",
    }


def test_translate_agent_researcher_is_read_only():
    # wiki-researcher lists no Write tool in the canonical CC file, so the
    # translated permission must not open `edit` — CC's tool list is the
    # deny-by-default gate and OpenCode's permission block must mirror it.
    out = generate_opencode.translate_agent(_cc_researcher_frontmatter(), MODEL_MAP)
    assert out["permission"]["*"] == "deny"
    assert "edit" not in out["permission"]  # no Write tool -> no edit allow; the catch-all denies it
    assert out["permission"]["read"] == "allow"
    assert out["permission"]["bash"] == "allow"


def test_translate_agent_maps_model_and_sets_subagent_mode():
    out = generate_opencode.translate_agent(_cc_ingest_frontmatter(), MODEL_MAP)
    assert out["mode"] == "subagent"
    assert out["model"] == "anthropic/claude-sonnet-4-5"
    out = generate_opencode.translate_agent(_cc_researcher_frontmatter(), MODEL_MAP)
    assert out["model"] == "anthropic/claude-haiku-4-5"


def test_translate_agent_drops_skills_preload():
    out = generate_opencode.translate_agent(_cc_ingest_frontmatter(), MODEL_MAP)
    assert "skills" not in out


def test_translate_agent_keeps_description():
    out = generate_opencode.translate_agent(_cc_ingest_frontmatter(), MODEL_MAP)
    assert out["description"] == _cc_ingest_frontmatter()["description"]


def test_translate_agent_unknown_tool_raises():
    frontmatter = _cc_ingest_frontmatter()
    frontmatter["tools"] = "Read, Whisper"
    with pytest.raises(generate_opencode.GenerationError, match="Whisper"):
        generate_opencode.translate_agent(frontmatter, MODEL_MAP)


def test_translate_agent_unmapped_model_raises():
    with pytest.raises(generate_opencode.GenerationError, match="sonnet"):
        generate_opencode.translate_agent(_cc_ingest_frontmatter(), {})


def test_translate_agent_model_id_must_contain_slash():
    with pytest.raises(generate_opencode.GenerationError, match="provider/model"):
        generate_opencode.translate_agent(
            _cc_ingest_frontmatter(), {"sonnet": "gpt-5"},
        )


# ---------------------------------------------------------------------------
# seam 3 — render_agent: translated frontmatter + verbatim body -> md text
# ---------------------------------------------------------------------------


def _rendered_ingest_agent() -> tuple[str, dict]:
    out_fm = generate_opencode.translate_agent(_cc_ingest_frontmatter(), MODEL_MAP)
    _yaml, body = generate_opencode.split_frontmatter(WIKI_INGEST_CC)
    return generate_opencode.render_agent(out_fm, body), body


def test_render_agent_wraps_in_frontmatter_delimiters():
    text, body = _rendered_ingest_agent()
    assert text.startswith("---\n")
    assert "mode: subagent" in text
    assert "model: anthropic/claude-sonnet-4-5" in text


def test_render_agent_round_trips_frontmatter_and_keeps_body_exact():
    text, body = _rendered_ingest_agent()
    yaml_text, roundtrip_body = generate_opencode.split_frontmatter(text)
    parsed = generate_opencode.parse_frontmatter(yaml_text)
    assert parsed["mode"] == "subagent"
    assert parsed["model"] == "anthropic/claude-sonnet-4-5"
    assert parsed["permission"]["*"] == "deny"
    assert parsed["permission"]["edit"] == "allow"
    # split -> render -> split returns the same body, byte for byte
    assert roundtrip_body == body


def test_render_agent_quotes_the_star_permission_key():
    # `*` is a YAML alias indicator, so it must be quoted to survive a
    # re-parse — an unquoted `*:` is not valid YAML.
    text, _body = _rendered_ingest_agent()
    assert "'*': deny" in text or '"*": deny' in text


# ---------------------------------------------------------------------------
# seam 4 — command wrappers: one thin .opencode/commands/ md per skill
# ---------------------------------------------------------------------------


def test_translate_command_agent_backed_for_ingest():
    out = generate_opencode.translate_command(
        "wiki-ingest", "Turn a raw document into wiki pages.",
    )
    assert out == {"description": "Turn a raw document into wiki pages.", "agent": "wiki-ingest"}


def test_translate_command_researcher_agent_is_wiki_researcher():
    out = generate_opencode.translate_command("wiki-retrieval", "Answer from the vault.")
    assert out["agent"] == "wiki-researcher"


def test_translate_command_skill_loading_commands_have_no_agent():
    for skill in ("wiki-watch", "wiki-init", "save-conversation", "wiki-conventions"):
        out = generate_opencode.translate_command(skill, "desc")
        assert "agent" not in out
        assert out["description"] == "desc"


def test_translate_command_unknown_skill_raises():
    with pytest.raises(generate_opencode.GenerationError, match="nope"):
        generate_opencode.translate_command("nope", "desc")


def test_render_command_round_trips_frontmatter_and_template():
    for skill in generate_opencode.SKILLS:
        description = f"description for {skill}"
        fm = generate_opencode.translate_command(skill, description)
        template = generate_opencode.COMMAND_TEMPLATE[skill]
        text = generate_opencode.render_command(fm, template)
        yaml_text, body = generate_opencode.split_frontmatter(text)
        parsed = generate_opencode.parse_frontmatter(yaml_text)
        assert parsed["description"] == description
        if skill in generate_opencode.COMMAND_AGENT:
            assert parsed["agent"] == generate_opencode.COMMAND_AGENT[skill]
        else:
            assert "agent" not in parsed
        assert body == "\n" + template + "\n"


def test_render_command_covers_all_six_skills_with_templates():
    assert set(generate_opencode.SKILLS) == set(generate_opencode.COMMAND_TEMPLATE)
    assert set(generate_opencode.SKILLS) == {
        "wiki-conventions", "wiki-ingest", "wiki-init",
        "wiki-retrieval", "wiki-watch", "save-conversation",
    }


# ---------------------------------------------------------------------------
# load_model_map — the --model-config JSON file overlaid on the defaults
# ---------------------------------------------------------------------------


def test_load_model_map_absent_file_yields_defaults(tmp_path):
    assert generate_opencode.load_model_map(None) == generate_opencode.DEFAULT_MODELS


def test_load_model_map_file_overrides_defaults(tmp_path):
    cfg = tmp_path / "models.json"
    cfg.write_text(
        json.dumps({"sonnet": "openai/gpt-5", "haiku": "openai/gpt-5-mini"}),
        encoding="utf-8",
    )
    mapping = generate_opencode.load_model_map(cfg)
    assert mapping["sonnet"] == "openai/gpt-5"
    assert mapping["haiku"] == "openai/gpt-5-mini"


def test_load_model_map_partial_file_keeps_other_defaults(tmp_path):
    cfg = tmp_path / "models.json"
    cfg.write_text(json.dumps({"sonnet": "openai/gpt-5"}), encoding="utf-8")
    mapping = generate_opencode.load_model_map(cfg)
    assert mapping["sonnet"] == "openai/gpt-5"
    assert mapping["haiku"] == generate_opencode.DEFAULT_MODELS["haiku"]


def test_load_model_map_non_object_json_raises(tmp_path):
    cfg = tmp_path / "models.json"
    cfg.write_text(json.dumps(["sonnet"]), encoding="utf-8")
    with pytest.raises(generate_opencode.GenerationError, match="JSON object"):
        generate_opencode.load_model_map(cfg)


def test_load_model_map_invalid_json_raises(tmp_path):
    cfg = tmp_path / "models.json"
    cfg.write_text("not json", encoding="utf-8")
    with pytest.raises(generate_opencode.GenerationError, match="models.json"):
        generate_opencode.load_model_map(cfg)


# ---------------------------------------------------------------------------
# list_models — the canonical model names used by the CC agents (for install)
# ---------------------------------------------------------------------------


def test_list_models_scans_canonical_agents(tmp_path):
    root = _make_plugin_root(tmp_path)
    assert generate_opencode.list_models(root) == ["sonnet", "haiku"]


# ---------------------------------------------------------------------------
# generate — the one I/O seam: canonical plugin -> .opencode/ agents+commands
# ---------------------------------------------------------------------------


def _skill_md(name: str, description: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\nBody of {name}.\n"


def _make_plugin_root(tmp_path) -> Path:
    """A plugin-shaped fixture: agents/ + the six skills/, minimal content."""
    root = tmp_path / "plugin"
    agents = root / "agents"
    agents.mkdir(parents=True)
    (agents / "wiki-ingest.md").write_text(WIKI_INGEST_CC, encoding="utf-8")
    (agents / "wiki-researcher.md").write_text(WIKI_RESEARCHER_CC, encoding="utf-8")
    for skill in generate_opencode.SKILLS:
        skill_dir = root / "skills" / skill
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            _skill_md(skill, f"The {skill} capability."), encoding="utf-8",
        )
    return root


def _agent_text(output_root, name):
    return (output_root / "agents" / f"{name}.md").read_text(encoding="utf-8")


def test_generate_writes_two_agents_and_six_commands(tmp_path):
    root = _make_plugin_root(tmp_path)
    output = tmp_path / ".opencode"
    written = generate_opencode.generate(root, generate_opencode.DEFAULT_MODELS, output)
    assert {p.relative_to(output).as_posix() for p in written} == {
        *(f"agents/{a}" for a in ("wiki-ingest.md", "wiki-researcher.md")),
        *(f"commands/{s}.md" for s in generate_opencode.SKILLS),
    }


def test_generate_agent_uses_mapped_model_and_translated_permission(tmp_path):
    root = _make_plugin_root(tmp_path)
    output = tmp_path / ".opencode"
    models = {"sonnet": "openai/gpt-5", "haiku": "openai/gpt-5-mini"}
    generate_opencode.generate(root, models, output)
    text = _agent_text(output, "wiki-ingest")
    assert "model: openai/gpt-5" in text
    assert "permission:" in text
    assert "'*': deny" in text or '"*": deny' in text
    researcher = _agent_text(output, "wiki-researcher")
    assert "model: openai/gpt-5-mini" in researcher
    assert "edit: allow" not in researcher


def test_generate_keeps_agent_body_verbatim(tmp_path):
    root = _make_plugin_root(tmp_path)
    output = tmp_path / ".opencode"
    generate_opencode.generate(root, generate_opencode.DEFAULT_MODELS, output)
    _yaml, body = generate_opencode.split_frontmatter(WIKI_INGEST_CC)
    assert _agent_text(output, "wiki-ingest").endswith(body)


def test_generate_command_carries_skill_description(tmp_path):
    root = _make_plugin_root(tmp_path)
    output = tmp_path / ".opencode"
    generate_opencode.generate(root, generate_opencode.DEFAULT_MODELS, output)
    cmd = (output / "commands" / "wiki-watch.md").read_text(encoding="utf-8")
    assert "description: The wiki-watch capability." in cmd


def test_generate_is_idempotent(tmp_path):
    root = _make_plugin_root(tmp_path)
    output = tmp_path / ".opencode"
    generate_opencode.generate(root, generate_opencode.DEFAULT_MODELS, output)
    first = sorted(p.read_bytes() for p in output.rglob("*.md"))
    generate_opencode.generate(root, generate_opencode.DEFAULT_MODELS, output)
    second = sorted(p.read_bytes() for p in output.rglob("*.md"))
    assert first == second


def test_generate_missing_canonical_agent_raises(tmp_path):
    root = _make_plugin_root(tmp_path)
    (root / "agents" / "wiki-ingest.md").unlink()
    with pytest.raises(generate_opencode.GenerationError, match="wiki-ingest.md"):
        generate_opencode.generate(root, generate_opencode.DEFAULT_MODELS, tmp_path / "out")


def test_generate_missing_skill_raises(tmp_path):
    root = _make_plugin_root(tmp_path)
    skill_md = root / "skills" / "wiki-init" / "SKILL.md"
    skill_md.unlink()
    with pytest.raises(generate_opencode.GenerationError, match="wiki-init"):
        generate_opencode.generate(root, generate_opencode.DEFAULT_MODELS, tmp_path / "out")


# ---------------------------------------------------------------------------
# main — the CLI: writes paths, --list-models / --default-models, error exit
# ---------------------------------------------------------------------------


def test_main_prints_written_paths(tmp_path, capsys):
    root = _make_plugin_root(tmp_path)
    rc = generate_opencode.main(
        ["--plugin-root", str(root), "--output", str(tmp_path / ".opencode")],
    )
    assert rc == 0
    printed = capsys.readouterr().out.splitlines()
    assert any(p.endswith("agents/wiki-ingest.md") for p in printed)
    assert any(p.endswith("commands/save-conversation.md") for p in printed)


def test_main_model_config_flag_is_honoured(tmp_path, capsys):
    root = _make_plugin_root(tmp_path)
    cfg = tmp_path / "models.json"
    cfg.write_text(json.dumps({"sonnet": "openai/gpt-5"}), encoding="utf-8")
    generate_opencode.main(
        [
            "--plugin-root", str(root),
            "--model-config", str(cfg),
            "--output", str(tmp_path / ".opencode"),
        ],
    )
    text = _agent_text(tmp_path / ".opencode", "wiki-ingest")
    assert "model: openai/gpt-5" in text


def test_main_list_models_prints_models(tmp_path, capsys):
    root = _make_plugin_root(tmp_path)
    rc = generate_opencode.main(["--plugin-root", str(root), "--list-models"])
    assert rc == 0
    assert capsys.readouterr().out.splitlines() == ["sonnet", "haiku"]


def test_main_default_models_prints_defaults_json(tmp_path, capsys):
    root = _make_plugin_root(tmp_path)
    rc = generate_opencode.main(["--plugin-root", str(root), "--default-models"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == generate_opencode.DEFAULT_MODELS


def test_main_generation_error_exits_nonzero(tmp_path, capsys):
    root = _make_plugin_root(tmp_path)
    (root / "agents" / "wiki-ingest.md").unlink()
    rc = generate_opencode.main(["--plugin-root", str(root), "--output", str(tmp_path / "o")])
    assert rc != 0
    assert "error:" in capsys.readouterr().err
