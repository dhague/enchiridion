# The script layer is a bundled TypeScript bundle executed by an already-installed interpreter (Node/Bun)

The script layer ships as a single bundled TypeScript file executed by an interpreter that is already installed on the target hosts: **Node** (Claude Code, Joule Desktop) and **Bun** (OpenCode). esbuild produces one bundled `.js` plus the SQLite `.wasm` asset, shipped **inside the plugin** and — for Joule Desktop — as **skill supporting files under a `scripts/` subdirectory** (confirmed: Joule Desktop grants sandbox read+execute on installed skill directories at runtime, and its `skill-creator` documents the `scripts/` convention). There is **no runtime `npm install`, no registry, no network, no lazy fetch, and no per-platform binaries**. `wiki-plugin/bin/enchiridion` is a thin `exec node <bundled.js> "$@"` shim (preferring `node` on `PATH`; the `ENCHIRIDION_BIN` dev override is retained). CI is a single esbuild bundle step.

## Why an installed interpreter instead of a bundled executable

The plugin targets hosts that already run an agent harness. Shipping the script layer as code that interpreter runs means there is **no own-built executable to build, sign, and distribute per platform** — the runtime payload is code, not a binary the OS gates. This also means no install step beyond the plugin directory: the interpreter is the one already-installed runtime the plugin depends on. The cost is a stated prerequisite (Node, or Bun for OpenCode) instead of a self-contained binary; that is accepted because every target host already has it.

## Hard constraint — pure JS + WASM only, no native addons

A native addon would (a) reintroduce a platform binary that has to be built per target and gates at launch, and (b) break Bun, OpenCode's runtime. This is why `node-sqlite3-wasm` is mandatory over `better-sqlite3`, and why any optional native path (e.g. `chokidar`'s `fsevents`) must degrade gracefully to pure JS.

## Toolchain

- **Language:** TypeScript, bundled by **esbuild** into a single `.js` plus the SQLite `.wasm` asset.
- **SQLite:** **`node-sqlite3-wasm`** — pure-WASM, FTS5 compiled in, real on-disk persistence, synchronous API. Rejected `@sqlite.org/sqlite-wasm` (Node build is in-memory only) and `sql.js` (FTS3 only, no FTS5).
- **Git:** **`isomorphic-git`** (pure JS) — `readBlob`/`resolveRef({ref:'HEAD'})`/`walk({trees:[TREE({ref:'HEAD'})]})` cover [ADR-0015](0015-search-index-view-of-committed-history.md)'s read-content-from-HEAD-blobs requirement.
- **YAML:** **`eemeli/yaml`** — preserves key order and edits single scalars through the Document/AST model ([ADR-0012](0012-frontmatter-round-trip-relaxed.md)).
- **CLI / Markdown / property tests:** `commander`, `markdown-it`, `fast-check` — all pure JS.

## Gate 0 result

**Verified 2026-08-19 on branch `spike/247-node-sqlite3-wasm-fts5` (spikes #247 and #248).**

The go/no-go spike ran the exact FTS5 + bm25 surface the TypeScript `searchindex` module uses — production-identical schema (`SchemaVersion "4"`, `porter unicode61`, bm25 weights `0.0,10.0,5.0,1.0`) in an in-memory database — on both runtimes. All seven assertions passed on both runtimes.

| Runtime | Version | Result |
|---------|---------|--------|
| Node.js | v22.22.2 | All 7 PASS |
| Bun | 1.3.14 (Node.js compat v24.3.0) | All 7 PASS |

Assertions covered: WASM module load, FTS5 virtual table creation, porter unicode61 tokenizer registration, insert, keyword MATCH, bm25 column weights (distinct non-zero scores), and phrase-quoted query matching. Bun's Node.js compat layer handles `node-sqlite3-wasm`'s WASM-based approach without issue. No `bun:sqlite` fallback adapter is needed. The dual-engine requirement from the Consequences section is confirmed satisfiable via `node-sqlite3-wasm` alone.

## Consequences

- **Node is a stated prerequisite** (and Bun for OpenCode). Coding-harness users already have it; hosts that lack it must install it — the honest price of shipping code rather than a binary.
- **Dual-engine.** The bundle must run on Node (Claude Code, Joule Desktop) *and* Bun (OpenCode). CI runs the suite on both.
- **Code signing is moot for the runtime artifact** — there is no own-built executable to sign; the runtime payload is code the interpreter runs.
- **[ADR-0012](0012-frontmatter-round-trip-relaxed.md) carries** — frontmatter round-trip stays non-byte-identical with key order preserved, realised through `eemeli/yaml` and property-tested via `fast-check`. The move-touches-only-link-lines contract is likewise property-tested.
- **[ADR-0015](0015-search-index-view-of-committed-history.md) is realised with `isomorphic-git`**: the index stays a materialised view of `HEAD`'s `wiki/` tree read from git blobs, and the FTS5 schema (`SchemaVersion` `"4"`, `porter unicode61`, bm25 weights `0.0,10.0,5.0,1.0`) is stable.