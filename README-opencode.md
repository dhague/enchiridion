# Installing wiki-knowledge on OpenCode

The wiki-knowledge plugin is authored for Claude Code, but its agents, skills,
and slash commands are also usable on [OpenCode](https://opencode.ai). Claude
Code discovers the plugin through a marketplace; OpenCode has no equivalent
mechanism, so installing it there is an explicit, per-vault step that
generates OpenCode's own agent/command files from the canonical Claude Code
sources.

This is a port of a subset of the plugin surface — see [Limitations](#limitations)
below for what isn't wired up yet.

## Prerequisites

- OpenCode installed (`curl -fsSL https://opencode.ai/install | bash`)
- Python 3.12+, with `ruamel.yaml` installed (`pip install ruamel.yaml`) —
  the only dependency the generator/installer scripts need
- A clone of this repo, so you have a `wiki-plugin/` directory to pass as
  `--plugin-root`

## What gets installed

Running the installer writes:

- `.opencode/agents/wiki-ingest.md`, `.opencode/agents/wiki-researcher.md` —
  translated from the canonical Claude Code agent files (`wiki-plugin/agents/`).
  `model:` is mapped through your model config, and CC's `tools:` list becomes
  an OpenCode `permission` block.
- `.opencode/commands/*.md` — one thin slash command per skill
  (`wiki-conventions`, `wiki-ingest`, `wiki-init`, `wiki-retrieval`,
  `wiki-watch`, `save-conversation`). The two that map to a subagent
  (`wiki-ingest`, `wiki-retrieval`) delegate to it directly; the rest tell the
  session to load and follow the corresponding `SKILL.md`.
- `.opencode/plugins/session-tracker.ts` — copied from
  `wiki-plugin/wiring/opencode/plugins/`, OpenCode's equivalent of the
  Claude Code session-tracking hook (used by `save-conversation`).
- `.opencode/wiki-knowledge/config.json` — a marker file carrying
  `plugin_root`, the OpenCode replacement for Claude Code's
  `${CLAUDE_PLUGIN_ROOT}` substitution.
- `.opencode/wiki-knowledge/model-config.json` — the model mapping used for
  this install, so it doubles as the documented place to edit and re-apply
  overrides later.
- `opencode.json` — merges `skills.paths` to point at the plugin's own
  `skills/` directory (shared in place, not copied). Merges into whatever
  `opencode.json` already exists rather than clobbering it; if the existing
  file isn't valid JSON (e.g. it's JSONC with comments), the install fails
  rather than merging unsafely.

The skill bodies themselves (`wiki-plugin/skills/*/SKILL.md`) are never
copied — OpenCode reads them in place via `skills.paths`.

## Install

There are two deployment modes, matching [ADR-0004](docs/adr/0004-deployment-modes-and-vault-root-resolution.md):

### Dedicated (vault is its own project)

Installs into the vault's own `.opencode/` and merges the vault's
`opencode.json`. Run from anywhere, pointing `--plugin-root` at your clone of
this repo:

```bash
cd /path/to/your-vault
python /path/to/enchiridion/wiki-plugin/scripts/install-opencode.py \
  --plugin-root /path/to/enchiridion/wiki-plugin
```

### Query-from-anywhere (global install, `$WIKI_ROOT` points at the vault)

Installs into `~/.config/opencode/` instead, so the agents/commands/skills
are available from any directory, and the vault is selected via `$WIKI_ROOT`
at query time:

```bash
python /path/to/enchiridion/wiki-plugin/scripts/install-opencode.py \
  --plugin-root /path/to/enchiridion/wiki-plugin \
  --global

export WIKI_ROOT=/path/to/your-vault   # set per-shell/session, not exported globally
```

### Model config

The installer needs a `provider/model-id` for each canonical model name the
CC agents use (currently `sonnet` and `haiku`). By default it prompts for
each interactively, showing the documented default:

```
OpenCode model id for 'sonnet' (default 'anthropic/claude-sonnet-4-5'):
OpenCode model id for 'haiku' (default 'anthropic/claude-haiku-4-5'):
```

Press enter to accept a default, or type your own `provider/model-id`. To
skip the prompt (e.g. in a script), pass `--model-config` with a JSON file:

```bash
python /path/to/enchiridion/wiki-plugin/scripts/install-opencode.py \
  --plugin-root /path/to/enchiridion/wiki-plugin \
  --model-config models.json
```

```json
{
  "sonnet": "anthropic/claude-sonnet-4-5",
  "haiku": "anthropic/claude-haiku-4-5"
}
```

Keys omitted from the file fall back to the documented defaults. To change
the mapping after install, edit `.opencode/wiki-knowledge/model-config.json`
(written by the installer) and re-run with `--model-config` pointed at it.

## Verify the install

```bash
opencode agent list       # should list wiki-ingest and wiki-researcher as subagents
opencode debug skill      # should list all six wiki-* / save-conversation skills
```

Then from inside the vault (dedicated mode) or with `$WIKI_ROOT` set (global
mode), the same slash commands work as in Claude Code: `/wiki-init`,
`/wiki-ingest`, `/wiki-retrieval`, `/wiki-watch`, `/save-conversation`,
`/wiki-conventions`.

## Regenerating after a plugin upgrade

`install-opencode.py` always regenerates `.opencode/agents/` and
`.opencode/commands/` from whatever `wiki-plugin/` you point `--plugin-root`
at, and is idempotent — safe to re-run after pulling a newer version of this
repo:

```bash
python /path/to/enchiridion/wiki-plugin/scripts/install-opencode.py \
  --plugin-root /path/to/enchiridion/wiki-plugin \
  --model-config .opencode/wiki-knowledge/model-config.json   # reuse your existing mapping
```

(Pass `--global` too if you installed that way.)

## Limitations

- The OpenCode wiring is generated, not hand-authored: only the agent
  frontmatter and the six slash commands are translated. Skill bodies are
  shared verbatim via `skills.paths`, so any Claude-Code-specific tool
  references inside a skill still assume CC's tool names.
- OpenCode has no `${CLAUDE_PLUGIN_ROOT}` equivalent; scripts locate the
  plugin root via `.opencode/wiki-knowledge/config.json` instead.
- This wiring is validated in CI (`.github/workflows/opencode-wiring.yml`)
  against a pinned OpenCode version and a mocked model backend — see that
  workflow for the exact commands it runs if something doesn't match your
  local behavior.
- Broader OpenCode parity (statusline, further host-neutral polish) is still
  tracked on the wayfinder map, [#1](https://github.com/dhague/enchiridion/issues/1).
