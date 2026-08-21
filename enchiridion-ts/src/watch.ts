/**
 * The raw/ watcher — event-driven detection + debounce + queue.
 *
 * The `/wiki-watch` skill orchestrates; this is the half it launches in the
 * background and polls. Four pieces:
 *
 *   - [runWatch] — the long-lived loop that owns the watcher, the debouncer,
 *     the per-poll-tick sweep, and the queue, with the file-watcher, the
 *     sweep, and the clock injectable so the loop's timing is testable
 *     without chokidar, real signals, or real sleeps. The CLI shrinks to
 *     lock handling and one call to it.
 *   - [Debouncer] — per-file debounce, pure (injectable clock, no threads, no
 *     filesystem) so the timing is testable without real sleeps.
 *   - The lock file at `.wiki-knowledge/watch.lock` — one watcher per vault,
 *     with stale-lock recovery for a hard-killed predecessor.
 *   - The queue file at `.wiki-knowledge/watch-queue.jsonl` — one
 *     vault-relative path per line (despite the extension, not JSON). A
 *     wake-up signal and nothing more: SKILL.md re-checks the sweep when it
 *     needs the reason.
 *
 * Mutual exclusion over the lock/queue files uses an exclusive-create lock
 * file (`.mutex` / `.writelock` created with the `wx` flag, removed on
 * release). The module is pure-JS only (ADR-0017 — a native flock addon
 * would break Bun, the OpenCode runtime), and the atomic `wx` create gives
 * the same cross-process mutual exclusion a blocking flock provides.
 */

import fs from "node:fs";
import path from "node:path";
import { watch as watchRaw } from "chokidar";
import { scan as scanEligible } from "./ingestscan.js";

/** The per-file settle window. */
export const DefaultDebounceSeconds = 30;

/** How long a live-PID lock is trusted before it counts as stale. */
export const StaleLockSeconds = 600;

/** How often the main loop checks for settled files. */
export const DefaultPollIntervalSeconds = 5;

/**
 * Debouncer tracks the most recent event time per vault-relative file path.
 * clock defaults to a monotonic seconds source, injectable so tests can drive
 * settling with fake timestamps.
 */
export class Debouncer {
  private lastEvent = new Map<string, number>();

  constructor(
    private readonly debounceSeconds: number,
    private readonly clock: () => number = defaultClock(),
  ) {}

  /** Notes an event for rel at the current clock time. */
  recordEvent(rel: string): void {
    this.lastEvent.set(rel, this.clock());
  }

  /** Returns, and stops tracking, every file whose debounce window has
   * elapsed. */
  settledFiles(): string[] {
    const now = this.clock();
    const settled: string[] = [];
    for (const [rel, last] of this.lastEvent) {
      if (now - last >= this.debounceSeconds) settled.push(rel);
    }
    for (const rel of settled) this.lastEvent.delete(rel);
    return settled;
  }

  /** Returns the recorded event time for rel, for tests that want to assert
   * what the handler recorded. */
  lastEventTime(rel: string): number | undefined {
    return this.lastEvent.get(rel);
  }
}

function defaultClock(): () => number {
  const start = Date.now();
  return () => (Date.now() - start) / 1000;
}

// --- lock file ---------------------------------------------------------------

/** Writes lockPath with the given (or current) PID and timestamp. */
export function writeLock(
  lockPath: string,
  pid: number,
  startedAt?: Date,
): void {
  fs.mkdirSync(path.dirname(lockPath), { recursive: true, mode: 0o755 });
  if (!pid) pid = process.pid;
  const started = startedAt ? startedAt : new Date();
  const payload = {
    pid,
    started_at: started.toISOString(),
  };
  fs.writeFileSync(lockPath, JSON.stringify(payload), { mode: 0o644 });
}

/** Unlinks lockPath; a no-op when absent. */
export function removeLock(lockPath: string): void {
  try {
    fs.unlinkSync(lockPath);
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code !== "ENOENT") throw err;
  }
}

/** Reports (isStale, pid) for the lock at lockPath. pid is null when the lock
 * file is unparsable.
 *
 * An unparsable lock file counts as stale (fails toward proceeding, not toward
 * a permanent bail). */
