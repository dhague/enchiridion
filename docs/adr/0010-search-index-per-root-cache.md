# 10. Shared `SearchIndex` connection lives behind a per-root cache, not a caller-side memo

Grilled 2026-08-07, from [#127](https://github.com/dhague/enchiridion/issues/127).

## Context

`SearchIndex`'s own docstring already states the intended contract: "One
`SearchIndex` per vault, lifetime of the process." Every write commits
immediately (no WAL — Resilio + sqlite sidecars corrupts, see ADR-0006), so
two live connections against the same `index.db` racing an uncommitted
transaction surface as `database is locked`.

That contract was enforced in exactly one place: `discover.py`'s `_vault_for`,
an `lru_cache`-memoized `Vault` per root, justified by a docstring explaining
that `check()` is called once per planned page in a multi-page `IngestPlan`,
so a fresh `Vault` per call would open a new connection per page. Every other
caller (`search.py`, `ingest.py`, `vault.py`'s CLI) constructs one `Vault` per
process and never hits the problem — not because the contract doesn't apply
to them, but because they happen to only ever call it once.

A caller maintaining its own memo table to stop a collaborator from opening a
second connection is the collaborator's lifetime constraint leaking across
the seam: undiscoverable from `Vault.__init__` or `SearchIndex.__init__`,
correct only by accident of how many times each caller happens to invoke it.

## Decision

The per-root cache moves into `search_index.py` itself, as a module-level
factory `for_root(root) -> SearchIndex` alongside the existing (untouched)
`SearchIndex.__init__`:

- `SearchIndex.__init__` stays the raw, always-fresh constructor. Every
  existing test that constructs `SearchIndex(...)` directly — including the
  `git=fake` injection path and the schema-migration tests that pre-seed a
  stale `index.db` before constructing — keeps its current behavior
  unchanged.
- `for_root(root)` is the only cached entrypoint: process-lifetime, keyed by
  resolved root path, no eviction. `Vault._get_index()` calls it instead of
  constructing `SearchIndex` directly.
- `for_root()` takes **root only** — no `git` parameter — so the test-fake
  injection path can never silently collide with a cached production
  instance; anything needing a fake `VaultGit` continues to call
  `SearchIndex(root, git=fake)` directly, uncached, as today.
- `discover.py`'s `_vault_for`/`lru_cache` and its justifying docstring are
  deleted outright. `check()` goes back to a plain `Vault(vault_root)` per
  call — cheap again, because the shared connection now lives one layer
  down.

Rejected alternative: document the constraint at `Vault.__init__` and treat
caller-side memoization (`discover.py`'s pattern) as sanctioned practice. That
closes the gap for exactly one caller and blesses the smell the issue was
raising rather than removing it — any future caller that calls `check()` (or
constructs `Vault` more than once per process) would have to independently
rediscover and re-solve the same problem.

No eviction/close hook: every current entrypoint is a one-shot CLI process —
[ADR-0001](0001-no-mcp-server.md) rules out a long-running server — so an
unbounded per-root cache is bounded in practice to a handful of roots for the
life of one invocation.

## The current implementation (2026-08-14, from [#174](https://github.com/dhague/enchiridion/issues/174))

The `searchindex` module satisfies this decision by the opposite
mechanism, and deliberately has **no `ForRoot`**. `Open` pairs with `Close`,
so a connection's lifetime is explicit rather than process-long — which
removes the thing the cache existed to work around (a `SearchIndex` that
could not be closed, leaving a caller-side memo as the only way to stop a
second connection).

What survives is the argument, not the implementation: the constraint must
not be left to a caller's accidental call count. So the command owns the one
handle and passes it down; packages beneath it take a `Searcher`, never a
root, and therefore *cannot* open a competing connection. `discover.Check`
and `discover.Discover` take that searcher for exactly this reason — before
#174 they each opened their own index off a root, which made `enchiridion
discover --plan` open two per run, correct only because the first `Close` ran
before the second `Open`.

## Consequences

- `SearchIndex`'s "one per vault, lifetime of process" docstring becomes an
  enforced property of the module rather than an unenforced comment.
- `Vault` stays cheap to construct everywhere, including repeatedly in a
  single process — the property `discover.py` needed but had to fake with
  its own memo.
- Not addressed here: the `SearchQuery` value-type proposal from the same
  issue. Rejected as out of scope for now — only one real caller
  (`search.py`) exercises the full parameter set. The dead `fields` parameter
  cleanup is tracked as a separate, unrelated follow-up.
