"""Generate OpenCode agents and commands from the canonical Claude Code
sources (#91).

The wiki plugin's agent files (`agents/wiki-ingest.md`,
`agents/wiki-researcher.md`) are authored once, for Claude Code; this script
translates them into OpenCode's markdown agent form for ``.opencode/agents/``
and emits thin ``.opencode/commands/`` wrappers for the six slash commands.
The skill bodies themselves are not generated — OpenCode shares them by
``skills.paths`` pointing at the plugin's own ``skills/`` directory, per the
host-neutral skill decision (#90).

Translation rules (per #71 Q2/Q6/Q8):

- ``name``/``description`` carry over unchanged.
- ``model`` maps through a config file keyed by the canonical names used in
  the CC frontmatter (``sonnet``, ``haiku``) to ``provider/model-id``.
- ``tools:`` becomes a ``permission`` block. OpenCode permissions default to
  ``allow``, so to mirror CC's deny-by-default subagent tool list the
  generated agent carries a catch-all ``"*": deny`` with the translated tools
  allowed after it (matching OpenCode's last-matching-rule-wins ordering).
- ``skills:`` preload is dropped — OpenCode agents load skills on demand via
  the ``skill`` tool, so the frontmatter key has no OpenCode equivalent.
- The body is carried through verbatim.

CLI::

    python generate-opencode.py [--plugin-root DIR] [--model-config PATH]
                                [--output DIR]
"""
from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML

#: Canonical Claude Code agent files, plugin-relative.
CANONICAL_AGENTS = ("wiki-ingest.md", "wiki-researcher.md")

#: The six skills, one OpenCode slash-command wrapper each.
SKILLS = (
    "wiki-conventions",
    "wiki-ingest",
    "wiki-init",
    "wiki-retrieval",
    "wiki-watch",
    "save-conversation",
)

#: Fallback provider/model-id per canonical model name, used when the
#: --model-config file omits a key. Documented, overridable defaults.
DEFAULT_MODELS = {
    "sonnet": "anthropic/claude-sonnet-4-5",
    "haiku": "anthropic/claude-haiku-4-5",
}

#: Claude Code tool name -> the OpenCode permission key that gates it.
CC_TOOL_TO_PERMISSION = {
    "Read": "read",
    "Write": "edit",
    "Edit": "edit",
    "Grep": "grep",
    "Glob": "glob",
    "Bash": "bash",
    "Task": "task",
}

#: Slash command -> subagent that should run it (commands that load a skill
#: instead have no entry).
COMMAND_AGENT = {
    "wiki-ingest": "wiki-ingest",
    "wiki-retrieval": "wiki-researcher",
}

#: Thin command templates — one short prompt per slash command, delegating to
#: the subagent (ingest/retrieval) or instructing the current session to load
#: and follow the corresponding skill (the rest).
COMMAND_TEMPLATE = {
    "wiki-conventions": (
        "Load the `wiki-conventions` skill — the shared schema, folder, link, "
        "and typed-edge contract the vault's pages are written to."
    ),
    "wiki-ingest": (
        "Ingest the raw document(s) at $ARGUMENTS into the wiki vault."
    ),
    "wiki-init": (
        "Load the `wiki-init` skill and follow its procedure to scaffold a "
        "new wiki vault at $ARGUMENTS."
    ),
    "wiki-retrieval": (
        "Answer the following question from the wiki vault: $ARGUMENTS"
    ),
    "wiki-watch": (
        "Load the `wiki-watch` skill and follow its procedure: watch the "
        "vault's raw/ folder and auto-ingest new or changed files."
    ),
    "save-conversation": (
        "Load the `save-conversation` skill and follow its procedure to "
        "capture this session into the wiki vault."
    ),
}


class GenerationError(RuntimeError):
    """Raised when a canonical source can't be translated faithfully."""


def split_frontmatter(text: str) -> tuple[str, str]:
    """Split CC markdown into ``(frontmatter_yaml, body)``.

    The frontmatter is the leading block between two ``---`` lines; the body
    is everything after (including the trailing newline of the closing
    delimiter). Raises :class:`ValueError` when there is no frontmatter block.
    """
    if not text.startswith("---\n"):
        raise ValueError("no YAML frontmatter block at the start of the file")
    closing = text.find("\n---\n", 4)
    if closing == -1:
        raise ValueError("unterminated YAML frontmatter block")
    # closing points at the `\n---\n` delimiter; skip all five chars so the
    # body starts exactly where the frontmatter block ends, making
    # split -> render -> split a faithful round trip.
    return text[4:closing], text[closing + 5 :]