function lockIsStale(
  lockPath: string,
  now: Date,
  pidAlive: (pid: number) => boolean,
): { isStale: boolean; pid: number | null } {
  let data: string;
  try {
    data = fs.readFileSync(lockPath, "utf8");
  } catch {
    return { isStale: true, pid: null };
  }
  let payload: { pid?: number; started_at?: string };
  try {
    payload = JSON.parse(data);
  } catch {
    return { isStale: true, pid: null };
  }
  const startedAt = Date.parse(payload.started_at ?? "");
  if (Number.isNaN(startedAt)) return { isStale: true, pid: null };
  const pid = payload.pid ?? 0;
  if (!pidAlive(pid)) return { isStale: true, pid };
  if ((now.getTime() - startedAt) / 1000 > StaleLockSeconds) {
    return { isStale: true, pid };
  }
  return { isStale: false, pid };
}

/** Probes whether pid names a live process via `kill(pid, 0)`: ESRCH means
 * dead, EPERM means alive-but-not-ours. */
export function defaultPIDAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return (err as NodeJS.ErrnoException).code !== "ESRCH";
  }
}

/**
 * Tries to acquire the watch lock. Returns { acquired, stalePID }.
 *
 * A live lock (PID alive, within [StaleLockSeconds]) means another watcher is
 * running: { acquired: false, stalePID: null }, lock untouched. A stale one is
 * removed and replaced; the removed lock's PID is returned (null when the lock
 * file was unparsable), so the caller can log the takeover. Check, unlink and
 * write all happen under a companion `.mutex` file held exclusively, so two
 * processes racing a stale takeover can't both pass the staleness check before
 * either writes.
 */
export function acquireLock(
  lockPath: string,
  now?: Date,
  pidAlive?: (pid: number) => boolean,
): { acquired: boolean; stalePID: number | null } {
  const nowDate = now ? now : new Date();
  const alive = pidAlive ?? defaultPIDAlive;
  const result: { acquired: boolean; stalePID: number | null } = {
    acquired: false,
    stalePID: null,
  };
  const mutexPath = lockPath + ".mutex";
  withExclusiveLock(mutexPath, () => {
    if (fs.existsSync(lockPath)) {
      const { isStale, pid } = lockIsStale(lockPath, nowDate, alive);
      if (!isStale) {
        result.acquired = false;
        return;
      }
      fs.unlinkSync(lockPath);
      writeLock(lockPath, 0, nowDate);
      result.acquired = true;
      result.stalePID = pid;
      return;
    }
    writeLock(lockPath, 0, nowDate);
    result.acquired = true;
  });
  return result;
}

// --- queue file --------------------------------------------------------------

/** Returns the queue's entries. An absent queue is empty.
 *
 * Split on "\n", never anything fancier — a path can legitimately contain
 * other control bytes. */
export function readQueue(queuePath: string): string[] {
  let data: string;
  try {
    data = fs.readFileSync(queuePath, "utf8");
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw err;
  }
  const out: string[] = [];
  for (const line of data.split("\n")) {
    if (line !== "") out.push(line);
  }
  return out;
}

/**
 * Runs fn(currentLines) -> newLines under an exclusive lock.
 *
 * The lock serializes concurrent writers so a read-modify-write can't lose an
 * update; writing to a `.tmp` sibling and renaming means a concurrent reader
 * never sees a partial write. */
function withQueueLock(
  queuePath: string,
  fn: (lines: string[]) => string[],
): void {
  fs.mkdirSync(path.dirname(queuePath), { recursive: true, mode: 0o755 });
  const writelockPath = queuePath + ".writelock";
  withExclusiveLock(writelockPath, () => {
    const newLines = fn(readQueue(queuePath));
    let body = "";
    for (const line of newLines) body += line + "\n";
    const tmpPath = queuePath + ".tmp";
    fs.writeFileSync(tmpPath, body, { mode: 0o644 });
    fs.renameSync(tmpPath, queuePath);
  });
}

/** Appends rel to the queue, unless it's already there (idempotent). */
export function appendQueue(queuePath: string, rel: string): void {
  withQueueLock(queuePath, (lines) => {
    for (const line of lines) {
      if (line === rel) return lines;
    }
    return [...lines, rel];
  });
}

/** Removes every occurrence of rel from the queue. */
export function removeFromQueue(queuePath: string, rel: string): void {
  withQueueLock(queuePath, (lines) => lines.filter((l) => l !== rel));
}

// --- eligibility check on settle ----------------------------------------------

