# The TypeScript Port of the Script Layer

A record of the TypeScript rewrite of the enchiridion script layer — what it
replaced, how it was sequenced, and where the pieces landed. This is a
*summary*: the decisions and rationale live in the ADRs and issues it points
at, and the code at `enchiridion-ts/` is authoritative over any diagram here.

## Why

The original script layer was a single static Go binary ([ADR-0011](adr/0011-go-rewrite-scope-sequencing-toolchain.md)) at `enchiridion-go/`, lazy-fetched by a dependency-free bootstrap ([ADR-0013](adr/0013-go-binary-lazy-fetch-dependency-free-bootstrap.md)). It tripped Windows Defender ASR rule `01443614` on corporate-managed machines: an unsigned, near-zero-prevalence own-built `.exe` is blocked at launch, and signing does not reliably clear it. The fix was a full, all-or-nothing port to TypeScript ([ADR-0017](adr/0017-typescript-rewrite-approved-interpreter-asr.md), issue #244/#252): bundle the whole script layer with esbuild into a single `.js` plus the SQLite `.wasm` sidecar, and run it on an already-installed, already-trusted interpreter — Node (Claude Code, Joule Desktop) and Bun (OpenCode). A locally-built Go `.exe` trips the same ASR rule, so there was no partial-coverage story: the Go source was retired wholesale once the port reached parity.

## Outcome

- `enchiridion-go/` is **deleted** (#267). There is one script-layer implementation: the TypeScript bundle at `enchiridion-ts/dist/cli.cjs` + the `node-sqlite3-wasm` `.wasm` sidecar.
- `wiki-plugin/bin/enchiridion` is a thin POSIX-sh shim that execs `node` against the bundle, forwarding every argument; `ENCHIRIDION_BIN` overrides it for local dev (#254).
- All **15 subcommands** are implemented and exercised on both Node and Bun: `search`, `init`, `ingest`, `discover`, `ingest-scan`, `watch`, `save-session`, `tool-call-stats`, `commit`, `superseded-by`, `read-page`, `vault`, `page`, `place`, `hook`.
- No native addons anywhere: `node-sqlite3-wasm` (WASM, not `.node`), `chokidar` in pure-JS mode (no `fsevents`), `isomorphic-git` (JS). A native addon would re-trip the ASR rule the port exists to clear.

## Sequencing

The port followed the dependency graph, purest packages first, as a set of
child tickets off the port spec (#252). Each landed as its own worktree +
pull request, merged in order. The tickets, in sequence:

| Ticket | Module(s) ported | Subcommand wired |
|--------|------------------|------------------|
| #255 | `wikipage` (pure page model, link machinery, property tests) | `page get\|set\|merge` |
| #256 | `vaultgit` (all git access via `isomorphic-git`, strict/lenient surface) | — (module only) |
| #257 | `sessionstate` + `transcriptcapture` + `toolcallstats` | `save-session`, `tool-call-stats` |
| #258 | `place` + `pagerecord` (path computation, frontmatter schema) | `place` |
| #259 | `vault` (I/O layer, `ResolveRoot`) | `vault` (bare/root/move) |
| #260 | `chainofevidence` + `commit` | `commit` |
| #261 | `supersededby` + `ingestscan`/`ingestignore` | `superseded-by`, `ingest-scan` |
| #262 | `initwiki` + `hooks` | `init`, `hook` |
| #263 | `ingest` (the IngestPlan executor) | `ingest` |
| #264 | `discover` (overlap classification) | `discover` |
| #265 | `watch` (file watcher, pure-JS chokidar) | `watch` |
| #266 | full-CLI smoke suite against the bundle on Node + Bun; wired `search` | `search` |
| #267 | retired `enchiridion-go/`, stripped Go from CI, updated docs | — |

## What each piece became

The modules under `enchiridion-ts/src/`, in dependency order (the `vault ->
wikipage` one-way dependency carried across the port):

- **`wikipage.ts`** — the pure page model: `Page` get/set/merge/retarget, the
  link machinery (`iterLinks`, `percentEncode`/`percentDecode`, `splitDest`,
  `resolveLinkDest`), `splitFrontmatter`, `planMove`, `composeLink`,
  `normalizeBodyLinks`. Frontmatter uses `eemeli/yaml`'s Document/AST model so
  key order is preserved; code-block exclusion uses `markdown-it`. The two
  fast-check property tests re-establish the ADR-0011/0012 contracts: a move
  touches only link lines and all links still resolve; a no-op `set` is not
  byte-identical but preserves key order and differs only in scalar quote
  style.
- **`place.ts`** — kebab-slug + kind-folder path computation. `KindFolders`
  is the single source of truth for the kind→folder mapping (ADR-0008);
  `slugify` drops apostrophes, truncates at a hyphen boundary; `path`
  resolves canonical + custom kinds.
- **`pagerecord.ts`** — the one reader of the frontmatter schema: derives
  `kind` from folder, decodes all typed edges (resolving them vault-relative),
  derives `superseded_by` by inverting `supersedes` edges.
- **`vault.ts`** — the `Vault` I/O type plus `resolveRoot` (ADR-0004 order:
  `WIKI_ROOT` → `.wiki-root` marker → cwd). `Vault` has no search-index
  facade (that would be an import cycle). `vault move` fixes every inbound
  and outbound link across all pages via `planMove`, touching only link lines.
- **`vaultgit.ts`** — all git access via `isomorphic-git`, replicating Go's
  strict/lenient split. Strict: `init`/`add`/`commit` reject on failure.
  Lenient: `isWorkTree`/`committedPages`/`lastCommitDate`/`porcelainMentions`
  return documented defaults, never throw. `committedPages(since)` reads
  content from HEAD's blobs, with the three paths (HEAD==watermark no-op,
  range walk, full rebuild on unreachable watermark) and the merge-commit
  path-enumeration/date-exclusion rule.
- **`chainofevidence.ts`** — the page→stub→raw-file rule, run by `ingest` as
  pre-flight *and* by `commit` as the hard gate, so a hand-built manifest
  can't route around it into history.
- **`commit.ts`** — structured git commit per manifest (ADR-0003 attribution
  from content, not git identity), committer identity from git config with an
  OS-user@hostname fallback.
- **`ingest.ts`** — the IngestPlan executor: `resolve` (pure apart from vault
  reads; placement, order-preserved frontmatter projection, edge/raw_source
  link composition) → `validate` (shape then semantic, entirely before any
  write) → `execute` (write pages, idempotent, no rollback) → structured
  commit. `action: "synthesize"` runs through the same pipeline.
- **`searchindex.ts`** — SQLite FTS5 (`node-sqlite3-wasm`), a materialised
  view of HEAD's `wiki/` tree (ADR-0015): content from git blobs, never disk.
  Schema `"4"`, `porter unicode61`, bm25 `0.0,10.0,5.0,1.0`.
- **`discover.ts`** — overlap classification over the index. The query is an
  **OR** of the candidate's words with `raw: true` (an AND query silently
  zeros real duplicates); thresholds are parameters (duplicate ≥15 + shared
  title token, refines ≥15 no shared, related 5–15, distinct <5).
- **`supersededby.ts`** — walks each seed's `supersedes` chain to its head.
- **`ingestscan.ts`** + **`ingestignore.ts`** — the raw/ sweep and its
  per-folder `.ingestignore` policy (fnmatch, no ancestor walk). Eligibility
  fails toward offering: no git history or undetermined → eligible.
- **`watch.ts`** — the raw/ file watcher. `chokidar` pure-JS only (no
  `fsevents`, so no ASR re-trip), per-file debounce, a lock file preventing
  two concurrent watchers (stale-lock recovery), and a queue file.
- **`sessionstate.ts`** + **`transcriptcapture.ts`** — host detection
  (Claude Code vs OpenCode; OpenCode wins only on tracker evidence when both
  vars are set), transcript fetch, and the shared render seam.
- **`toolcallstats.ts`** — reads the hook-written log, prints the per-tool
  cost summary (the "prompts" figure is a proxy, not a turn count — #99).
- **`initwiki.ts`** — scaffolds a new vault: folder tree, git repo, gitignore,
  optional plugin-registration settings.
- **`hooks.ts`** — the `session-start` and `post-tool-use` handlers, read
  from stdin, failing open (every error swallowed, always exit 0).
- **`cli.ts`** — commander entry point, one subcommand per capability.

## Cross-cutting decisions that survived the port

- **No search-index facade on `Vault`.** `searchindex` depends only on
  `vaultgit` and counts the `wiki/**.md` tree itself; a facade would be an
  import cycle. Consumers that need to search open an `Index` directly.
- **No `ForRoot` cache.** `Open`/`Close` make the connection lifetime
  explicit (one connection at a time); everything below the CLI takes a
  `Searcher`, never a root (ADR-0010's *Go port* section).
- **Encoding at one decode boundary.** Link destinations are percent-encoded
  (minimal charset `%`, space, `()`, `#`, `<>`; unicode literal); decoding
  lives in `wikipage`'s link machinery, so destination-vs-path comparison
  stays correct by construction.
- **Property-tested contracts.** The move-contract and ADR-0012 frontmatter
  round-trip properties are machine-checked on every CI run (fast-check,
  analogues of Go's `rapid` suite).
- **Release.** The Go release pipeline (GoReleaser → six binaries, Homebrew
  tap, Chocolatey, GitHub Release) is retired with the Go source. `tag-release.yml`
  still derives the plugin version tag, but a pushed tag currently triggers
  nothing; a TS-bundle release workflow (how a distributable plugin ships the
  esbuild bundle + wasm sidecar) is a separate ticket.

## Testing

- **Module tests** cover each module; the two fast-check property tests guard
  the page-move and frontmatter contracts.
- **CLI smoke tests** (`src/cli.smoke.test.ts`) run one test per subcommand
  against the **esbuild-bundled `dist/cli.cjs`** on both Node and Bun — the
  bundled `.cjs` + `.wasm` are the artifacts under test, no ts-node, no source
  maps.
- **CI** (`ts-enchiridion.yml`) runs typecheck, lint, format, build, and the
  test suite on both Node.js LTS and Bun; both must pass for a PR to merge.
- The `bin/enchiridion` shim behaviour is covered by
  `wiki-plugin/tests/bin_enchiridion.bats`.
- At the end of the port, **398 tests** pass on both runtimes.

## Commands

From `enchiridion-ts/`:

```
npm ci
npm run typecheck
npm run lint
npm run format:check
npm run build        # esbuild bundle -> dist/cli.cjs + the .wasm sidecar
npm test             # Node's built-in test runner via tsx
npm run test:bun     # needs bun on PATH; same tests under Bun
```

`wiki-plugin/bin/enchiridion` invokes the bundle: `node dist/cli.cjs <subcommand>` (or via `ENCHIRIDION_BIN` for local dev).