def parse_frontmatter(yaml_text: str) -> dict:
    """Parse the CC frontmatter YAML into a plain dict."""
    return YAML(typ="safe").load(yaml_text)


def translate_agent(frontmatter: dict, model_map: dict) -> dict:
    """Translate one CC agent's frontmatter into OpenCode's form.

    ``description`` carries over, ``model`` is mapped to ``provider/model-id``
    via ``model_map``, ``tools:`` becomes a ``permission`` block (a catch-all
    ``"*": deny`` followed by one ``allow`` per translated tool, matching
    OpenCode's last-matching-rule-wins ordering), and ``skills:`` preload is
    dropped. Raises :class:`GenerationError` on anything the translation
    can't represent faithfully (unknown tool, unmapped or malformed model).
    """
    name = frontmatter.get("name")
    if not name:
        raise GenerationError("agent frontmatter carries no name")

    model = frontmatter.get("model")
    if not model:
        raise GenerationError(f"{name}: no model in frontmatter")
    if model not in model_map:
        raise GenerationError(f"{name}: no model mapping for {model!r}")
    model_id = model_map[model]
    if "/" not in model_id:
        raise GenerationError(
            f"{name}: mapped model {model!r} -> {model_id!r} must be "
            "provider/model-id"
        )

    permission = {"*": "deny"}
    tools = frontmatter.get("tools")
    if not tools:
        raise GenerationError(f"{name}: no tools in frontmatter")
    for tool in (t.strip() for t in str(tools).split(",") if t.strip()):
        if tool not in CC_TOOL_TO_PERMISSION:
            raise GenerationError(
                f"{name}: no OpenCode permission mapping for tool {tool!r}"
            )
        permission[CC_TOOL_TO_PERMISSION[tool]] = "allow"

    return {
        "description": frontmatter["description"],
        "mode": "subagent",
        "model": model_id,
        "permission": permission,
    }


def _render_frontmatter(frontmatter: dict) -> str:
    """The ``---``-delimited frontmatter block for a generated file, with the
    trailing delimiter newline already in place."""
    yaml = YAML()
    yaml.width = 4096  # never line-wrap long scalars (matches wikipage.py)
    yaml.indent(mapping=2, sequence=4, offset=2)
    stream = StringIO()
    yaml.dump(frontmatter, stream)
    return "---\n" + stream.getvalue() + "---\n"


def render_agent(frontmatter: dict, body: str) -> str:
    """Render the translated frontmatter + verbatim body as an OpenCode agent
    markdown file.

    The emitted frontmatter deliberately carries no ``name`` — OpenCode takes
    the agent name from the file name.
    """
    return _render_frontmatter(frontmatter) + body


def translate_command(skill_name: str, description: str) -> dict:
    """Build an OpenCode command frontmatter for one skill.

    The description is the skill's own frontmatter description (the command
    is the user-facing entry point for the same capability). Skills that run
    as a subagent carry an ``agent`` key so the command triggers that
    subagent; script-orchestrated skills load their SKILL.md instead and get
    no agent. Raises :class:`GenerationError` for an unknown skill.
    """
    if skill_name not in COMMAND_TEMPLATE:
        raise GenerationError(f"no command template for skill {skill_name!r}")
    frontmatter: dict = {"description": description}
    if skill_name in COMMAND_AGENT:
        frontmatter["agent"] = COMMAND_AGENT[skill_name]
    return frontmatter


def render_command(frontmatter: dict, template: str) -> str:
    """Render a command markdown file: frontmatter + the thin template body."""
    return _render_frontmatter(frontmatter) + "\n" + template + "\n"