/** Enqueues settledRel iff it's in eligibleRels.
 *
 * A settled event doesn't mean "ingest this" — an `.ingestignore` match, or a
 * file whose back-pointer page is already current, settles too. eligibleRels
 * comes from one sweep per poll tick, not per file, so eligibility matches the
 * manual sweep exactly. */
export function checkAndEnqueue(
  eligibleRels: Set<string>,
  settledRel: string,
  queuePath: string,
): boolean {
  if (!eligibleRels.has(settledRel)) return false;
  appendQueue(queuePath, settledRel);
  return true;
}

// --- watch paths --------------------------------------------------------------

/** The set of files one watcher run touches. */
export interface Paths {
  root: string;
  lock: string;
  queue: string;
}

/** Returns the watch paths for a vault root. */
export function forRoot(root: string): Paths {
  const wk = path.join(root, ".wiki-knowledge");
  return {
    root,
    lock: path.join(wk, "watch.lock"),
    queue: path.join(wk, "watch-queue.jsonl"),
  };
}

/** Maps one filesystem event path to a vault-relative path, or null when the
 * event should be ignored: a directory, or a path outside root. */
export function relForEvent(root: string, abs: string): string | null {
  let info: fs.Stats;
  try {
    info = fs.statSync(abs);
  } catch {
    return null;
  }
  if (info.isDirectory()) return null;
  const rel = path.relative(root, abs);
  if (rel === ".." || rel.startsWith(".." + path.sep)) return null;
  return toSlash(rel);
}

// --- exclusive-create lock file (ADR-0017: pure JS, no native addons) ---------

/** Runs critical under an exclusive lock on lockPath, created atomically with
 * the `wx` flag and removed on release.
 *
 * Replaces a blocking flock: an atomic exclusive create gives the same cross-process
 * mutual exclusion without a native addon. When another process holds the
 * lock, blocks (retrying) until it is released. */
function withExclusiveLock(lockPath: string, critical: () => void): void {
  fs.mkdirSync(path.dirname(lockPath), { recursive: true, mode: 0o755 });
  let fd: number;
  for (;;) {
    try {
      fd = fs.openSync(lockPath, "wx");
      break;
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== "EEXIST") throw err;
      sleep(5);
    }
  }
  let criticalErr: unknown = null;
  try {
    critical();
  } catch (err) {
    criticalErr = err;
  } finally {
    fs.closeSync(fd);
    try {
      fs.unlinkSync(lockPath);
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== "ENOENT") {
        if (criticalErr === null) criticalErr = err;
      }
    }
  }
  if (criticalErr !== null) throw criticalErr;
}

