# Can OpenCode skills run node scripts?

## Question

OpenCode (https://opencode.ai, GitHub: github.com/anomalyco/opencode) is a terminal AI coding agent whose "skills" are `SKILL.md` files (markdown + YAML frontmatter) that inject instructions into an agent session. Does OpenCode's skill system support executing scripts — shelling out to `node` — or is a skill purely instructional text? Specifically: is there a `script` field in skill frontmatter, a `scripts/` directory convention, or an execute mechanism tied to skills?

Sources are primary only: the OpenCode docs site (opencode.ai), the JSON config schema (opencode.ai/config.json), and the OpenCode source tree at `github.com/anomalyco/opencode` (cloned at commit `ff3ef6e`, 2026-08-21). Claims are labelled **[verified]** where read from a first-party source, **[inferred]** where reasoned from those.

---

## Verdict

**Yes — in the practical sense that matters — but only through the agent's own Bash tool, never through any script-execution mechanism built into the skill system.**

An OpenCode skill is, structurally, a single markdown file. The skill's job is to inject instructions into the agent's context; it has no runner and no execute hook. What *does* happen is that a skill directory may ship extra files — including a `scripts/` folder with `node` scripts — and OpenCode's skill tool explicitly surfaces that directory to the agent with a file listing and the instruction that relative paths in the skill are relative to that directory. The agent then runs the scripts by shelling out to `node` with its ordinary `Bash` tool. Script execution is the *agent's* doing, not the skill's.

Three consequences fall out:

1. **There is no `script` field in skill frontmatter.** OpenCode recognizes exactly `name`, `description`, `license`, `compatibility`, and `metadata` in the documented schema; the source parses only `name` and `description` (plus an internal, undocumented `slash` boolean), and unknown fields are ignored.
2. **A `scripts/` directory is a convention the skill tool helps with, not a mechanism it enforces.** The tool lists up to 10 sibling files and points the agent at the base directory. Nothing executes them.
3. **If you want script execution with guarantees, skills are the wrong tool.** OpenCode's sanctioned programmatic extension surface is **plugins** (JS/TS modules that can run arbitrary commands through a Bun shell `$` and register custom tools) and **slash commands** (which can inject inline shell output into a prompt). A skill that needs `node` to run reliably should ship its script and instruct the agent to invoke it with Bash — which is exactly how Claude Code skills work too.

---

## 1. What the docs say — skills are text

**[verified]** The [Agent Skills docs page](https://opencode.ai/docs/skills) describes a skill as a `SKILL.md` markdown file with YAML frontmatter. The only recognized frontmatter fields are:

> Only these fields are recognized: `name` (required), `description` (required), `license` (optional), `compatibility` (optional), `metadata` (optional, string-to-string map). **Unknown frontmatter fields are ignored.**

There is no `script` field, no `command` field, and the page never mentions scripts, execution, or a `scripts/` directory anywhere. Discovery locations are `.opencode/skills/`, `~/.config/opencode/skills/`, plus Claude- and agents-compatible paths (`.claude/skills/`, `.agents/skills/`). The page documents exactly one interaction: the agent calls the `skill` tool with a `name`, and the skill's content is injected into the conversation.

**[verified]** The docs' framing is "Skills are loaded on-demand via the native `skill` tool — agents see available skills and can load the full content when needed." The unit of loading is *content* (a markdown string), not a package with behavior.

## 2. What the source says — the skill tool surfaces, it never executes

**[verified]** The skill tool implementation (`packages/opencode/src/tool/skill.ts` in the repo; the current v2 twin is `packages/core/src/tool/skill.ts`) does three things when a skill loads:

1. Looks the skill up by `name` and asserts the skill permission.
2. Injects the parsed markdown content.
3. Lists the other files in the skill's directory and tells the agent where the skill lives.

The model-visible output ends with (`packages/core/src/tool/skill.ts:35-52`, `toModelOutput`):

```
Base directory for this skill: <dir>
Relative paths in this skill (e.g., scripts/, reference/) are relative to this base directory.
Note: file list is sampled.

<skill_files>
  <file>...</file>   (up to 10, sorted, SKILL.md excluded)
</skill_files>
```

The tool's own description (`packages/core/src/tool/skill.ts:27-33`) tells the model the same thing: "The output may contain detailed workflow guidance as well as references to **scripts, files, etc. in the same directory as the skill**." So the `scripts/` path convention is first-party, but it is guidance *to the agent about where files are*, not a loader. **[inferred]** The agent is expected to follow the reference and run the script with its `Bash` tool — `node` is just a binary the agent shells out to, exactly like any other command.

The file listing is **sampled**, capped at `FILE_LIMIT = 10` (`packages/core/src/tool/skill.ts:15`), so a skill directory larger than ~10 files is only partially visible on load.

## 3. What the source says — frontmatter parsing is minimal

**[verified]** The skill loader (`packages/opencode/src/skill/index.ts`) parses each `SKILL.md` with the shared markdown/frontmatter parser and keeps only what `isSkillFrontmatter` accepts (`packages/opencode/src/skill/index.ts:53-59`):

```ts
function isSkillFrontmatter(data: unknown): data is { name: string; description?: string } {
  return (
    isRecord(data) &&
    typeof data.name === "string" &&
    (data.description === undefined || typeof data.description === "string")
  )
}
```

Nothing else in the frontmatter is read. The v2 loader (`packages/core/src/skill.ts:33-37`) decodes the same two fields plus an internal `slash` boolean (`name`, `description`, `slash`) — and `slash` has no consumer in `packages/core/src` at the commit reviewed. **[verified]** A skill can also be loaded *into* the `skill` tool's available list via plugin API (`ctx.skill.transform`, in `packages/plugin/src/v2/promise/`), but that registers sources; it adds no execution.

## 4. Skills can ship scripts, including from remote registries

**[verified]** Because the skill tool points the agent at the skill's whole directory, any files a skill ships — `scripts/*.js`, `reference/*.md`, whatever — are discoverable by the agent. This is true for local skills and for **URL-sourced skills**: `opencode.json` accepts `skills.urls`, and OpenCode pulls remote skills from an `index.json` registry that lists the skill's files (`packages/opencode/src/skill/discovery.ts:49-132` downloads every file named in the index and caches it under `~/.cache/opencode/skills/<name>/`). So a distributed skill can legitimately carry a `scripts/` payload. Nothing about that payload is ever auto-run — it is downloaded to disk and pointed at.

**[verified]** Config schema: `https://opencode.ai/config.json` declares `$defs.Config.properties.skills` as `{ "paths": ["Additional paths to skill folders"], "urls": [...] }`. The loader reads exactly `cfg.skills?.paths` and `cfg.skills?.urls` (`packages/opencode/src/skill/index.ts:210-227`).

## 5. The two mechanisms that *do* execute scripts — and when to prefer them

For completeness, OpenCode's documented ways to run scripts are **not** part of the skill system:

- **[verified]** **Plugins** (`https://opencode.ai/docs/plugins`) are JS/TS modules loaded at startup. They receive a `$` — "Bun's shell API for executing commands" — and can register **custom tools** whose `execute` function runs arbitrary code. This is the sanctioned programmable surface: if you need reliable, structured script invocation, a plugin custom tool or an MCP server is the mechanism, not a skill.
- **[verified]** **Commands** (`https://opencode.ai/docs/commands`, OpenCode's slash commands, a different feature from skills) support inline shell injection: `` !`npm test` `` in a command template "inject[s] bash command output into your prompt" at command-execution time. This is OpenCode itself running the command — but it is scoped to slash commands, not skills.

## 6. OpenCode vs Claude Code on this question

**[inferred]** OpenCode's answer is effectively the same shape as Claude Code's: a skill is markdown, scripts ride along as sibling files, and the *agent* invokes them by shelling out. The difference is cosmetic but real: OpenCode's skill tool is explicit about it — it prints the base directory and a file listing and instructs the model that `scripts/` paths are relative to it — whereas a Claude Code skill just sits in a directory the agent can already see. Neither host has a skill-level script runner; both lean on the agent's Bash tool. If the motivating question is "can a wiki-knowledge-style skill run an `enchiridion` node script," the answer in OpenCode is yes — ship the script (or point the skill at a repo-relative path like `bin/enchiridion`) and instruct the agent to run it with Bash; OpenCode's skill tool will surface the directory and the agent will shell out.

## 7. The runtime: OpenCode bundles Bun, not Node

A corollary worth its own section, because it changes the install story for anyone shipping a script with a skill:

**[verified]** OpenCode does **not** require Node.js to install or run. The [install docs](https://opencode.ai/docs) list exactly two prerequisites — a modern terminal emulator and API keys — and the primary install paths (the `curl https://opencode.ai/install | bash` script, Homebrew, and prebuilt release binaries) ship a self-contained single binary with no runtime dependency. (`npm install -g opencode-ai` is offered as one optional install path among several, not a requirement.) The binary embeds the **Bun** runtime, not Node: the [plugins docs](https://opencode.ai/docs/plugins) describe the plugin context's `$` as "Bun's shell API for executing commands," npm plugins are "installed automatically using Bun at startup," and `.opencode/package.json` dependencies are resolved with `bun install`. The installed binary on macOS (verified locally, `~/.opencode/bin/opencode`) is a single 144 MB Mach-O executable — a bundled runtime, not a launcher for a system interpreter.

The consequence for skill-shipped scripts:

**[inferred]** Because the runtime inside OpenCode is Bun, a skill script written in Node-compatible JavaScript/TypeScript can be run with the bundled `bun` instead of requiring the user to install Node separately. `node` on PATH remains the safest target for maximum portability (a user may or may not have it), but `bun` is guaranteed to exist wherever OpenCode runs — a skill can rely on it. This is the same argument that drives this repo's own script layer ([ADR-0017](https://github.com/dhague/wiki-knowledge/blob/main/docs/adr/0017-bundled-typescript-on-installed-interpreter.md)): ship a bundled bundle and run it on an already-installed interpreter. Here the "already-installed interpreter" is Bun, shipped with OpenCode itself.

---

## Confidence and coverage

**Confidence: high.** The verdict rests on two independent primary sources that agree — the docs page (which defines the entire skill schema and never mentions execution) and the actual skill tool source (which loads markdown, lists files, and has no execute path). The repo was read at `ff3ef6e` (2026-08-21, the current `dev` HEAD), so the state is current.

**What is NOT covered / uncertain:**

- **Bash tool availability and permissioning.** This research assumes the invoking agent has a working `Bash` tool and permission to run it. OpenCode agents can have `Bash` disabled (`tools: { bash: false }`), which would make skill-shipped scripts unrunnable. I did not test a live run against a running OpenCode instance.
- **The internal `slash` frontmatter flag** (v2 `Skill.Info.slash`) has no consumer in `packages/core/src` at this commit; it may be wired up on another branch or in an upcoming release. I did not chase its history.
- **The `FILE_LIMIT = 10` sampling** means a skill directory with more than 10 sibling files shows only a subset on load; whether later tool calls refresh the list is not established here.
- **CLI-driven skills** (e.g. `opencode run skill:name`) were not investigated; only the TUI/agent `skill` tool path.
- **Version drift.** Docs and source were both read on 2026-08-21; both are moving targets.
- **Bun-as-`node` compatibility.** "Node-compatible scripts run with `bun`" is the common case, not a guarantee; scripts using Node-specific built-ins or native modules beyond Bun's compatibility surface may not run. The claim that Bun is bundled is verified; the claim that *any given* node script runs under it is not, and was not tested live.

---

## Appendix: sources

**OpenCode docs** (primary):
[Intro / Install](https://opencode.ai/docs) — prerequisites are a terminal + API keys only (no Node); install script, Homebrew, release binaries, with npm/Bun as optional package-manager installs ·
[Agent Skills](https://opencode.ai/docs/skills) — skill = `SKILL.md` + frontmatter; recognized fields (`name`, `description`, `license`, `compatibility`, `metadata`); "unknown frontmatter fields are ignored"; discovery locations; the `skill` tool and permissions ·
[Plugins](https://opencode.ai/docs/plugins) — JS/TS plugin modules, the `$` Bun shell for executing commands, custom tools, events; npm plugins installed via `bun install` ·
[Commands](https://opencode.ai/docs/commands) — slash commands; `` !`command` `` inline shell output injection ·
[Config schema](https://opencode.ai/config.json) — `$defs.Config.properties.skills` = `{ paths, urls }` ("Additional paths to skill folders")

**OpenCode source** (github.com/anomalyco/opencode, commit `ff3ef6e`, 2026-08-21):
[`packages/core/src/tool/skill.ts`](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/tool/skill.ts) — the v2 `skill` tool: `FILE_LIMIT = 10`, directory glob, base-directory + sampled `<skill_files>` output, "Relative paths in this skill (e.g., scripts/, reference/) are relative to this base directory." ·
[`packages/opencode/src/tool/skill.ts`](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/tool/skill.ts) — the legacy twin, same shape (ripgrep listing, limit 10) ·
[`packages/opencode/src/skill/index.ts`](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/skill/index.ts) — `isSkillFrontmatter` (name + description only), discovery globs, `cfg.skills?.paths` / `cfg.skills?.urls` ·
[`packages/core/src/skill.ts`](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/skill.ts) — v2 loader; frontmatter schema `{ name, description, slash }`; directory + embedded sources ·
[`packages/opencode/src/skill/discovery.ts`](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/skill/discovery.ts) — URL-skill registry pull (`index.json` with `{ skills: [{ name, files, version }] }`), downloads all listed files ·
[`packages/core/src/config.ts`](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/config.ts) — `skills` config field ("Additional paths or URLs to discover skills from")