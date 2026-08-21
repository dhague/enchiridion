# The search index is a view of committed history, not of the working tree

A page is part of the vault once it is **committed**. The search index reflects exactly that: `.wiki-knowledge/index.db` is a materialised view of `HEAD`'s `wiki/` tree, built from git blobs, and a file sitting uncommitted on disk is a draft that search does not see. Freshness is decided by a single SHA comparison — the index stores the `HEAD` it has already accounted for in its `meta` table, and when that matches the repository's current `HEAD` there is nothing to do.

This replaces the freshness mechanism [ADR-0006](0006-stdlib-fts5-not-embeddings.md) specified: an unconditional `(mtime_ns, size)` staleness scan over every page on every search. That scan treated the working tree as the source of truth, which made it correct under a *different* definition of the vault than the one this project actually holds. `CONTEXT.md` has always defined a vault as "the git repository the plugin operates on" — the mtime scan was quietly modelling it as a directory that happens to be under version control. The two models disagree only about uncommitted files, but that disagreement is the whole of this decision.

Reading **blobs rather than files** is what makes the model true by construction rather than by convention. A dirty working tree is not ignored by policy; it is structurally invisible, because the index never looks at the filesystem to decide what a page says. This also closes the failure mode that a naive SHA watermark over filesystem reads would open: under a file-sync tool (Resilio, OneDrive), `.git` and the page files arrive independently, so reading `HEAD`'s changed paths off disk can capture pre-sync bytes and then advance the watermark past them, leaving a page permanently wrong. Blob reads cannot observe that skew. A secondary concern here, but a free one to eliminate.

`git status` is never run. Nothing in the search path needs to know whether the working tree is dirty, which is the simplification that makes the whole design pay: when `HEAD` is unchanged the scan is one ref read and zero filesystem work — better than the previous idle cost, which grew with page count (~53 ms at 2000 pages), and better than the previous per-search full-history walk, which grew with commit count without bound.

## Mechanism

`meta.git_head` holds the last accounted-for commit. On every search:

- **`HEAD` == watermark** — nothing to do. The common case, and now free.
- **watermark reachable from `HEAD`** — walk the range, enumerate changed `wiki/**.md` paths, re-upsert each from **`HEAD`'s** blob (not the intermediate commit's, so a path touched three times is read once and always holds the current committed state), remove paths deleted in the range.
- **watermark unreachable, or absent** — amend, rebase, `reset --hard`, a re-clone over an existing `index.db`: there is no enumerable delta, so drop and rebuild from `HEAD`'s tree. Reachability is not a separate query — the range walk stops when it finds the watermark, and reaching the root commit without finding it *is* the negative answer.
- **no `HEAD` at all** — a repo with no commits indexes as empty, silently, retried next search. Consistent with the rule: nothing committed, nothing indexed.

The watermark advances after any successful scan, including when the intervening commits touched no pages. It means "the `HEAD` I have already accounted for," not "the `HEAD` that last changed something" — otherwise a vault whose commits mostly touch `raw/` would re-walk a growing range on every search, which is the cost this exists to remove.

Merge commits contribute to *path enumeration* (diffed against first parent) but not to *date attribution*, which stays non-merge-only as `CommitDates()` has always done. Path enumeration must include them because a path touched only by a conflict resolution would otherwise never be enumerated — under the mtime scan that self-healed, and here it would not. Excluding merges from dates keeps `git_date` semantics byte-identical to before, so no page's reported age shifts as a side effect.

`mtime_ns` and `size` are dropped from the `page` schema, and `SchemaVersion` goes to `"4"`. The bump is load-bearing beyond the column change: every existing `index.db` was populated under working-tree semantics and may hold uncommitted content that this decision says was never in the vault. The version bump forces one clean rebuild under the new rules with no user action, exactly as the `2 → 3` bump did for a semantics change with an unchanged shape.

## Consequences

**A page you have written but not committed is not searchable, and nothing errors.** This is the cost of the decision, paid deliberately. It is made legible in one place: `search --status` reports `git_head` alongside a count of `wiki/**.md` files on disk exceeding the indexed page count — *"N page(s) on disk not yet committed — not searchable."* That is one directory walk on an explicit diagnostic path, never on the search hot path, and `git_head` is what turns "why isn't my page showing up" from unanswerable into observable.

**`raw/` keeps the opposite policy, and that is not an inconsistency.** `ingestscan` sweeps the inbox with `PorcelainMentions`/`LastCommitDate` under a documented *"fail toward offering"* stance — an uncommitted raw file is precisely what that sweep exists to catch. `wiki/` is a log and `raw/` is an inbox; the boundary between them is exactly where the two policies meet.

**The `Git` seam grows a payload but keeps its shape.** One method — `CommittedPages(since string) (Snapshot, error)` — returns the new `HEAD`, whether it fell back to a full tree read, and the per-page `{page_ref, date, content, deleted}` list. Reachability lives inside `vaultgit`, the only package that can answer it, rather than leaking into `searchindex`. `since == ""` means "all of `HEAD`'s tree," so first-build and full-rebuild are the same call. A whole-vault snapshot is single-digit MB at 2000 pages.

**`searchindex`'s centre of gravity moves from `vault` to `vaultgit`.** The `vault` import survives only for `Status()`'s on-disk count. `docs/architecture.md`'s package graph is stale until redrawn.

**`--reindex` is re-pointed, not removed.** Bare `--reindex` forces the range walk to `HEAD`; `--reindex --full` drops and rebuilds from `HEAD`'s tree. `Stats.Updated` now counts pages re-read because a commit touched them, not because their bytes changed on disk.

**Tests split along the seam.** Reachability, range enumeration, and blob reads are genuine git behaviour and get real isomorphic-git repositories in `vaultgit`. `searchindex` keeps fixture-driven tests through the `openWithGit` seam with an extended fake `Git`, since its job is "apply a snapshot to SQL correctly."

## What would reopen this

A workflow where the write-then-commit gap is long enough to matter — hand-authoring sessions where pages sit uncommitted for hours and need to be retrievable meanwhile. The answer then is not to restore the mtime scan wholesale but to make the gap shorter (commit on write), because the mtime scan's real cost was epistemic: it made "what is in the vault" a question with two different answers depending on which subsystem you asked.