function sleep(ms: number): void {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function toSlash(p: string): string {
  return p.split(path.sep).join("/");
}

// --- the watch loop -----------------------------------------------------------

/** The minimal file-watcher surface [runWatch] drives. Chokidar's FSWatcher
 * satisfies it structurally. */
export interface Watcher {
  /** Registers a filesystem-event handler. The loop consumes only the "all"
   * event, keyed by (eventName, path). */
  on(event: "all", cb: (eventName: string, p: string) => void): void;
  /** Registers the log-and-keep-watching error handler. */
  on(event: "error", cb: (err: unknown) => void): void;
  /** Registers the ready handler, which fires the "watching …" banner. */
  on(event: "ready", cb: () => void): void;
  /** Stops watching; resolves once fully closed. */
  close(): Promise<void>;
}

/** One eligibility sweep, run per poll tick when files have settled. Returns
 * the vault-relative paths the sweep offers. */
export type Sweep = () => Promise<Set<string>>;

/** Schedules `tick` every `ms`; the returned function cancels it. The default
 * uses setInterval/clearInterval; tests inject a scheduler that stores the
 * tick for manual driving, so no real sleeps are needed. */
export type Scheduler = (tick: () => void, ms: number) => () => void;

/** The injectable seams [runWatch] composes. Every field defaults to the
 * production implementation; tests inject fakes for all of them. */
export interface WatchOptions {
  /** Per-file settle window, seconds (default [DefaultDebounceSeconds]). */
  debounceSeconds?: number;
  /** How often to check for settled files, seconds (default
   * [DefaultPollIntervalSeconds]). */
  pollIntervalSeconds?: number;
  /** Creates the file watcher over the raw/ root (default: chokidar). */
  makeWatcher?: (rawRoot: string) => Watcher;
  /** One eligibility sweep per poll tick (default: the real ingest-scan over
   * paths.root, matching the manual sweep exactly). */
  sweep?: Sweep;
  /** Monotonic-seconds clock for the debouncer (default: real time). */
  clock?: () => number;
  /** Registers a signal handler (default: process.on). */
  onSignal?: (signal: "SIGINT" | "SIGTERM", cb: () => void) => void;
  /** Removes a signal handler (default: process.removeListener). */
  offSignal?: (signal: "SIGINT" | "SIGTERM", cb: () => void) => void;
  /** Schedules the per-poll-tick sweep (default: setInterval). */
  schedule?: Scheduler;
  /** The pid printed in the "watching …" banner (default: process.pid). */
  pid?: number;
  /** Emits the loop's user-facing lines (default: console.log). */
  log?: (line: string) => void;
}

/**
 * runWatch runs the long-lived watch loop: a file watcher over root/raw/
 * records events into the debouncer; on each poll tick settled files are
 * swept for eligibility and enqueued. SIGINT/SIGTERM logs "watcher stopped",
 * cancels the poll, closes the watcher, removes the lock, and resolves the
 * returned promise.
 *
 * Creates root/raw/ if missing. The watcher, sweep, clock, signal handling,
 * scheduler, and log are injectable, so the loop is testable with no
 * chokidar, no real signals, and no real sleeps.
 */
export function runWatch(
  paths: Paths,
  options: WatchOptions = {},
): Promise<void> {
  const debounceSeconds = options.debounceSeconds ?? DefaultDebounceSeconds;
  const pollIntervalSeconds =
    options.pollIntervalSeconds ?? DefaultPollIntervalSeconds;
  const rawRoot = path.join(paths.root, "raw");
  fs.mkdirSync(rawRoot, { recursive: true, mode: 0o755 });

  const watcher = (options.makeWatcher ?? defaultWatcher)(rawRoot);
  const debouncer = new Debouncer(
    debounceSeconds,
    options.clock ?? defaultClock(),
  );
  const sweep = options.sweep ?? defaultSweep(paths.root);
  const schedule = options.schedule ?? defaultSchedule;
  const onSignal = options.onSignal ?? defaultOnSignal;
  const offSignal = options.offSignal ?? defaultOffSignal;
  const log = options.log ?? console.log;
  const pid = options.pid ?? process.pid;

  return new Promise<void>((resolve) => {
    let cancel = (): void => {};
    let stopped = false;
    const stop = (): void => {
      if (stopped) return;
      stopped = true;
      log("watcher stopped");
      cancel();
      offSignal("SIGINT", stop);
      offSignal("SIGTERM", stop);
      void watcher.close();
      removeLock(paths.lock);
      resolve();
    };
    onSignal("SIGINT", stop);
    onSignal("SIGTERM", stop);

    watcher.on("all", (_eventName: string, p: string) => {
      const rel = relForEvent(paths.root, p);
      if (rel !== null) debouncer.recordEvent(rel);
    });
    watcher.on("error", (_err: unknown) => {
      // Log-and-keep-watching; a transient read error isn't fatal.
    });
    watcher.on("ready", () => {
      log(`watching ${rawRoot} (debounce=${debounceSeconds}s, pid=${pid})`);
    });

    cancel = schedule(() => {
      const settled = debouncer.settledFiles();
      if (settled.length === 0) return;
      sweep()
        .then((eligible) => {
          for (const rel of settled) {
            const queued = checkAndEnqueue(eligible, rel, paths.queue);
            if (queued) log(`queued ${rel}`);
          }
        })
        .catch((err) => {
          log(`error scanning raw/: ${(err as Error).message}`);
        });
    }, pollIntervalSeconds * 1000);
  });
}

function defaultWatcher(rawRoot: string): Watcher {
  return watchRaw(rawRoot, { ignoreInitial: true });
}

function defaultSweep(root: string): Sweep {
  return async (): Promise<Set<string>> => {
    const result = await scanEligible(root, "", null);
    return new Set(result.eligible.map((c) => c.rawRel));
  };
}

function defaultSchedule(tick: () => void, ms: number): () => void {
  const id = setInterval(tick, ms);
  return () => clearInterval(id);
}

function defaultOnSignal(signal: "SIGINT" | "SIGTERM", cb: () => void): void {
  process.on(signal, cb);
}

function defaultOffSignal(signal: "SIGINT" | "SIGTERM", cb: () => void): void {
  process.removeListener(signal, cb);
}
