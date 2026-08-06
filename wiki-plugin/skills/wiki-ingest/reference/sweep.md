# Wiki Ingest — sweep procedure

Read this only when `/wiki-ingest` was invoked with a **folder** (under `raw/`, with or without the `raw/` prefix) or with **no path** (meaning all of `raw/`). It is never preloaded — the `wiki-ingest` agent never runs a sweep (see [`../SKILL.md`](../SKILL.md)'s Invocation section), so this doc is dead weight in its context and lives here instead, read once by the invoking session when a sweep is actually happening.

A **sweep** is one of: listing every raw file in scope that the underlying scanner considers eligible, asking per file, and delegating each accepted file to the `wiki-ingest` agent one at a time. Run the sweep in the *invoking* session, not as a subagent — a subagent has no channel to the user (see [#18]'s finding, applied unchanged to this procedure), and the per-file confirmation must be answered by the human, not the agent.

## Procedure

1. **Run the scan.** `python "${CLAUDE_PLUGIN_ROOT}/scripts/ingest_scan.py" <folder-or-empty>` (or `--json` for machine-readable output) prints every file that needs ingestion with its reason:
   - `never-ingested` — no page's `raw_source` points at it (a fresh file).
   - `changed-since-ingestion` — at least one page already points at it, but the raw file is strictly newer than that page's `git_date` *or* `git status --porcelain` reports it modified/untracked. The candidate's `back_pointers` carry the vault-relative paths of the pages that already point at it; pass those through to the `wiki-ingest` agent as a reconciliation hint so it doesn't rediscover them via search.
2. **Print the list, then ask.** A miscount — a file that should have been on the list, or one that shouldn't — is much easier to notice before anything is written. Offer **all / none / choose**:
   - `all` — ingest every eligible file in one go.
   - `none` — bail out without writing anything.
   - `choose` — drop into the literal per-file ask, where each eligible file gets one of **yes / skip / never**:
     - `yes` — ingest it (proceed to step 3 for this file).
     - `skip` — this run only; the file will be offered again on the next sweep.
     - `never` — `python "${CLAUDE_PLUGIN_ROOT}/scripts/ingest.py" --ignore <raw_rel>`. The file is never offered again on this vault unless the policy is edited. For a file that the scanner offers only because it was ingested before [#34]'s `source/` stub-and-back-edge rule landed, add `--ignore-comment "ingested before back-pointers were mandatory"` so a human reading the file later can tell cleanup from policy.
3. **Delegate one accepted file at a time.** For each file the user accepted (`yes`), call the `wiki-ingest` agent with the file's path and — for a `changed-since-ingestion` file — the back-pointer paths as context. The agent runs [`../SKILL.md`](../SKILL.md)'s single-file procedure and returns the manifest. **One subagent, one `IngestPlan`, one commit per file.** Twenty-six conversation transcripts do not fit one context, and per-file commits keep the "what changed" story readable. For a `changed-since-ingestion` file, the agent should treat the back-pointers as a step-3 hint (a known starting set of candidates for the discovery call), not as a closed list.
4. **On failure, move to the next file.** A failed plan leaves whatever was already written on disk uncommitted, consistent with `ingest.py`'s no-rollback stance; rerun is always safe once the cause is fixed. Don't abort the sweep on a single failure — surface it in the final summary and keep going.
5. **Report a summary at the end.** Per file: yes / no / skip / never, the manifest returned (for `yes`), or a one-line error (for `yes` that failed). Never a page-content dump — the `wiki-ingest` agent's own step 6 already returned the manifest; this is the sweep-level roll-up.

The same control files govern the sweep as govern a single ingestion: a folder's `INGESTION.md` (read by each `wiki-ingest` subagent when it processes a file from that folder) steers *how* to ingest its files; a folder's `.ingestignore` (applied by the scan itself, before the per-file ask) steers *which* files to offer. Neither file is itself an ingestion target, and a parent's policy never bleeds into a child folder.
