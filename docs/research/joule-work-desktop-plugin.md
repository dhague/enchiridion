# Making the wiki-knowledge plugin installable from SAP Joule Work Desktop via the AI Skills Library

Research for [#220](https://github.com/dhague/wiki-knowledge/issues/220). The ask: establish, from primary sources, how to build and ship a Joule Work Desktop "AI Skills Library plugin" for the wiki-knowledge system — retrieval + ingestion skills plus a thin MCP server wrapping the `enchiridion` script layer — in enough detail to design ours.

Sources are primary or near-primary: the AI Skills Library's own docs site, its GitHub repo and issue templates, its catalog pages (including the JSON each detail page is built from), the Open Plugins specification, the Agent Skills specification, the `npx skills` CLI README, SAP help portal documents and PDFs, the SAP help search API, the current MCP specification, and two SAP-published Community blogs (one of which is a first-person Joule Work Desktop account). Claims are labelled **[verified]** where read from a primary source, **[inferred]** where reasoned from those, and **[unverified]** where I could not find a source — those are the EAC-gated details, and I say so rather than guess.

---

## Verdict (distilled answers to the ticket)

**(a) Concrete SKILL.md + plugin structure.** Publish **skills** first: each is `skills/<slug>/SKILL.md` with `name` (lowercase-hyphen slug matching the folder) and a trigger-rich `description` in YAML frontmatter — the two required fields per the library's [contribution rules](https://github.com/SAP/ai-skills-library/blob/main/CONTRIBUTING.md) and the [Agent Skills spec](https://agentskills.io/specification). Optional-but-used-in-the-wild frontmatter: `license`, `metadata.author`, `compatibility`, `allowed-tools`, `argument-hint`. The **"Add to Joule Work Desktop" button today is offered for skills only** — plugin catalog entries get only a `/plugin install` CLI command ([verified] from the library's own per-item data). For a plugin, the *standard* is the [Open Plugins](https://open-plugins.com) layout (`.plugin/plugin.json` + `skills/` + `.mcp.json`), but every real catalog entry actually uses the Claude Code marketplace conventions (`.claude-plugin/marketplace.json`, or `plugins/<name>/plugin.json` + sibling `.mcp.json`) — the library documents Open Plugins while onboarding Claude-format repos.

**(b) Does Joule consume stdio MCP?** **No evidence for stdio; yes for HTTP.** A [SAP-published blog](https://community.sap.com/t5/technology-blog-posts-by-sap/contextualize-joule-work-desktop-with-obsidian-notes/ba-p/14462509) written with a real Joule Work Desktop user describes its "Connectors" as **HTTP** — "that fits my use of HTTP for Connectors rather than stdio" — added by pasting a URL into Settings → Connector → Manage Connectors. The MCP servers that demonstrably work in JWD (SAP LeanIX, the OAuth'd `mcp.leanix.net` endpoint) are HTTP. The **MCP spec version JWD speaks is [unverified]** — the Desktop help set is EAC-gated and absent from the public help index.

**(c) Can the host model do multi-step tool use?** **Yes.** Joule Work's "Agentic Thinking" conversation mode runs "a reasoning engine" that "autonomously plan[s], decompose[s], and execute[s] tasks step by step", selects and combines skills dynamically, and "iterate[s] through multiple steps until the task is complete" ([verified], [Joule Work help §4.2](https://help.sap.com/docs/JOULE)). Skills are only active in that mode. So IngestPlan-authoring-then-`ingest` is plausible. **The exact model is [unverified]** (SAP publishes no model name; conversations go to "SAP-hosted large language models"). One real risk: **custom skills in Joule Work run in a sandbox with no outbound network** ([verified], web product help) — whether JWD-imported skills are similarly restricted is unverified.

**(d) Binary bundling.** The **Open Plugins standard explicitly permits a plugin-relative compiled binary** as a stdio command (`"command": "./bin/validator"`, with `PLUGIN_ROOT`/`PLUGIN_DATA` env vars) — so bundling `enchiridion` works for stdio-capable hosts (Claude Code, OpenCode). **But JWD connectors are HTTP URLs, so for Joule Work Desktop the server must be reachable over HTTP** (hosted wrapper around the binary), not bundled-and-stdio. No catalog entry bundles a binary today — all stdio servers are `npx` npm packages.

**(e) Registration steps + blockers.** Public GitHub repo with `skills/<slug>/SKILL.md` + author/license → open the [Register a New Skill](https://github.com/SAP/ai-skills-library/issues/new?template=new-skill.yml) issue → maintainer reviews and onboards → the entry appears at **Community** trust level (SAP Certified / Partner Verified are the other two). Updates are just pushes to your own repo. **Blockers:** Joule Work and Joule Work Desktop are **not GA** — EAC-only ([verified] from the SAP tutorial and SAP's UX Q3 blog); testing requires an EAC tenant plus the Desktop app; the Desktop docs are gated; "Add to Joule Work Web" is flagged "Coming soon"; and no install path conveys configuration (env vars / vault root) — the deep link carries only `repository` + `name`.

---

## 1. AI Skills Library mechanics

The library at [skills.cloud.sap](https://skills.cloud.sap/) is a public catalog of reusable AI agent components, built on a bring-your-own-repo model. Its [docs page](https://skills.cloud.sap/docs) is the authoritative overview.

### Item types and trust levels

**[verified]** [docs → "What's in the catalog"](https://skills.cloud.sap/docs): four entry kinds:

- **Skill** — "a markdown file that teaches an AI assistant how to perform a specific, repeatable task", defined in the `SKILL.md` format, loaded on demand.
- **Plugin** — "a bundle of related entries in one installable unit… can combine several skills and MCP servers — but it can also be as small as a single skill". Versioned and trust-rated as one unit.
- **MCP Server** — connects agents to external tools/data "over **HTTP** (a hosted URL) or **STDIO** (a local command)". May stand alone, be declared by a plugin, or be referenced by skills.
- **Marketplace** — "a named source that groups entries together. Add a marketplace once, then install any plugin or skill listed within it."

**[verified]** [docs → "Trust & checks"](https://skills.cloud.sap/docs): every entry carries a trust level — **SAP Certified** (developed/maintained by SAP), **Partner Verified** (verified SAP partner), **Community** (contributed). Skills may also display automated **checks** such as **SkillSpector**; a passing check "complements, but doesn't replace, the trust level". The library's per-skill data records `checks.skillspector` as `{providerVersion, verdict, status, severity}` ([verified] from the catalog JSON).

### SKILL.md schema

**[verified]** [tutorial, step 4](https://developers.sap.com/tutorials/ai-skills-library.html): a skill lives in `skills/<skill-slug>/SKILL.md` and "must include at minimum a `name` and `description` at the top". Author and license must be discoverable somewhere in the repo (README, LICENSE, or package.json).

**[verified]** [CONTRIBUTING.md](https://github.com/SAP/ai-skills-library/blob/main/CONTRIBUTING.md): the `description` "is what appears on the AI Skills Library listing and drives AI trigger matching — make it specific and trigger-rich."

**[verified]** The [Agent Skills specification](https://agentskills.io/specification) (which Joule Work itself says custom skills follow, and which `npx skills` installs) pins the constraints:

| Field | Required | Constraint |
|---|---|---|
| `name` | Yes | ≤ 64 chars, lowercase letters/digits/hyphens, no leading/trailing/consecutive hyphens, **must match the parent directory name** |
| `description` | Yes | ≤ 1024 chars, describes what the skill does and when to use it, keyword-rich for trigger matching |
| `license` | No | SPDX name or file reference |
| `compatibility` | No | ≤ 500 chars; environment requirements |
| `metadata` | No | arbitrary string map (e.g. `author`, `version`) |
| `allowed-tools` | No | experimental space-separated tool allow-list |

Real library entries use this extended schema — e.g. SAP LeanIX's `skills/automations-toolkit/SKILL.md` carries `name`, `description`, `argument-hint`, `license`, `compatibility`, `metadata.author`, `metadata.version`, and `allowed-tools` including `"mcp__leanix__*"` ([verified], repo cloned). So for our skills: keep `name`/`description` per spec, add `license`, `metadata.author`, and — for anything that shells out — `compatibility`/`allowed-tools`.

Body guidance: keep SKILL.md under ~500 lines / <5000 tokens; push details into `references/`, `scripts/`, `assets/` subfolders ([verified], [Agent Skills spec "Progressive disclosure"](https://agentskills.io/specification)).

### Repository folder contract

**[verified]** [docs → "Contributing"](https://skills.cloud.sap/docs) and the [README](https://github.com/SAP/ai-skills-library):

```
your-repo/
└── skills/
    └── <skill-slug>/
        └── SKILL.md
```

Discovery is not literally single-level: `npx skills` walks `skills/` up to three levels deep and also reads `.claude-plugin/marketplace.json` / `.claude-plugin/plugin.json` if present ([verified], [skills CLI README "Skill Discovery"](https://github.com/vercel-labs/skills)). The `capire/skills` repo even nests skills at `skills/skills/<slug>/` and is listed in the catalog.

Each skill in the catalog carries an ORD-style id `sap.skills:skill:<owner>.<repo>.<slug>:v1` ([verified], catalog JSON, e.g. `sap.skills:skill:ui5.plugins-coding-agents.ui5-best-practices:v1`).

### How a Plugin is structured

The library's docs describe plugins as following the **[Open Plugins](https://open-plugins.com)** standard ([verified], [docs → "Understanding plugins"](https://skills.cloud.sap/docs)):

```
my-plugin/
├── .plugin/
│   └── plugin.json       # Manifest: name, version, metadata
├── skills/               # Agent Skills (SKILL.md)
├── .mcp.json             # MCP tool servers
├── agents/               # Specialized sub-agents
├── hooks/                # Event-driven automation
└── rules/                # Coding standards
```

"When you install a plugin, the host tool scans its directory, discovers the components in their default locations, and registers them. Each component is namespaced under the plugin name… skills become available, and any MCP servers start running." **[inferred]** The library docs' directory diagram shows the Claude-style `.mcp.json` inside the Open-Plugins-style tree — the site is clearly converging the two; the normative portable form is [open-plugins.com](https://open-plugins.com).

The Open Plugins spec, in primary form ([plugin-authors](https://open-plugins.com/plugin-authors), [manifest](https://open-plugins.com/plugin-authors/manifest), [MCP servers](https://open-plugins.com/plugin-authors/mcp-servers)):

- Root `plugin.json` with `$schema` (required, `https://agent-plugins.org/schemas/1.0.0/plugin.schema.json`) and `name` (required) plus optional `version`, `description`, `author`, `homepage`, `repository`, `license`, `keywords`, `extensions`.
- Root `mcp.json` — only `$schema` and `mcpServers`. Transports: `stdio` (`command` required; optional `args`/`env`/`cwd`), `streamable-http` (`url` + optional literal `headers`), `sse` (deprecated). **`command` is one executable token — either a bare name resolved by platform search, or a plugin-relative path starting with `./`** (this is the hook for a bundled binary, §4).
- Clients inject `PLUGIN_ROOT` and `PLUGIN_DATA` env vars and expand `${PLUGIN_ROOT}`/`${PLUGIN_DATA}` in `args`, `env` values, and `cwd`.
- Failure isolation: an invalid `mcp.json` disables MCP for the plugin but not its skills.

**However — the deployed reality in the catalog is the Claude Code marketplace format**, not the Open Plugins layout ([verified] by cloning the repos behind three catalog plugins):

1. **`SAP/leanix-ai-plugins`** — root `.claude-plugin/marketplace.json` declares plugin `sap-leanix` with `mcpServers: {"leanix": {"type": "http", "url": "https://mcp.leanix.net/services/mcp-server/v1/mcp?toolsets=…"}}` and `skills: ["./skills/automations-toolkit", "./skills/calculations-toolkit"]`. Skills live at `skills/<slug>/SKILL.md`.
2. **`UI5/plugins-coding-agents`** — one plugin per `plugins/<name>/` dir: `plugins/ui5/plugin.json` (`name`, `version`, `description`, `author`, `license`, `skills: ["./skills/"]`) plus sibling `plugins/ui5/.mcp.json` (`{"mcpServers":{"ui5-mcp-server":{"command":"npx","args":["-y","@ui5/mcp-server"]}}}`).
3. **`capire/skills`** — `.claude-plugin/marketplace.json` + `.claude-plugin/plugin.json`, skills at `skills/skills/<slug>/`.

So the pragmatic answer for a new plugin: satisfy the library's stated Open Plugins contract *and* the format the current catalog tooling actually ingests (Claude marketplace manifests), i.e. ship both `.mcp.json` (or `mcp.json`) and a plugin manifest the library's indexer recognises.

### How install works, and what it can carry

**[verified]** [docs → "Installing"](https://skills.cloud.sap/docs) and the library's own client code:

- **Add to Joule Work Desktop** — a deep link. Reverse-engineering the library's web app (chunk `0nbid_aak72ca.js`) gives the exact shape:

  ```
  joule://install-skill?repository=https://github.com/<owner>/<repo>&name=<skillName>&origin=skills.cloud.sap
  ```

  It opens the Desktop app and installs the named skill locally. **The payload is just `repository` + `name` (+ `origin`) — there is no channel for env vars, args, or a vault root.** **[verified]** Only **skill** entries get this button; every catalog **plugin** entry's `installationCommands` array contains only `{"type":"cli", …}`. (The button renders "Not supported here"/"Coming soon" via per-item feature flags; `jouleWebEnabled` is currently `false`, `jouleDesktopEnabled` `true`.)
- **Add to Joule Work Web** — same mechanics, currently disabled ("Coming soon").
- **CLI (`npx`)** — the current command, per the [library README](https://github.com/SAP/ai-skills-library) and every skill's install snippet, is the open-source Vercel tool:

  ```bash
  npx skills add <owner>/<repo> --skill <slug>
  ```

  The [skills CLI](https://github.com/vercel-labs/skills) resolves the repo, discovers `SKILL.md` files, and symlinks/copies them into agent skill directories (`./<agent>/skills/` or `~/<agent>/skills/`). It also reads Claude plugin manifests for skill paths. **[verified] It cannot convey configuration either** — no env/args/vault-root hook. (The tutorial's `npx -y @sap/skill-install <slug>` is stale: the npm package `@sap/skill-install` does not exist on the public registry as of this writing, and the live catalog uses `npx skills`.)
- **Plugin install** (plugin-capable agents): `/plugin marketplace add <owner>/<repo>` then `/plugin install <name>@<marketplace>` ([verified], [docs](https://skills.cloud.sap/docs)). "Installing a plugin also registers the MCP servers it declares."
- **MCP server alone** — a copy-into-`.mcp.json` snippet matching the transport; the docs give `{"mcpServers": {"ui5": {"type": "stdio", "command": "npx", "args": ["-y", "@ui5/mcp-server"]}}}` for STDIO and `{"type":"http","url":…}` for HTTP.

**Design consequence for us:** configuration cannot ride the install. The vault root, git remote, and any runtime knobs must come from somewhere else — an environment the host provides (e.g. Open Plugins' `PLUGIN_DATA`), a first-run dialog the skill instructs the model to run, an explicit instruction in the SKILL.md body ("point me at your vault folder"), or a hosted server whose URL embeds the target. This mirrors how the wiki-knowledge plugin already handles vault-root resolution in Claude Code ([ADR-0004](https://github.com/dhague/wiki-knowledge/blob/main/docs/adr/0004-deployment-modes-and-vault-root-resolution.md)) — but on JWD the resolution prompt can't be a shell the agent controls.

### Bring-your-own-repo registration, the review gate, and trust levels

**[verified]** [CONTRIBUTING.md](https://github.com/SAP/ai-skills-library/blob/main/CONTRIBUTING.md), the [docs](https://skills.cloud.sap/docs), and the issue template [`new-skill.yml`](https://github.com/SAP/ai-skills-library/issues/new?template=new-skill.yml):

1. Make the repo public on github.com with the `skills/<slug>/SKILL.md` layout; author + license discoverable.
2. Open a "Register a New Skill" issue: repository URL, a list of skills, and a readiness checklist (public repo; `SKILL.md` with `name`/`description`; author info; license info).
3. A maintainer reviews and onboards the repo. Once approved the entry appears at **Community** trust level. Updates need no re-registration — "edit your own repository directly — the catalog reflects your latest published content."

A Community-level plugin needs nothing beyond this checklist; **SAP Certified** implies SAP authorship and **Partner Verified** implies partner status, neither available to us. The library's own contributions (rules for PRs *into* SAP/ai-skills-library) require Apache-2.0 and DCO, but that governs contributions to the platform repo, not third-party bring-your-own-repo content.

---

## 2. MCP on Joule Work Desktop

**Does it consume MCP servers at all?** **[verified]** Yes. A [SAP-published Community blog](https://community.sap.com/t5/technology-blog-posts-by-sap/contextualize-joule-work-desktop-with-obsidian-notes/ba-p/14462509) — authored with a genuine JWD beta user (Mathias Kemeter) — describes adding **VaultGate**, a local MCP server, to JWD as a **Connector**, and SAP's own UX-team [Q3/2026 update](https://community.sap.com/t5/technology-blog-posts-by-sap/sap-ux-q3-2026-update-part-1-ai-joule-mobile-sap-build-work-zone/ba-p/14451534) states JWD works with "skills and **MCP** servers". SAP's [LeanIX "Connecting to the MCP Server"](https://help.sap.com/docs/leanix/ea/mcp-server) documents an HTTP MCP endpoint that the [SAP LeanIX Spaces demo](https://www.youtube.com/watch?v=9lWa-OZAnY0) shows being used live inside JWD. (Be careful with one popular source here: Marian Zeis's widely-shared "Testing Joule Work Desktop" post is **not** a real test — the author disclosed he never had access and rebuilt a lookalike; do not cite it as evidence of JWD internals.)

**Which transport?** **[verified]** HTTP. The same SAP blog: VaultGate "has **HTTP transport**: works with my Connector model; no stdio required," and it is added by pasting **a URL** into *Settings icon in the conversation input → Select Connector → Manage Connectors → add the URL*. The LeanIX server the library lists is `type: http`. The blog's "rather than stdio" wording **[inferred]** indicates stdio is not the JWD connector model (whether JWD supports stdio at all is **[unverified]**).

**Which MCP spec version?** **[unverified].** The Desktop help set (`help.sap.com/docs/JOULE_WORK_DESKTOP`, referenced from the blog) is **absent from the public SAP help index** — the search API returns no JOULE_WORK_DESKTOP topics, and the deliverable-metadata endpoint behind it redirects to login. I could not probe a spec version off the public LeanIX endpoint either (it requires OAuth bearer auth). For context: the current MCP specification revision is **[2026-07-28]** ([modelcontextprotocol.io/specification](https://modelcontextprotocol.io/specification)); since the 2025-06-18 revision, **Streamable HTTP** is the standard HTTP transport, replacing the deprecated HTTP+SSE. The Open Plugins `mcp.json` schema (which the library documents) uses the exact type names `stdio`, `streamable-http`, `sse`, so an HTTP MCP server built to Streamable HTTP is the defensible target.

**How does a host register an MCP server?** **[verified]** In-app, by URL, per the blog's Manage Connectors flow; the AI Skills Library's MCP-server pages offer a copyable `.mcp.json` snippet for the Claude Code path instead. Whether installing a *plugin* auto-registers its declared MCP server **in JWD** is **[unverified]** (the library's docs claim the general plugin install registers servers, but JWD's connector UI is URL-based and the deep link is skill-only).

**Tool-schema constraints.** None documented publicly. **[unverified]** any JWD-specific limits.

**For our design:** a JWD-facing MCP server should expose **Streamable HTTP** at a URL the user pastes into Manage Connectors, with OAuth (LeanIX) or bearer auth. A stdio-only server would at best serve the Claude Code / OpenCode hosts, not JWD.

---

## 3. Host-model capability

**Which model drives JWD?** **[unverified].** SAP publishes no model name for Joule Work / Desktop. The library tutorial's AI notice is the only public statement: "Conversations are sent to SAP-hosted large language models for processing" ([developers.sap.com/tutorials/ai-skills-library.html](https://developers.sap.com/tutorials/ai-skills-library.html)). Joule in general is a multi-model offering via SAP's AI Core; the specific model(s) behind JWD are not disclosed. Do not assert a model.

**Multi-step tool use — the design-critical question.** **[verified]** The [Joule Work help (PDF, §4.2 Conversation Modes)](https://help.sap.com/doc/525677ed302148a4b7cc9988321055fb/CLOUD/en-US/dc1706e63f4d443f9da2aeae83a2277e.pdf) describes **Agentic Thinking** mode: "it uses a reasoning engine to autonomously plan, decompose, and execute tasks step by step", "**Select and combine agent skills dynamically** to complete your task", and "**Iterate through multiple steps until the task is complete**". Skills run only in this mode ("Skills enable Joule to handle complex scenarios by selecting and combining the appropriate skills dynamically… agentic thinking mode must be enabled", §6 Skills). Tool calls are surfaced to the user ("Tool calls remain visible" per the SAP blog). So the loop our ingestion needs — model authors an IngestPlan JSON, calls `enchiridion ingest`, reads the result, iterates — is within what Joule Work's orchestration is documented to do. The Desktop is the same product family ([verified] "Joule Work desktop is the native desktop app of Joule Work", help PDF intro), though Desktop-specific orchestration details are behind the EAC gate.

**Skill selection and length.** Skills are auto-selected by the `description` ("The description is what Joule reads first to decide whether your skill fits a user's request", help PDF §6.2). Instructions should be clear sequential steps with edge cases; "bundle scripts for mechanical steps" is explicitly recommended — which is exactly the skill-shells-to-binary shape we already have.

**A caution specific to custom skills:** [verified] Joule Work custom skills "execute in an isolated sandbox environment **without external network connectivity**… cannot make outbound HTTP requests, call external APIs, or access Internet resources" (help PDF §6). That is the *web* product's custom-skill contract. JWD is a native app with "secure sandbox" local-file access, and its Connectors are how tools get external/network access — so the sane architecture is: skills stay pure instructions; every network/exec side effect goes through an MCP connector (our enchiridion server). Whether the web product's no-outbound-network rule applies to JWD-imported skills is **[unverified]** and worth testing inside the EAC before committing to a skill-only design that shells out.

---

## 4. Packaging — can a plugin bundle a compiled binary?

**Per the standard: yes, for stdio hosts.** [verified] The [Open Plugins MCP config](https://open-plugins.com/plugin-authors/mcp-servers) explicitly allows `"command": "./bin/validator"` — a plugin-relative executable — with `args`/`env`/`cwd` and the `PLUGIN_ROOT`/`PLUGIN_DATA` variables. So the Open Plugins packaging contract (which the library says plugins follow) permits shipping a plugin-relative executable inside the plugin and launching it via stdio. Claude Code / OpenCode consume `.mcp.json` stdio entries exactly this way, so a bundled-executable plugin is directly consumable by the hosts the wiki-knowledge plugin already targets.

**But there is no evidence any catalog entry ships a binary, and JWD wouldn't reach it.** [verified] Every MCP server currently listed in the catalog is either an **npm package launched via `npx`** (`@ui5/mcp-server`, `chrome-devtools-mcp@latest`) or a **hosted HTTP URL** (LeanIX). The de-facto distribution shape for a catalog MCP server is "reference an external package/URL", not "bundle a binary". And because JWD's Connector model is a pasted **URL**, a binary sitting inside an installed plugin is invisible to JWD — the plugin's MCP server must be reachable over HTTP, which means hosting a streamable-HTTP wrapper around `enchiridion` (or shipping it as an npm package JWD could conceivably run — unverified).

**Conclusion for our packaging ticket:** build the thin MCP server so it can run two ways: (1) as a bundled `./bin/enchiridion` stdio server declared in the plugin's `mcp.json`/`.mcp.json` for Claude Code/OpenCode; (2) as a streamable-HTTP server behind a URL for JWD's Manage Connectors. Keep the self-contained `./bin/enchiridion` path (the script layer runs on the already-installed Node, so `bin/enchiridion` is a thin `exec node` shim over the bundled bundle) — a `./bin` path is exactly what the Open Plugins contract supports.

---

## 5. Working examples (real catalog entries)

All three are live in the catalog (skills.cloud.sap); I cloned the repos and read the library's per-item JSON.

### 5.1 SAP LeanIX — the one plugin that bundles skills + an MCP server

- Catalog entry: [SAP LeanIX plugin](https://skills.cloud.sap/plugins/SAP/leanix-ai-plugins/sap-leanix) — **2 skills, 1 MCP server**, trust level SAP Certified. Marketplace: [SAP LeanIX](https://skills.cloud.sap/marketplaces/SAP/leanix-ai-plugins).
- MCP server entry: [leanix](https://skills.cloud.sap/mcp/mcp-leanix-net) — **HTTP** transport, `https://mcp.leanix.net/services/mcp-server/v1/mcp?toolsets=inventory,automations,calculations,custom_reports`, OAuth-authenticated (verified by probing the endpoint: 401 `invalid_token`).
- Repo layout ([github.com/SAP/leanix-ai-plugins](https://github.com/SAP/leanix-ai-plugins)):
  ```
  .claude-plugin/marketplace.json   # declares plugin "sap-leanix"
  skills/automations-toolkit/SKILL.md   (+ examples/, references/)
  skills/calculations-toolkit/SKILL.md  (+ examples/, references/)
  ```
  `marketplace.json` pins `mcpServers.leanix` (type `http`, url) and `skills` as a path array. The skills' `allowed-tools` includes `"mcp__leanix__*"` and `compatibility` says "Requires LeanIX MCP server for API access (mcp__leanix__* tools)" — i.e. the **skill references the plugin's own MCP server by tool-name pattern**. Install from the catalog: `/plugin marketplace add SAP/leanix-ai-plugins` + `/plugin install sap-leanix@leanix-ai-plugins`; the README notes the MCP server "connects automatically on first use" in Claude Code.

### 5.2 UI5 — skills + a stdio MCP server, distributed via Claude Code's official marketplace

- Catalog entry: [UI5 plugin](https://skills.cloud.sap/plugins/UI5/plugins-coding-agents/ui5) — **7 skills, 1 MCP server**, SAP Certified, installed via `/plugin install ui5@claude-plugins-official`.
- MCP server entry: [ui5-mcp-server](https://skills.cloud.sap/mcp/ui5-mcp-server) — **STDIO** transport, `npx -y @ui5/mcp-server` (npm package [@ui5/mcp-server](https://github.com/UI5/mcp-server), `bin: ui5mcp`).
- Repo layout ([github.com/UI5/plugins-coding-agents](https://github.com/UI5/plugins-coding-agents)):
  ```
  plugins/ui5/plugin.json     # name, version, description, author, license, "skills": ["./skills/"]
  plugins/ui5/.mcp.json       # {"mcpServers":{"ui5-mcp-server":{"command":"npx","args":["-y","@ui5/mcp-server"]}}}
  plugins/ui5/skills/<slug>/SKILL.md   (+ references/, tests/ for some skills)
  ```
  One plugin per `plugins/<name>/` directory; the MCP server is a sibling `.mcp.json`, not inside plugin.json.

### 5.3 chrome-devtools — a standalone MCP server entry

- Catalog entry: [chrome-devtools](https://skills.cloud.sap/mcp/chrome-devtools-mcp) — STDIO, `npx -y chrome-devtools-mcp@latest`, Community, source [ChromeDevTools/chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp).

**Pattern summary:** the MCP-server relationship shows up twice on each detail page — the MCP server lists "declared by N plugin(s), referenced by N skill(s)" and the plugin lists its servers (llms.txt / detail pages). Both LeanIX (HTTP) and UI5 (stdio, npm) are the two shapes a JWD-facing server must cover.

---

## What I could not verify

- **JWD's MCP spec version and whether stdio is supported at all.** Desktop help is EAC-gated and absent from the public index; the only public MCP-capable JWD server (LeanIX) requires OAuth and exposes no version anonymously.
- **The model driving JWD.** Not publicly disclosed.
- **Whether the web product's no-outbound-network sandbox applies to JWD-imported skills.** The help doc pins it for *custom skills in Joule Work*; Desktop specifics are unpublished.
- **Whether installing a library Plugin auto-registers its MCP server inside JWD.** The library claims plugin installs register servers, but JWD's connector flow is manual URL entry and plugin entries currently get no Joule deep-link.
- **`@sap/skill-install`.** The tutorial's CLI command references an npm package that doesn't exist publicly; the live catalog uses `npx skills`. It may be pre-GA/private or retired.
- **What the JWD app does after receiving `joule://install-skill?repository=…&name=…`** (fetch the repo? read the skill? where does it land? can it install a plugin's MCP server?). The scheme is verified from the library's web app; the receiving end is EAC-gated.
- **SkillSpector's exact quality criteria** (the library only names it as an automated check).

---

## Appendix: sources

**AI Skills Library** (primary):
[docs site](https://skills.cloud.sap/docs) — item types, plugins/Open Plugins, install methods, trust levels, contributing ·
[skills.cloud.sap](https://skills.cloud.sap/) catalog + [`/llms.txt`](https://skills.cloud.sap/llms.txt) (the "Agent view") ·
[SAP/ai-skills-library](https://github.com/SAP/ai-skills-library) ([README](https://github.com/SAP/ai-skills-library#readme), [CONTRIBUTING.md](https://github.com/SAP/ai-skills-library/blob/main/CONTRIBUTING.md), [new-skill.yml template](https://github.com/SAP/ai-skills-library/issues/new?template=new-skill.yml), hosted skills `skills/sap-fiori-guidelines/SKILL.md`) ·
[tutorial "Get Started with the AI Skills Library"](https://developers.sap.com/tutorials/ai-skills-library.html) ·
library web-app deep-link construction (`joule://install-skill?repository=…&name=…&origin=…`) read from chunk `0nbid_aak72ca.js`; feature flags (`jouleDesktopEnabled`, `jouleWebEnabled`) from the homepage payload.

**Open Plugins / Agent Skills / skills CLI** (primary):
[open-plugins.com](https://open-plugins.com) · [plugin manifest](https://open-plugins.com/plugin-authors/manifest) · [MCP servers / mcp.json](https://open-plugins.com/plugin-authors/mcp-servers) · [agentplugins/agent-plugins-example](https://github.com/agentplugins/agent-plugins-example) ·
[agentskills.io/specification](https://agentskills.io/specification) ·
[vercel-labs/skills](https://github.com/vercel-labs/skills) (npx skills) ·
npm registry probes: `skills` (latest 1.5.22), `@sap/skill-install` (does not exist).

**Real catalog repos** (cloned):
[SAP/leanix-ai-plugins](https://github.com/SAP/leanix-ai-plugins) ·
[UI5/plugins-coding-agents](https://github.com/UI5/plugins-coding-agents) ·
[UI5/mcp-server](https://github.com/UI5/mcp-server) ·
[capire/skills](https://github.com/capire/skills) ·
catalog entries: [SAP LeanIX](https://skills.cloud.sap/plugins/SAP/leanix-ai-plugins/sap-leanix), [leanix MCP](https://skills.cloud.sap/mcp/mcp-leanix-net), [UI5](https://skills.cloud.sap/plugins/UI5/plugins-coding-agents/ui5), [ui5-mcp-server](https://skills.cloud.sap/mcp/ui5-mcp-server), [chrome-devtools](https://skills.cloud.sap/mcp/chrome-devtools-mcp).

**Joule Work / Desktop / MCP** (SAP primary):
[Joule Work help PDF](https://help.sap.com/doc/525677ed302148a4b7cc9988321055fb/CLOUD/en-US/dc1706e63f4d443f9da2aeae83a2277e.pdf) (§4.2 conversation modes, §6 skills, sandbox/no-network restriction, desktop-as-native-app note) ·
[SAP help search API](https://help.sap.com/http.svc/search) — JOULE_WORK_DESKTOP topics absent from the public index; deliverable-metadata endpoint login-gated ·
[Contextualize Joule Work Desktop with Obsidian Notes](https://community.sap.com/t5/technology-blog-posts-by-sap/contextualize-joule-work-desktop-with-obsidian-notes/ba-p/14462509) (SAP-published; HTTP connectors, Manage Connectors by URL, VaultGate, JWD user-guide URL) ·
[SAP UX Q3/2026 Update – Part 1](https://community.sap.com/t5/technology-blog-posts-by-sap/sap-ux-q3-2026-update-part-1-ai-joule-mobile-sap-build-work-zone/ba-p/14451534) (EAC-only, macOS/Windows, skills + MCP servers) ·
[Joule Work product page](https://www.sap.com/products/artificial-intelligence/joule-work.html) (web/desktop/mobile surfaces) ·
[SAP LeanIX MCP Server docs](https://help.sap.com/docs/leanix/ea/mcp-server) (HTTP endpoint, OAuth) ·
[LeanIX Spaces in JWD demo](https://www.youtube.com/watch?v=9lWa-OZAnY0) ·
MCP spec ([modelcontextprotocol.io/specification](https://modelcontextprotocol.io/specification), 2026-07-28 revision; [transports](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports)).

**Not cited as evidence (disclosure):** [Marian Zeis, "Testing Joule Work Desktop…"](https://blog.zeis.de/posts/2026-08-11-joule-work-desktop/) — the author disclosed he never had access to JWD and rebuilt a lookalike ("Werkbank"); useful only as a demo of the deep-link-shaped install pattern (`werkbank://`), not as JWD fact.
