# OpenCode install ships as an npm deployer package (`@dhague/wiki-knowledge`)

[#217](https://github.com/dhague/wiki-knowledge/issues/217) wants the plugin installable without cloning the repo. Claude Code gets `/plugin marketplace add dhague/wiki-knowledge` (ADR-0016); OpenCode has no marketplace — its only remote-install primitive is the npm registry. [#218](https://github.com/dhague/wiki-knowledge/issues/218) therefore resolves: publish **`@dhague/wiki-knowledge`**, an npm package whose `bin` is a pure deployer — `npx @dhague/wiki-knowledge` copies a build-time-assembled runtime subset into the vault, making the vault self-contained. No cloning, no Python, no generation at install time.

## Why npm, and why a deployer

OpenCode's only documented remote-install primitive is npm (`plugin` array in `opencode.json` / `opencode plugin <pkg>`); there is no GitHub-marketplace equivalent. skills.sh (`npx skills add <owner/repo>`) was evaluated and rejected: it installs bare `SKILL.md` files only — no agents, commands, session-tracker, model config, or config merge — and OpenCode ignores `hooks:`/`context:` frontmatter, so the generated-and-shared install architecture ([#71](https://github.com/dhague/wiki-knowledge/issues/71)/[#90](https://github.com/dhague/wiki-knowledge/issues/90)) could not ride along.

An OpenCode **plugin** package (a `server`/`tui` entrypoint) was also considered and rejected as the vehicle: it delivers plugin *behavior* only (hooks, env, tools) and cannot install agents, commands, or skills into a vault — those are config/skill-dir files, so a file-deploy step is unavoidable regardless. The npm package's honest job is therefore the deploy.

## The shape

- **Build-time assembly.** A release step runs `generate-opencode.py` against the canonical Claude Code sources and packages the pre-generated `.opencode/agents/*.md` + `.opencode/commands/*.md`, the six skill dirs, `wiring/opencode/plugins/session-tracker.ts`, the config templates, and the `enchiridion` `.cjs`/`.wasm` pair (the [ADR-0017](0017-bundled-typescript-on-installed-interpreter.md) artifacts, v0.9.0). Install = pure copy; no Python, no `generate-opencode.py` at install time.
- **Self-contained vault.** The deployer copies the surface into the vault: skills → `<vault>/.agents/skills/` (OpenCode's native skill discovery — the `opencode.json` `skills.paths` merge from `install-opencode.py` is dropped), agents/commands → `.opencode/agents|commands/`, session-tracker → `.opencode/plugins/`, marker + model-config + runtime → `.opencode/wiki-knowledge/`. `--global` targets `~/.config/opencode/` (query-from-anywhere, ADR-0004). Re-running `npx @dhague/wiki-knowledge` updates the vault.
- **Deployer-owned, not vault content.** The deployer appends the whole deployed surface to the vault's `.gitignore`; the installed artifacts are regenerated on re-install, never edited by hand.
- **Versioning.** The npm version is coupled to `plugin.json`'s version — one cut-release bumps both — and `npm publish` is a human step in the release script (no CI keys).

## Why a self-contained vault reverses #90's "shared, not copied"

The original OpenCode install kept one canonical copy of the skill bodies in the plugin and pointed at it via `skills.paths`, on the assumption the plugin root was a stable clone. Under npm packaging the package *is* the snapshot: it lives in an ephemeral `npx` cache, so a `skills.paths` pointer into it would be version-dependent and fragile. Copying the snapshot into the vault is therefore not a drift hazard — re-running the deployer is the update mechanism, exactly like re-installing. The vault stops needing the repo at runtime.

## Consequences

- **Node/npm is a stated install-time prerequisite for the OpenCode path** (npx), unlike the Claude Code marketplace path — the honest price of the one-liner.
- **Installed vaults carry copies of the six skill dirs** (gitignored); skill updates arrive by re-running the deployer.
- **The Bun-runtime question is in scope** ([#293](https://github.com/dhague/wiki-knowledge/issues/293)): the shipped `.cjs`/`.wasm` pair must run under Bun as deployed (OpenCode is a Bun-compiled binary; the bundle is CJS and Bun runs CJS), and the deployer fixes the skills' invocation path. Research at `docs/research/opencode-skills-node-scripts.md` confirms `bun` is guaranteed present wherever OpenCode runs.