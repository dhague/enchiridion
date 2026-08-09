---
name: wiki-watch
description: Watch a vault's raw/ folder and auto-ingest new or changed files as they appear, without a manual sweep. Invoke via /wiki-watch to start a long-running foreground watcher session; Ctrl-C to stop it.
---

# Wiki Watch

Per [Auto-ingest new files as they appear](https://github.com/dhague/enchiridion/issues/37): a user-initiated, foreground, event-driven watcher — not a system daemon, not a hook. The user runs `/wiki-watch` in a session they keep open, and Ctrl-Cs it when done. Nothing is installed as a service and nothing auto-starts.

The substantive logic lives in `scripts/watch_raw.py` (event detection, per-file debounce, lock file, queue file) and the existing sweep machinery (`ingest_scan.py`, the `wiki-ingest` agent, `ingest.py`). This file is procedural glue: launch the watcher script, run a startup sweep, then poll its queue and dispatch one `wiki-ingest` subagent per file.

## Procedure

1. **Resolve the vault root** — `$WIKI_ROOT` if set, else `cwd`, per `vault.py`'s resolution order. Every command below assumes cwd (or `$WIKI_ROOT`) is already the vault.

2. **Launch `watch_raw.py` in the background**:
   ```
   python "${CLAUDE_PLUGIN_ROOT}/scripts/watch_raw.py"
   ```
   using `Bash` with `run_in_background: true`. Accepts `--debounce <seconds>` (default 30) if the user asked for a different debounce window.

3. **Poll for startup**, up to a ~10s deadline (checking every ~0.5s) rather than a fixed sleep — a slow machine or cold Python/watchdog import can take longer than a couple of seconds to report in. Check the background output on each poll:
   - If it printed `another watcher is already running (lock at ...)` and exited, **surface this to the user** and stop — do not start a second watcher against the same vault.
   - If it printed `watching <raw/> (debounce=...s, pid=...)`, it's running normally; continue.
   - If the deadline is reached with neither line seen, **surface this to the user** and stop — the watcher's startup could not be confirmed.

4. **Startup sweep.** Run `python "${CLAUDE_PLUGIN_ROOT}/scripts/ingest_scan.py" --json` once. For each eligible file, dispatch a `wiki-ingest` Sonnet subagent via `Task` with the file's path (and, for a `changed-since-ingestion` file, its back-pointers as a reconciliation hint) — same shape as the existing `/wiki-ingest sweep`'s per-file delegation, but **without** the per-file yes/skip/never gate: every eligible file at startup is ingested. Wait for each manifest, log it (see Logging below), then move to the next file.

5. **Watch loop.** Poll the queue file at `.wiki-knowledge/watch-queue.jsonl` every ~5s (`Read` the file, or `cat` it — it's a plain newline-delimited list of vault-relative paths). For each entry:
   - Dispatch a `wiki-ingest` Sonnet subagent via `Task` with the file's path.
   - Wait for the manifest and log it.
   - Remove that entry from the queue: `python "${CLAUDE_PLUGIN_ROOT}/scripts/watch_raw.py" --dequeue <file-rel-path>`.

   The queue is only a wake-up signal (a file path, nothing more) — `ingest_scan.py`'s eligibility logic already ran once (in `watch_raw.py`, before the entry was queued), so no re-check is needed before dispatching.

6. **On Ctrl-C (SIGINT):** the background `watch_raw.py` process receives the same signal and shuts down gracefully on its own (stops its observer, removes its lock, exits) — nothing to forward manually. Finish whatever `wiki-ingest` `Task` is currently in flight (or exit immediately if none is), then end the session's loop.

## Failure handling

A `Task` failure (model error, plan rejected, commit failure) is logged as a one-line error and the loop moves to the next file — never abort the whole watch session over one bad file. The failed file stays in `raw/` and will be re-offered: either by the next `ingest_scan.py` eligibility check (still `never-ingested` or `changed-since-ingestion`), or by another filesystem event settling it again.

## Logging

One line per file, to the session's own stdout — no separate log file:

- Success: `<timestamp> ingested <raw_rel> — <one-line manifest summary>`
- Failure: `<timestamp> failed <raw_rel> — <one-line error>`

Never a page-content dump — the `wiki-ingest` agent's own manifest is already the summary; this is the watch-loop-level roll-up.