def load_model_map(model_config: Path | str | None) -> dict:
    """The provider/model-id mapping: the ``--model-config`` JSON file overlaid
    on :data:`DEFAULT_MODELS` (config wins, defaults fill the rest). An absent
    file yields just the defaults.
    """
    mapping = dict(DEFAULT_MODELS)
    if model_config is None:
        return mapping
    path = Path(model_config)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GenerationError(f"model config {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise GenerationError(f"model config {path} must be a JSON object")
    mapping.update(data)
    return mapping


def _read_canonical(path: Path, what: str) -> str:
    """Read a canonical source file, raising :class:`GenerationError` with a
    clear message when it's absent — generation must never silently skip a
    source it is supposed to translate."""
    if not path.is_file():
        raise GenerationError(f"{what} not found: {path}")
    return path.read_text(encoding="utf-8")


def list_models(plugin_root: Path | str) -> list[str]:
    """The distinct canonical ``model`` values across the CC agent files, in
    file order — the names install-opencode.py must prompt a mapping for.
    """
    root = Path(plugin_root)
    seen: list[str] = []
    for filename in CANONICAL_AGENTS:
        text = _read_canonical(
            root / "agents" / filename, "canonical agent file",
        )
        yaml_text, _body = split_frontmatter(text)
        model = parse_frontmatter(yaml_text).get("model")
        if model and model not in seen:
            seen.append(model)
    return seen


def generate(
    plugin_root: Path | str,
    model_map: dict,
    output_root: Path | str,
) -> list[Path]:
    """Generate the full OpenCode surface into ``output_root``.

    Reads the canonical CC agents and the six skill SKILL.md files from
    ``plugin_root``; writes ``<output_root>/agents/{name}.md`` and
    ``<output_root>/commands/{skill}.md``. Returns the paths written. Errors
    on any missing canonical source rather than silently skipping it. Calling
    twice with the same inputs writes identical bytes (idempotent).
    """
    root = Path(plugin_root)
    out = Path(output_root)
    agents_dir = out / "agents"
    commands_dir = out / "commands"
    agents_dir.mkdir(parents=True, exist_ok=True)
    commands_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for filename in CANONICAL_AGENTS:
        path = root / "agents" / filename
        text = _read_canonical(path, "canonical agent file")
        yaml_text, body = split_frontmatter(text)
        frontmatter = parse_frontmatter(yaml_text)
        name = frontmatter.get("name")
        if not name:
            raise GenerationError(f"canonical agent {filename} carries no name")
        translated = translate_agent(frontmatter, model_map)
        target = agents_dir / f"{name}.md"
        target.write_text(render_agent(translated, body), encoding="utf-8")
        written.append(target)

    for skill in SKILLS:
        skill_path = root / "skills" / skill / "SKILL.md"
        skill_text = _read_canonical(skill_path, "skill SKILL.md")
        skill_yaml, _body = split_frontmatter(skill_text)
        description = parse_frontmatter(skill_yaml).get("description")
        if not description:
            raise GenerationError(f"skill {skill!r} carries no description")
        cmd_fm = translate_command(skill, description)
        target = commands_dir / f"{skill}.md"
        target.write_text(
            render_command(cmd_fm, COMMAND_TEMPLATE[skill]), encoding="utf-8",
        )
        written.append(target)

    return written


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate OpenCode agents and commands from the canonical "
        "Claude Code sources.",
    )
    parser.add_argument(
        "--plugin-root",
        default=str(Path(__file__).resolve().parent.parent),
        help="the plugin's install directory (defaults to this script's own "
        "plugin, so a bare run generates from the plugin source itself)",
    )
    parser.add_argument(
        "--model-config",
        help="JSON file mapping canonical model names to provider/model-id; "
        "keys omitted fall back to the documented defaults",
    )
    parser.add_argument(
        "--output", default=".opencode", help="directory to write agents/ and commands/ into"
    )
    parser.add_argument(
        "--default-models",
        action="store_true",
        help="print, as JSON, the canonical model names the CC agents use "
        "mapped to their documented defaults ('' when a model has no "
        "default) — install-opencode.py reads this to know which models to "
        "prompt for",
    )
    args = parser.parse_args(argv)

    try:
        if args.default_models:
            defaults = {
                model: DEFAULT_MODELS.get(model, "")
                for model in list_models(args.plugin_root)
            }
            print(json.dumps(defaults))
            return 0
        model_map = load_model_map(args.model_config)
        written = generate(args.plugin_root, model_map, args.output)
    except (GenerationError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
