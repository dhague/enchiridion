# Installing wiki-knowledge on OpenCode

The wiki-knowledge plugin is authored for Claude Code, but its agents, skills,
and slash commands are also usable on [OpenCode](https://opencode.ai). Claude
Code discovers the plugin through a marketplace; OpenCode has no equivalent
mechanism, so installing it is an explicit, per-vault step run via `npx`.

This is a port of a subset of the plugin surface — see [Limitations](#limitations)
below for what isn't wired up yet.

## Prerequisites

- OpenCode installed (`curl -fsSL https://opencode.ai/install | bash`)
- Node.js 22.12.0+

## What gets installed

Running the installer writes:

- `.agents/skills/wiki-conventions/`, `.agents/skills/wiki-ingest/`, etc. —
  six skill bodies copied into the vault's agent-local skills directory.
- `.opencode/agents/wiki-ingest.md`, `.opencode/agents/wiki-researcher.md` —
  agent definitions with `model:` set to your configured model IDs.
- `.opencode/commands/*.md` — one slash command per skill
  (`wiki-conventions`, `wiki-ingest`, `wiki-init`, `wiki-ask`,
  `wiki-watch`, `save-conversation`).
- `.opencode/plugins/session-tracker.ts` — OpenCode's equivalent of the
  Claude Code session-tracking hook, used by `/save-conversation`.

  > The tracker records session ids and injects `$OPENCODE_SESSION_ID` into
  > every shell OpenCode spawns; `enchiridion save-session` reads both, and
  > fetches the transcript itself via `opencode export <session id>`, so
  > `/save-conversation` needs the `opencode` CLI on `PATH`.
- `.opencode/wiki-knowledge/cli.cjs` + `.wasm` — the bundled `enchiridion`
  runtime (Node.js, no separate install).
- `.opencode/wiki-knowledge/config.json` — a marker file carrying
  `plugin_root`, the OpenCode replacement for Claude Code's
  `${CLAUDE_PLUGIN_ROOT}` substitution.
- `.opencode/wiki-knowledge/model-config.json` — the model mapping used for
  this install; edit and re-run to change it later.
- `.gitignore` entries for all of the above (dedicated mode only).

## Install

There are two deployment modes, matching [ADR-0004](docs/adr/0004-deployment-modes-and-vault-root-resolution.md):

### Dedicated (vault is its own project)

Installs into the vault's own `.opencode/` and `.agents/`. Run from the vault root:

```bash
cd /path/to/your-vault
npx @dhague/wiki-knowledge
```

### Query-from-anywhere (global install, `$WIKI_ROOT` points at the vault)

Installs into `~/.config/opencode/` instead, so the agents/commands/skills
are available from any directory, and the vault is selected via `$WIKI_ROOT`
at query time:

```bash
npx @dhague/wiki-knowledge --global

export WIKI_ROOT=/path/to/your-vault   # set per-shell/session, not exported globally
```

### Model config

The installer needs a `provider/model-id` for each canonical model name
(currently `sonnet` and `haiku`). When run interactively it prompts for each,
showing the documented default:

```
OpenCode model id for 'sonnet' (default 'anthropic/claude-sonnet-4-5'):
OpenCode model id for 'haiku' (default 'anthropic/claude-haiku-4-5'):
```

Press enter to accept a default, or type your own `provider/model-id`. To
skip the prompt (e.g. in a script), pass `--model-config` with a JSON file:

```bash
npx @dhague/wiki-knowledge --model-config models.json
```

```json
{
  "sonnet": "openai/gpt-5.6-terra",
  "haiku": "openai/gpt-5.6-luna"
}
```

Keys omitted from the file fall back to the documented defaults. To change
the mapping after install, edit `.opencode/wiki-knowledge/model-config.json`
and re-run with `--model-config` pointed at it.

## Verify the install

```bash
opencode agent list       # should list wiki-ingest and wiki-researcher as subagents
opencode debug skill      # should list all six wiki-* / save-conversation skills
```

Then from inside the vault (dedicated mode) or with `$WIKI_ROOT` set (global
mode), the same slash commands work as in Claude Code: `/wiki-init`,
`/wiki-ingest`, `/wiki-ask`, `/wiki-watch`, `/save-conversation`,
`/wiki-conventions`.

## Updating after a plugin upgrade

Re-run the installer from the vault root (or with `--global`). It always
overwrites `.opencode/agents/`, `.opencode/commands/`, the skills, and the
runtime bundle. Pass `--model-config` to reuse your existing mapping:

```bash
cd /path/to/your-vault
npx @dhague/wiki-knowledge \
  --model-config .opencode/wiki-knowledge/model-config.json
```

## Limitations

- OpenCode has no `${CLAUDE_PLUGIN_ROOT}` equivalent; scripts locate the
  plugin root via `.opencode/wiki-knowledge/config.json` instead.
- This wiring is validated in CI (`.github/workflows/opencode-wiring.yml`)
  against a pinned OpenCode version and a mocked model backend — see that
  workflow for the exact commands it runs if something doesn't match your
  local behavior.
- Broader OpenCode parity (statusline, further host-neutral polish) is still
  tracked on the wayfinder map, [#1](https://github.com/dhague/wiki-knowledge/issues/1).
