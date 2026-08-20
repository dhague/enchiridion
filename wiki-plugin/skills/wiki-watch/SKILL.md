---
name: wiki-watch
description: Watch a vault's raw/ folder and auto-ingest new or changed files as they appear, without a manual sweep. Invoke via /wiki-watch to start a long-running foreground watcher session; Ctrl-C to stop it.
---
# Wiki Watch

Per [Auto-ingest new files as they appear](https://github.com/dhague/enchiridion/issues/37): user-initiated, foreground, event-driven watcher — not system daemon, not hook. User runs `/wiki-watch` in open session, Ctrl-Cs when done. Nothing installed as service, nothing auto-starts.

Substantive logic in the `watch` subcommand of the enchiridion script layer (`<plugin-root>/bin/enchiridion watch` — event detection, per-file debounce, lock file, queue file) and existing sweep machinery (`enchiridion ingest-scan`, `wiki-ingest` agent, `enchiridion ingest`). This file is procedural glue: launch watcher, run startup sweep, poll queue, dispatch one `wiki-ingest` subagent per file.

## Procedure

1. **Resolve the vault root** — `$WIKI_ROOT` if set, else `cwd`, per the vault root resolution order. Every command below assumes cwd (or `$WIKI_ROOT`) is vault.

2. **Launch the `enchiridion watch` subcommand in the background**:
   ```
   "<plugin-root>/bin/enchiridion" watch
   ```
   using `Bash` with `run_in_background: true`. Accepts `--debounce <seconds>` (default 30) if user asked for different debounce window.

3. **Poll for startup**, up to ~10s deadline (checking every ~0.5s) — a slow machine can take longer than a couple seconds. Check background output each poll:
   - Printed `another watcher is already running (lock at ...)` and exited: **surface to user** and stop — do not start second watcher against same vault.
   - Printed `watching <raw/> (debounce=...s, pid=...)`: running normally, continue.
   - Deadline reached with neither line: **surface to user** and stop — watcher startup unconfirmed.

4. **Startup sweep.** Run `"<plugin-root>/bin/enchiridion" ingest-scan --json` once. For each eligible file, dispatch `wiki-ingest` Sonnet subagent via `Task` with file path (and, for `changed-since-ingestion` file, its back-pointers as reconciliation hint) — same shape as existing `/wiki-ingest sweep`'s per-file delegation, but **without** per-file yes/skip/never gate: every eligible file at startup gets ingested. Wait for each manifest, log it (see Logging below), move to next file.

5. **Watch loop.** Poll queue file at `.wiki-knowledge/watch-queue.jsonl` every ~5s (`Read` or `cat` — plain newline-delimited list of vault-relative paths). For each entry:
   - Dispatch `wiki-ingest` Sonnet subagent via `Task` with file path.
   - Wait for manifest and log it.
   - Remove entry from queue: `"<plugin-root>/bin/enchiridion" watch --dequeue <file-rel-path>`.

   Queue is only wake-up signal (file path, nothing more) — the eligibility logic already ran once (in `enchiridion watch`, before entry queued), so no re-check needed before dispatching.

6. **On Ctrl-C (SIGINT):** background `enchiridion watch` receives same signal and shuts down gracefully (stops observer, removes lock, exits) — nothing to forward manually. Finish current in-flight `wiki-ingest` `Task` (or exit immediately if none), then end loop.

## Failure handling

`Task` failure (model error, plan rejected, commit failure): log one-line error, loop moves to next file — never abort whole watch session over one bad file. Failed file stays in `raw/`, re-offered by next `enchiridion ingest-scan` eligibility check (still `never-ingested` or `changed-since-ingestion`) or another filesystem event.

## Logging

One line per file to session stdout — no separate log file:

- Success: `<timestamp> ingested <raw_rel> — <one-line manifest summary>`
- Failure: `<timestamp> failed <raw_rel> — <one-line error>`

No page-content dump — `wiki-ingest` agent's manifest is already summary; this is watch-loop-level roll-up.