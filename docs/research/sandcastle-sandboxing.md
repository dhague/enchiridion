# Sandcastle integration for prompt-free sandboxed development

Research for [#24](https://github.com/dhague/enchiridion/issues/24). Scope, per the ticket's clarification, is **development-time**: letting agent sessions that *build and test* the wiki-knowledge plugin run without constant permission prompts. It is not about the shipped plugin's runtime behaviour for end users.

Sources are primary throughout: the sandcastle repo's own source and ADRs, and Claude Code's official docs at `code.claude.com`. Claims are labelled **[verified]** where read directly from a primary source and **[inferred]** where reasoned from those sources.

---

## Verdict

**Rule sandcastle out for this problem.** Adopt the native path instead: a tightened `permissions.allow` set in `.claude/settings.local.json`, plus `additionalDirectories` for the vault, and — if prompts remain annoying — `permissions.defaultMode: "acceptEdits"` or auto mode.

Sandcastle is a real, well-built, Windows-aware project and it *does* work with Podman. It is simply not a tool for the stated job. It solves "run an AFK agent on a prompt, in a container, on a throwaway branch, and merge the commits back". Issue #24 wants "my interactive session on this machine stops asking me to approve `pytest`". Those are different problems, and three specifics of this repo make the container path actively bad (Windows venv, two repos, Resilio Sync).

The decisive platform fact is separate and also worth stating plainly: **Claude Code's own built-in Bash sandbox does not run on native Windows** — so the "just turn on `/sandbox`" answer is unavailable here without WSL2.

---

## 1. What sandcastle actually is

**[verified]** [`mattpocock/sandcastle`](https://github.com/mattpocock/sandcastle) is a TypeScript library published as [`@ai-hero/sandcastle`](https://github.com/mattpocock/sandcastle/blob/main/package.json), MIT licensed, ~7.1k stars, version `0.12.0`, actively developed (created 2026-03-17, pushed 2026-06-29). Its own [README](https://github.com/mattpocock/sandcastle/blob/main/README.md) describes it as:

> A TypeScript library for orchestrating AI coding agents in isolated sandboxes:
> 1. You invoke agents with a single `sandcastle.run()`.
> 2. Sandcastle handles sandboxing the agent with a configurable branch strategy.
> 3. The commits made on the branches get merged back.

**[verified]** It is **its own mechanism, container-based** — not a wrapper around Claude Code's `sandbox-exec`/bubblewrap sandbox. The README lists providers: Docker, Podman, Vercel (Firecracker microVMs), and `noSandbox()`. There is a first-class Podman provider at [`src/sandboxes/podman.ts`](https://github.com/mattpocock/sandcastle/blob/main/src/sandboxes/podman.ts), exported as `@ai-hero/sandcastle/sandboxes/podman` in `package.json` — this is not a Docker-CLI shim, it shells out to the `podman` binary directly and uses Podman-specific flags (`--userns=keep-id:uid=N,gid=N`, `:z` SELinux labels).

**[verified]** How it suppresses prompts: it passes `--dangerously-skip-permissions` to the containerised agent. From [`src/AgentProvider.ts`](https://github.com/mattpocock/sandcastle/blob/main/src/AgentProvider.ts), the `claudeCode` provider:

```ts
const permissionFlag = options?.permissionMode
  ? ` --permission-mode ${options.permissionMode}`
  : dangerouslySkipPermissions
    ? " --dangerously-skip-permissions"
    : "";
```

**[verified]** Crucially, the [`no-sandbox` provider's own docstring](https://github.com/mattpocock/sandcastle/blob/main/src/sandboxes/no-sandbox.ts) says it explicitly declines to do this on the host:

> Skips container isolation entirely — the agent executes on the host. **Does not pass `--dangerously-skip-permissions` to the agent — the user manages permissions themselves.**

So sandcastle's prompt suppression is inseparable from its container boundary. There is no "sandcastle mode" that makes a host session quieter. **[inferred]** This is a deliberate safety design, and it matches Anthropic's own guidance (see §2) that `--dangerously-skip-permissions` should only be used inside an isolation boundary.

**[verified]** The container it builds is a Linux image. The [reference `.sandcastle/Dockerfile`](https://github.com/mattpocock/sandcastle/blob/main/.sandcastle/Dockerfile) is `FROM node:22-bookworm`, creates an `agent` user, and installs Claude Code via `curl -fsSL https://claude.ai/install.sh | bash`.

### A naming trap

**[verified]** Sandcastle's [`docs/research/permissions-systemic-fix.md`](https://github.com/mattpocock/sandcastle/blob/main/docs/research/permissions-systemic-fix.md) is titled "Permissions: Systemic Diagnosis and Proposed Fix" and is entirely about **POSIX file permissions** — host UID vs image UID, `EACCES`, SELinux labels, `could not lock config file /home/agent/.gitconfig: Permission denied`. It has nothing to do with Claude Code permission prompts. Anyone skimming the repo for "permissions" will hit this doc and misread it.

---

## 2. Claude Code's native permission and sandbox story

### The built-in Bash sandbox — unavailable on native Windows

**[verified]** [Sandboxing docs](https://code.claude.com/docs/en/sandboxing), stated twice:

> The sandbox is built into Claude Code and runs on macOS, Linux, and WSL2. **Native Windows is not supported.** On Windows, run Claude Code inside a WSL2 distribution.

and under Limitations:

> **Platform support**: supports macOS, Linux, and WSL2. WSL1 and native Windows are not supported.

**[verified]** Mechanism: Seatbelt on macOS, [bubblewrap](https://github.com/containers/bubblewrap) on Linux and WSL2, with `socat` for the network proxy. Enabled via `/sandbox` or `sandbox.enabled: true`. In **auto-allow mode** sandboxed Bash commands run with no prompt at all — this is exactly what #24 asks for, and it is the thing Windows can't have natively.

### Permission rules — available everywhere

**[verified]** [Permissions docs](https://code.claude.com/docs/en/permissions). `permissions.allow` / `deny` / `ask` arrays, evaluated **deny → ask → allow, first match wins**; specificity does not reorder. Rules **merge** across settings scopes rather than override (unlike scalar settings) — [Settings docs](https://code.claude.com/docs/en/settings).

Bash rule syntax, quoted:

> * `Bash(npm run test *)` matches Bash commands starting with `npm run test`
> * A single `*` matches any sequence of characters including spaces
> * When `*` appears at the end with a space before it (like `Bash(ls *)`), it enforces a word boundary … `Bash(ls *)` matches `ls -la` but not `lsof`. In contrast, `Bash(ls*)` without a space matches both

Two caveats that matter a lot for this repo:

> Claude Code is aware of shell operators, so a rule like `Bash(safe-cmd *)` won't give it permission to run the command `safe-cmd && other-cmd`. The recognized command separators are `&&`, `||`, `;`, `|`, `|&`, `&`, and newlines. **A rule must match each subcommand independently.**

> This wrapper list is built in and is not configurable. Development environment runners such as `direnv exec`, `devbox run`, `mise exec`, `npx`, and `docker exec` are not in the list.

**[verified]** There is also a built-in read-only set that never prompts: `ls`, `cat`, `echo`, `pwd`, `head`, `tail`, `grep`, `find`, `wc`, `which`, `diff`, `stat`, `du`, `cd`, and read-only forms of `git`. Not configurable.

**[verified]** `additionalDirectories` extends file access beyond the project root, and `acceptEdits` mode auto-accepts edits "for paths in the working directory or `additionalDirectories`".

### Permission modes

**[verified]** [Permission modes docs](https://code.claude.com/docs/en/permission-modes):

| Mode | What runs without asking |
| --- | --- |
| `default` (aka `manual`) | Reads only |
| `acceptEdits` | Reads, file edits, and common filesystem commands (`mkdir`, `touch`, `mv`, `cp`) |
| `plan` | Reads, plus classifier-approved commands when auto mode is available |
| `auto` | Everything, with background safety checks (a classifier) |
| `dontAsk` | Only pre-approved tools |
| `bypassPermissions` | Everything |

**[verified]** Auto mode is gated: it "appears when your account meets the auto mode requirements", and `defaultMode: "auto"` is **only honoured from user settings** — "Claude Code ignores `defaultMode: \"auto\"` in project and local settings." **[inferred]** So auto mode cannot be committed into this repo's `.claude/settings.json` for the user; it must be set in `~/.claude/settings.json`, and availability depends on their account.

**[verified]** Anthropic's own guidance ties bypass to isolation ([Sandbox environments](https://code.claude.com/docs/en/sandbox-environments)):

> Always run `--dangerously-skip-permissions` sessions inside a container, a VM, or the sandbox runtime.

and, for this user's exact situation:

> | Work on a native Windows host | A container or VM, or run the Bash sandbox inside WSL2 |

### PreToolUse hooks

**[verified]** [Hooks docs](https://code.claude.com/docs/en/hooks). A `PreToolUse` hook can auto-approve by exiting 0 with this on stdout:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",
    "permissionDecisionReason": "Safe command auto-approved"
  }
}
```

`permissionDecision` accepts `allow` | `deny` | `ask` | `defer` (`defer` falls through to the normal flow). Matchers are regexes on tool name, optionally narrowed by an `if` field using permission-rule syntax (`"if": "Bash(git *)"`).

### Sandbox runtime and dev containers

**[verified]** [`@anthropic-ai/sandbox-runtime`](https://github.com/anthropic-experimental/sandbox-runtime) wraps the *whole* Claude Code process in Seatbelt/bubblewrap, covering hooks and MCP servers too — but it is the same primitives, so **[inferred]** it inherits the same macOS/Linux/WSL2-only constraint. The docs call it "a beta research preview".

**[verified]** The [dev container](https://code.claude.com/docs/en/devcontainer) path is first-party: `ghcr.io/anthropics/devcontainer-features/claude-code:1.0`, plus a reference container with an iptables egress firewall. It explicitly supports running without prompts:

> Because the container runs Claude Code as a non-root user and confines command execution to the container, you can pass `--dangerously-skip-permissions` for unattended operation.

**[verified]** But the comparison table says dev containers **require Docker**, and the docs and diagrams reference Docker and the Dev Containers spec throughout. **[inferred]** Podman can back a devcontainer (the Dev Containers CLI supports alternate `dockerPath`/podman), but Anthropic's docs do not document or support that, so it is unverified territory.

**[verified]** Nowhere in Claude Code's sandboxing, sandbox-environments, or devcontainer docs is **Podman mentioned at all**. Podman is a sandcastle feature, not a Claude Code one.

---

## 3. Platform fit — Windows 11 with Podman

This is the question the ticket cares most about, and the honest answer is **"it works, and it still doesn't help."**

### Sandcastle on Windows + Podman genuinely works

Sandcastle has deliberate, tested Windows support — this is not an afterthought:

- **[verified]** [`src/sandboxes/podman.ts`](https://github.com/mattpocock/sandcastle/blob/main/src/sandboxes/podman.ts) has an explicit win32 pre-flight:
  ```ts
  // Pre-flight: check Podman Machine on macOS/Windows
  if (process.platform === "darwin" || process.platform === "win32") {
    await checkPodmanMachine();
  }
  ```
- **[verified]** [`src/mountUtils.ts`](https://github.com/mattpocock/sandcastle/blob/main/src/mountUtils.ts) has a `normalizeMounts` win32 branch that rewrites `C:\...` to `C:/...` and remaps sandbox paths under the repo dir, plus a `patchGitMountsForWindows` function.
- **[verified]** [ADR-0006 "Git worktree mounts on Windows hosts"](https://github.com/mattpocock/sandcastle/blob/main/docs/adr/0006-git-worktree-mounts-on-windows.md) documents and fixes the exact `.git`-file `gitdir:` breakage you hit when a Windows worktree is mounted into a Linux container, by mounting the parent `.git` at `/.sandcastle-parent-git` and overlaying a corrected `.git` file.
- **[verified]** There are dedicated test files: `SessionStore.windowsPath.test.ts`, `WorktreeManager.windowsPath.test.ts`, `createSandbox-windowsMounts.test.ts`, `createWorktree-windowsMounts.test.ts`, `interactive-windowsMounts.test.ts`.
- **[verified]** The `no-sandbox` provider routes host commands through `cmd.exe /d /s /c` on win32 and sets `shell: true` for `.cmd`/`.ps1` npm wrappers — i.e. someone ran this on Windows and fixed what broke.
- **[verified]** The permissions research doc's cross-platform matrix has a "Windows / WSL2 Docker" row.

Setup required, **[inferred]** from the above: `podman machine init` + `podman machine start` (Podman on Windows runs its containers inside a WSL2 VM), then `npx @ai-hero/sandcastle init`, build the image (the Podman provider does a `podman image inspect` pre-flight and fails if the image is missing), and put a `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` into `.sandcastle/.env`. Rootless with `--userns=keep-id:uid=1000,gid=1000` is the default, and **[verified]** the permissions research doc notes Podman "survived" the UID-alignment regression precisely because of that flag — so Podman is arguably the *better-supported* of the two local providers.

So: **not a dead end.** If the goal were "run an AFK agent on a ticket overnight in a container", sandcastle + Podman on this machine is a credible, supported configuration.

### Why it's still the wrong tool here

Three concrete blockers, in descending severity:

**(a) The dev loop's toolchain is a Windows venv.** CLAUDE.md pins the test command to `wiki-plugin/.venv/Scripts/python.exe -m pytest`. `.venv/Scripts/python.exe` is a **Windows PE binary**; it cannot execute inside a `node:22-bookworm` Linux container. Running the suite in a sandcastle container means maintaining a second, Linux `.venv` (`.venv/bin/python`) — and since `.venv` is gitignored and bind-mounted from the host worktree, the two would collide in the same directory unless separated. **[inferred]** This is fixable (separate venv dir, or install deps at container build time) but it forks the project's documented dev setup in two, for a repo whose CLAUDE.md deliberately pins one.

**(b) Two repos, and sandcastle manages one.** This project's sessions write to *both* `C:/Users/darre/Code/enchiridion` and the dogfooding vault at `C:/Users/darre/Code/enchiridion-vault` (a separate git repo with its own history — see CLAUDE.md and the `WIKI_ROOT` env var in `.claude/settings.local.json`). Sandcastle's own [`.out-of-scope/multi-repo-sandbox.md`](https://github.com/mattpocock/sandcastle/blob/main/.out-of-scope/multi-repo-sandbox.md) is explicit **[verified]**:

> Sandcastle does not support managing multiple independent git repos (worktrees, branches, commit extraction) within a single sandbox session. … The single-repo assumption is deeply threaded through the system … This is a future-version feature, not a current priority. Users can work around this today using the `mounts` option on docker/podman providers to bind-mount additional repos into the sandbox (**without worktree/branch/commit management for those secondary repos**).

The vault would be a dumb bind-mount with no commit extraction — which defeats the point, since the whole `/save-conversation` and `wiki-ingest` flow is *about* committing to the vault.

**(c) Bind-mount friction: WSL2 VM + Resilio Sync.** Podman on Windows bind-mounts a Windows path through a WSL2 VM into a Linux container. **[inferred]** from the ADR-0006 problem statement and general 9p/virtiofs behaviour: expect slow filesystem I/O for a pytest run that touches many small files, plus UID mapping and CRLF concerns. Layered on top, CLAUDE.md notes the repo **sits inside a Resilio Sync folder** that continuously writes `*.rsls` temp files — a live external writer racing a container-mounted worktree. **[inferred]** Not necessarily fatal, but it's a real reason not to introduce a container into this specific working tree casually.

**(d) The category error.** Even setting (a)–(c) aside: sandcastle's unit of work is `run({ agent, sandbox, promptFile })` — you write TypeScript that dispatches an agent at a prompt. Its `interactive()` gets closer (it attaches the Claude TUI to a real TTY inside the container), but you'd be starting every dev session by running `npx tsx .sandcastle/main.ts` in a container against a worktree, rather than typing `claude` in the repo. **[inferred]** That is a wholesale change of working model to fix a prompt-fatigue problem that a settings file can fix.

### What about WSL2 directly?

**[verified]** If the user moved development into a WSL2 distro, Claude Code's *native* sandbox becomes available (bubblewrap + socat, auto-allow mode, no prompts) with no sandcastle at all. **[verified]** Caveats from the docs: on Ubuntu 24.04+ you may need an AppArmor profile for `bwrap`, and "on WSL2, sandboxed commands cannot launch Windows binaries such as `cmd.exe`, `powershell.exe`, or anything under `/mnt/c/`". **[inferred]** That last one is significant: a repo living at `/mnt/c/Users/darre/Code/enchiridion` would need to move into the WSL2 filesystem, and the Windows `.venv` would be replaced by a Linux one — same problem as (a), plus relocating a Resilio-synced folder. This is the strongest *sandbox* option available but it is a working-environment migration, not a config change. Worth flagging as a human decision, not something to do as part of #24.

---

## 4. What actually prompts during development here

Enumerated by reading the repo. The plugin's own scripts are the main surface, and there's a useful structural fact: **the scripts funnel git through Python.**

**[verified]** `wiki-plugin/scripts/commit.py` and `init_wiki.py` invoke git via `subprocess.run(["git", "-C", str(root), *args])` — not via the Bash tool. So a session running `python .../commit.py` produces **one** Bash prompt for the python invocation; the `git add`/`git commit` inside it never reach Claude Code's permission system. **[inferred]** That makes a single well-shaped allow rule for the scripts directory disproportionately effective.

Concrete dev-time prompt sources:

| Source | Example call | Prompted? |
| --- | --- | --- |
| Test suite | `.venv/Scripts/python.exe -m pytest` | Yes — Bash |
| Scripts by hand | `python "${CLAUDE_PLUGIN_ROOT}/scripts/build_index.py"` etc. (`place.py`, `normalize_raw.py`, `frontmatter.py`, `commit.py`, `build_index.py`, `init_wiki.py`, `session_state.py`, `save-session-to-vault.py`) | Yes — Bash |
| Git writes | `git add <paths>`, `git commit -m ...`, `git checkout -b`, `git merge` | Yes (read-only git forms are built-in-allowed) |
| GitHub CLI | `gh issue ...`, `gh api ...`, `gh label list`, `gh release ...` | Yes |
| Vault writes | Write/Edit under `C:/Users/darre/Code/enchiridion-vault` | Yes — outside the primary working directory |
| Venv/deps | `python -m venv .venv`, `pip install ...` | Yes |

**[verified]** The current `.claude/settings.local.json` already carries 21 allow rules accreted from "yes, don't ask again". Several are too literal to be much use:

- `"Bash(.venv/Scripts/python.exe -m pytest -q)"` — an **exact-match** rule. `... -m pytest tests/test_md.py` or `... -m pytest -x` prompts again. This is almost certainly the single biggest source of repeat prompts.
- `"Bash(.venv/Scripts/python.exe -m pip install -q ruamel.yaml markdown-it-py pytest hypothesis pyright)"` — exact-match on a one-off setup command; near-worthless as a standing rule.
- The `Read(//c/Users/darre/Code/enchiridion-vault/**)` rule grants reads of the vault but there is **no** `additionalDirectories` entry, so *writes* there still prompt and `acceptEdits` wouldn't cover it.
- Two rules pin absolute paths (`"C:/Users/darre/Code/enchiridion/wiki-plugin/scripts/*`), which won't match `${CLAUDE_PLUGIN_ROOT}`-expanded or relative invocations.

**[verified]** Also noted in passing, unrelated to #24 but worth a ticket: the dogfooding vault's `.claude/settings.json` points its marketplace at `c:\Users\darre\Resilio Sync\Code\enchiridion\wiki-plugin`, while this repo's `settings.local.json` uses `C:/Users/darre/Code/enchiridion/wiki-plugin`. One of those paths is stale.

---

## 5. Recommendation

**Rule out sandcastle for #24.** Close it with the native config below. Sandcastle stays on the table for a genuinely different future ticket ("run an AFK agent on a ticket in a container") — at which point Podman + `interactive()` is the configuration to try, and multi-repo remains the blocker to check first.

### Concrete config

Replace the accreted rules in `.claude/settings.local.json` — or better, promote the durable ones to a checked-in `.claude/settings.json` (permission rules merge across scopes, so both apply):

```json
{
  "permissions": {
    "allow": [
      "Bash(.venv/Scripts/python.exe -m pytest*)",
      "Bash(wiki-plugin/.venv/Scripts/python.exe -m pytest*)",
      "Bash(python wiki-plugin/scripts/*)",
      "Bash(python \"${CLAUDE_PLUGIN_ROOT}/scripts/*)",
      "Bash(git add *)",
      "Bash(git commit -m *)",
      "Bash(git checkout -b *)",
      "Bash(git switch *)",
      "Bash(git branch *)",
      "Bash(gh issue *)",
      "Bash(gh label *)",
      "Bash(gh api *)",
      "Bash(gh pr view *)",
      "Bash(gh release *)"
    ],
    "deny": [
      "Bash(git push --force*)",
      "Bash(git reset --hard*)",
      "Bash(git add -A*)",
      "Bash(git add .)"
    ],
    "additionalDirectories": [
      "C:/Users/darre/Code/enchiridion-vault"
    ]
  }
}
```

Notes on the specific choices:

- `Bash(.venv/Scripts/python.exe -m pytest*)` — the trailing `*` **without** a preceding space is deliberate. **[verified]** from the permissions docs: with a space it enforces a word boundary; without, it matches suffixes freely, so this covers `-q`, `-x`, `tests/test_md.py`, `-k foo`. Replaces the current exact-match rule.
- The `git add -A` / `git add .` deny rules directly encode CLAUDE.md's standing rule about never staging `*.rsls` files or `.claude/worktrees/`. **[verified]** deny beats allow regardless of specificity, so these hold even though `Bash(git add *)` is allowed. This is a genuine safety improvement over today's config, not just noise reduction.
- `additionalDirectories` is what stops vault writes prompting, and is what makes `acceptEdits` mode cover the vault. **[verified]** per the settings and permission-modes docs.
- **[verified]** No rule can cover compound commands: `pytest && git add` needs each subcommand matched independently. Prefer separate tool calls over `&&` chains in this repo.
- **[verified]** No rule can cover `npx`, `docker exec`, or `find -exec`/`-delete` — those are excluded from wrapper-stripping by design.

### Then, in order of escalation

1. **Ship the rules above.** Cheap, zero platform risk, immediately fixes the pytest churn. Do this first and measure whether it's enough.
2. **If still noisy: `"defaultMode": "acceptEdits"`** in project settings. Covers edits and `mkdir`/`touch`/`mv`/`cp` across the working dir and `additionalDirectories`.
3. **If still noisy: auto mode.** **[verified]** Must go in `~/.claude/settings.json` (ignored from project/local settings) and depends on account eligibility. A classifier replaces the prompt. **[verified]** Anthropic states auto mode "is not required [to have an isolation boundary] the way it is for `--dangerously-skip-permissions`" — so it's usable on a bare Windows host, unlike bypass.
4. **A `PreToolUse` hook** only if rules prove structurally insufficient — e.g. to allow any `python <plugin>/scripts/*.py` regardless of how the path was spelled, which prefix rules handle badly across `${CLAUDE_PLUGIN_ROOT}` / absolute / relative forms. The plugin already ships `hooks/hooks.json` with a `SessionStart` hook, so the wiring pattern exists. **[inferred]** Hold this in reserve; it's more machinery than the problem currently warrants.

### Needs a human decision

- **Does the user want to move development into WSL2?** That is the only way to get Claude Code's *real* OS-enforced sandbox with auto-allow on this machine. It costs: relocating the repo out of `/mnt/c` (and thus out of the Resilio Sync folder), rebuilding `.venv` as Linux, and abandoning the PowerShell-primary setup. Genuinely a big call, and out of scope for #24 as written.
- **Is auto mode available on this account?** Determines whether step 3 above is reachable.
- **Which rules get checked in vs. stay local?** The deny rules and the script/pytest allows are project-wide truths and arguably belong in `.claude/settings.json`; the absolute-path vault entry is machine-specific and belongs in `settings.local.json`.

---

## Appendix: sources

Primary sources read for this document.

**Sandcastle** (all `github.com/mattpocock/sandcastle`, branch `main`):
[README.md](https://github.com/mattpocock/sandcastle/blob/main/README.md) ·
[package.json](https://github.com/mattpocock/sandcastle/blob/main/package.json) ·
[src/AgentProvider.ts](https://github.com/mattpocock/sandcastle/blob/main/src/AgentProvider.ts) ·
[src/sandboxes/podman.ts](https://github.com/mattpocock/sandcastle/blob/main/src/sandboxes/podman.ts) ·
[src/sandboxes/no-sandbox.ts](https://github.com/mattpocock/sandcastle/blob/main/src/sandboxes/no-sandbox.ts) ·
[src/mountUtils.ts](https://github.com/mattpocock/sandcastle/blob/main/src/mountUtils.ts) ·
[src/interactive.ts](https://github.com/mattpocock/sandcastle/blob/main/src/interactive.ts) ·
[.sandcastle/Dockerfile](https://github.com/mattpocock/sandcastle/blob/main/.sandcastle/Dockerfile) ·
[docs/adr/0006-git-worktree-mounts-on-windows.md](https://github.com/mattpocock/sandcastle/blob/main/docs/adr/0006-git-worktree-mounts-on-windows.md) ·
[docs/research/permissions-systemic-fix.md](https://github.com/mattpocock/sandcastle/blob/main/docs/research/permissions-systemic-fix.md) ·
[.out-of-scope/multi-repo-sandbox.md](https://github.com/mattpocock/sandcastle/blob/main/.out-of-scope/multi-repo-sandbox.md)

**Claude Code official docs**:
[Sandboxing](https://code.claude.com/docs/en/sandboxing) ·
[Sandbox environments](https://code.claude.com/docs/en/sandbox-environments) ·
[Permissions](https://code.claude.com/docs/en/permissions) ·
[Permission modes](https://code.claude.com/docs/en/permission-modes) ·
[Settings](https://code.claude.com/docs/en/settings) ·
[Hooks](https://code.claude.com/docs/en/hooks) ·
[Dev containers](https://code.claude.com/docs/en/devcontainer) ·
[anthropic-experimental/sandbox-runtime](https://github.com/anthropic-experimental/sandbox-runtime)

**This repo**: `CLAUDE.md`, `.claude/settings.local.json`, `wiki-plugin/hooks/hooks.json`, `wiki-plugin/scripts/commit.py`, `wiki-plugin/scripts/init_wiki.py`, `wiki-plugin/skills/*/SKILL.md`, and the vault's `.claude/settings.json`.

No secondary sources (blog posts, summaries) were used.
