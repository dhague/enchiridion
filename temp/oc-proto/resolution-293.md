## Resolution

**OpenCode runs `enchiridion` in-process via a plugin custom tool on OpenCode's embedded Bun. The committed bundle + `.wasm` needs NO copy into the vault layout, and NO separate OpenCode `cli.js` — the one bundle gains an import-safe `run()` entry and serves both execution shapes.**

Prototyped (throwaway, `temp/oc-proto/`): a plugin-style harness imports the bundle and calls `run(argv)` → `{stdout, stderr, exitCode}` with real FTS5 hits returned in-process (`.wasm` loads in-process), the host process's stdout and exit code untouched.

Decisions:

- **Q7 execution shape = B (plugin-native).** A plugin custom tool (`wiki`) imports the bundle and calls an exported `run()` — the `execute` function runs inside OpenCode's embedded Bun, so no `node`/`bun` on PATH is needed at all. This is the honest fulfillment of "Bun for OpenCode" (ADR-0017): the bundle runs on the interpreter OpenCode actually ships.
- **Backward compatibility is preserved by the restructure itself.** The committed bundle today is NOT import-safe: its top-level `main()` runs on import and commander's `help()` calls `process.exit(0)` — it killed a test host mid-import. The fix: guard `main()` behind `require.main === module`, export `run(argv)` that captures stdout/stderr, overrides commander's exit (`exitOverride()`), and resets `process.exitCode`. The same bundle then works as a CLI for Claude Code / Joule Desktop (`node cli.cjs` unchanged; bats + both-runtime CI stay green) AND as an in-process library for OpenCode. No OpenCode-specific `cli.js` variant — one artifact, one freshness guard.
- **No artifact copy into the vault.** The marker's `plugin_root` already resolves to the clone that carries the committed bundle; the plugin reads the marker and imports `scripts/cli.cjs`. `install-opencode.py` gains only: copy the `wiki` tool plugin + allow it (and `skill`) in generated agents.
- **Gap #1 (skill permission) confirmed and in scope.** Generated agents emit `permission: {'*': deny}` with no `skill: allow`, yet their bodies say "preloaded above" while the generator drops CC's `skills:` preload. `generate-opencode.py` must translate CC `skills:` → `skill: allow` and add `wiki: allow`, and the "preloaded above" wording must go. This is what makes the OpenCode port actually functional for ingest.
- **Fog graduated.** "Cross-runtime artifact layout: does one bundle serve both Node and Bun, or does Bun need a variant?" — answered: one bundle, no variant. CI already proves Bun runs the bundle; the restructure adds import-safety on top.
- **Not in scope (per user):** Python→TypeScript migration of the OpenCode install scripts is a separate deferred decision; `#218` (npm-based install without cloning) remains a separate delivery-mechanism ticket.

Follow-on build tickets created as children of the map: import-safe `run()` entry (#296), the `wiki` tool plugin + install wiring (#297), generated-agent permissions + skill wording (#298), and CI wiring (#299).