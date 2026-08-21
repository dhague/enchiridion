/**
 * watch tests — the debounce/lock/queue machinery behind /wiki-watch.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  DefaultPollIntervalSeconds,
  DefaultDebounceSeconds,
  Debouncer,
  StaleLockSeconds,
  appendQueue,
  acquireLock,
  checkAndEnqueue,
  forRoot,
  readQueue,
  relForEvent,
  removeFromQueue,
  removeLock,
  runWatch,
  writeLock,
  type Watcher,
} from "./watch.js";

function tmpRoot(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "watch-test-"));
}

// --- debounce timing ---------------------------------------------------------

test("debounce: not settled within window", () => {
  let now = 0;
  const d = new Debouncer(30, () => now);
  for (const ts of [0, 5, 10, 15, 20, 25]) {
    now = ts;
    d.recordEvent("raw/notes/a.md");
  }
  assert.deepEqual(d.settledFiles(), []);
});

test("debounce: settles after final silence", () => {
  let now = 0;
  const d = new Debouncer(30, () => now);
  for (const ts of [0, 10, 20, 35]) {
    now = ts;
    d.recordEvent("raw/notes/a.md");
  }
  now = 35 + 29;
  assert.deepEqual(d.settledFiles(), []);
  now = 35 + 30;
  assert.deepEqual(d.settledFiles(), ["raw/notes/a.md"]);
});

test("debounce: settled files stop being tracked", () => {
  let now = 0;
  const d = new Debouncer(10, () => now);
  d.recordEvent("raw/a.md");
  now = 10;
  assert.deepEqual(d.settledFiles(), ["raw/a.md"]);
  now = 100;
  assert.deepEqual(d.settledFiles(), []);
});

test("debounce: is per-file", () => {
  let now = 0;
  const d = new Debouncer(30, () => now);
  d.recordEvent("raw/a.md");
  now = 15;
  d.recordEvent("raw/b.md");
  now = 30;
  assert.deepEqual(d.settledFiles(), ["raw/a.md"]);
});

// --- lock file lifecycle -----------------------------------------------------

test("write lock then remove", () => {
  const lockPath = path.join(tmpRoot(), ".wiki-knowledge", "watch.lock");
  writeLock(lockPath, 1234, new Date());
  assert.ok(fs.existsSync(lockPath));
  removeLock(lockPath);
  assert.ok(!fs.existsSync(lockPath));
});

test("remove lock missing is a no-op", () => {
  assert.doesNotThrow(() => removeLock(path.join(tmpRoot(), "watch.lock")));
});

test("acquire lock: live PID bails", () => {
  const lockPath = path.join(tmpRoot(), ".wiki-knowledge", "watch.lock");
  writeLock(lockPath, process.pid, new Date());
  const { acquired } = acquireLock(lockPath);
  assert.equal(acquired, false);
});

test("acquire lock: old timestamp is stale", () => {
  const lockPath = path.join(tmpRoot(), ".wiki-knowledge", "watch.lock");
  const old = new Date(Date.now() - (StaleLockSeconds + 60) * 1000);
  writeLock(lockPath, process.pid, old);
  const { acquired } = acquireLock(lockPath);
  assert.equal(acquired, true);
});

test("acquire lock: recent timestamp not stale", () => {
  const lockPath = path.join(tmpRoot(), ".wiki-knowledge", "watch.lock");
  const recent = new Date(Date.now() - (StaleLockSeconds - 60) * 1000);
  writeLock(lockPath, process.pid, recent);
  const { acquired } = acquireLock(lockPath);
  assert.equal(acquired, false);
});

test("acquire lock: no existing lock succeeds", () => {
  const lockPath = path.join(tmpRoot(), ".wiki-knowledge", "watch.lock");
  const { acquired, stalePID } = acquireLock(lockPath);
  assert.equal(acquired, true);
  assert.equal(stalePID, null);
});

test("acquire lock: unparsable lock is stale", () => {
  const lockPath = path.join(tmpRoot(), ".wiki-knowledge", "watch.lock");
  fs.mkdirSync(path.dirname(lockPath), { recursive: true });
  fs.writeFileSync(lockPath, "not json");
  const { acquired } = acquireLock(lockPath);
  assert.equal(acquired, true);
});

test("acquire lock: dead PID is stale, reports the removed pid", () => {
  const lockPath = path.join(tmpRoot(), ".wiki-knowledge", "watch.lock");
  const deadPID = 1 << 30;
  writeLock(lockPath, deadPID, new Date());
  const { acquired, stalePID } = acquireLock(lockPath);
  assert.equal(acquired, true);
  assert.equal(stalePID, deadPID);
});

// --- queue -------------------------------------------------------------------

test("queue: append creates and appends", () => {
  const queuePath = path.join(
    tmpRoot(),
    ".wiki-knowledge",
    "watch-queue.jsonl",
  );
  appendQueue(queuePath, "raw/a.md");
  appendQueue(queuePath, "raw/b.md");
  assert.deepEqual(readQueue(queuePath), ["raw/a.md", "raw/b.md"]);
});

test("queue: append is idempotent", () => {
  const queuePath = path.join(tmpRoot(), "watch-queue.jsonl");
  appendQueue(queuePath, "raw/a.md");
  appendQueue(queuePath, "raw/a.md");
  assert.deepEqual(readQueue(queuePath), ["raw/a.md"]);
});

test("queue: remove", () => {
  const queuePath = path.join(tmpRoot(), "watch-queue.jsonl");
  appendQueue(queuePath, "raw/a.md");
  appendQueue(queuePath, "raw/b.md");
  removeFromQueue(queuePath, "raw/a.md");
  assert.deepEqual(readQueue(queuePath), ["raw/b.md"]);
});

test("queue: read missing file is empty", () => {
  assert.deepEqual(readQueue(path.join(tmpRoot(), "watch-queue.jsonl")), []);
});

// --- eligibility check on settle ----------------------------------------------

test("check-and-enqueue: enqueues when eligible", () => {
  const queuePath = path.join(tmpRoot(), "watch-queue.jsonl");
  const ok = checkAndEnqueue(new Set(["raw/a.md"]), "raw/a.md", queuePath);
  assert.equal(ok, true);
  assert.deepEqual(readQueue(queuePath), ["raw/a.md"]);
});

test("check-and-enqueue: skips when not eligible", () => {
  const queuePath = path.join(tmpRoot(), "watch-queue.jsonl");
  const ok = checkAndEnqueue(new Set(["raw/other.md"]), "raw/a.md", queuePath);
  assert.equal(ok, false);
  assert.deepEqual(readQueue(queuePath), []);
});

// --- relForEvent --------------------------------------------------------------

test("rel-for-event: maps file under root", () => {
  const root = tmpRoot();
  const p = path.join(root, "raw", "note.md");
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, "x");
  assert.equal(relForEvent(root, p), "raw/note.md");
});

test("rel-for-event: ignores directory", () => {
  const root = tmpRoot();
  const dir = path.join(root, "raw", "subdir");
  fs.mkdirSync(dir, { recursive: true });
  assert.equal(relForEvent(root, dir), null);
});

test("rel-for-event: ignores path outside root", () => {
  const root = tmpRoot();
  const outside = path.join(tmpRoot(), "note.md");
  fs.writeFileSync(outside, "x");
  assert.equal(relForEvent(root, outside), null);
});

// --- paths + defaults ----------------------------------------------------------

test("for-root: yields the watch paths under .wiki-knowledge", () => {
  const paths = forRoot("/vault");
  assert.equal(paths.lock, "/vault/.wiki-knowledge/watch.lock");
  assert.equal(paths.queue, "/vault/.wiki-knowledge/watch-queue.jsonl");
});

test("defaults: exported constants", () => {
  assert.equal(DefaultDebounceSeconds, 30);
  assert.equal(DefaultPollIntervalSeconds, 5);
  assert.equal(StaleLockSeconds, 600);
});

// --- the watch loop (runWatch seam tests) ------------------------------------

/** A fake file watcher: the test fires "ready"/"all" events and calls
 * close(), with no chokidar on the loop's seam. */
class FakeWatcher implements Watcher {
  closed = false;
  private readyHandlers: (() => void)[] = [];
  private allHandlers: ((eventName: string, p: string) => void)[] = [];
  private errorHandlers: ((err: unknown) => void)[] = [];
  on(
    event: "all" | "error" | "ready",
    cb: (eventName: string, p: string) => void,
  ): void {
    if (event === "all") this.allHandlers.push(cb);
    else if (event === "error")
      this.errorHandlers.push(cb as (err: unknown) => void);
    else this.readyHandlers.push(cb as () => void);
  }
  close(): Promise<void> {
    this.closed = true;
    return Promise.resolve();
  }
  emit(event: string, ...args: unknown[]): void {
    if (event === "all") {
      for (const cb of this.allHandlers)
        cb(args[0] as string, args[1] as string);
    } else if (event === "error") {
      for (const cb of this.errorHandlers) cb(args[0]);
    } else {
      for (const cb of this.readyHandlers) cb();
    }
  }
}

/** Await a macrotask so the tick's async sweep chain has run to completion. */
async function flush(): Promise<void> {
  await new Promise<void>((r) => setImmediate(r));
}

/** A fake signal hub: runWatch's onSignal/offSignal pair, so a test can fire
 * SIGINT/SIGTERM without touching the process. */
function recordSignals(): {
  onSignal: (sig: "SIGINT" | "SIGTERM", cb: () => void) => void;
  offSignal: (sig: "SIGINT" | "SIGTERM", cb: () => void) => void;
  signals: Record<string, (() => void)[]>;
} {
  const signals: Record<string, (() => void)[]> = {};
  return {
    onSignal: (sig, cb) => {
      (signals[sig] ??= []).push(cb);
    },
    offSignal: (sig, cb) => {
      signals[sig] = (signals[sig] ?? []).filter((c) => c !== cb);
    },
    signals,
  };
}

/** Writes a real file under root/raw/, returning its absolute path — the loop
 * maps it through relForEvent exactly as production would. */
function rawFile(root: string, name: string): string {
  const abs = path.join(root, "raw", name);
  fs.mkdirSync(path.dirname(abs), { recursive: true });
  fs.writeFileSync(abs, "x");
  return abs;
}

test("run-watch: settle, sweep, enqueue end to end; a signal stops and cleans up", async () => {
  const root = tmpRoot();
  const paths = forRoot(root);
  const abs = rawFile(root, "a.md");

  const watcher = new FakeWatcher();
  const { onSignal, offSignal, signals } = recordSignals();
  const lines: string[] = [];
  const ticks: (() => void)[] = [];
  let pollMs = 0;
  let now = 0;
  let sweepRuns = 0;

  const done = runWatch(paths, {
    debounceSeconds: 30,
    pollIntervalSeconds: 5,
    makeWatcher: () => watcher,
    clock: () => now,
    sweep: async () => {
      sweepRuns++;
      return new Set(["raw/a.md"]);
    },
    onSignal,
    offSignal,
    schedule: (cb, ms) => {
      ticks.push(cb);
      pollMs = ms;
      return () => {};
    },
    pid: 42,
    log: (l) => lines.push(l),
  });

  assert.equal(pollMs, 5 * 1000);
  watcher.emit("ready");
  watcher.emit("all", "add", abs);
  now = 100;
  ticks[0]();
  await flush();

  assert.deepEqual(readQueue(paths.queue), ["raw/a.md"]);
  assert.equal(sweepRuns, 1);
  assert.deepEqual(lines, [
    `watching ${path.join(root, "raw")} (debounce=30s, pid=42)`,
    "queued raw/a.md",
  ]);

  writeLock(paths.lock, 999, new Date());
  signals.SIGTERM?.[0]();
  await done;
  assert.match(lines[lines.length - 1], /watcher stopped/);
  assert.ok(!fs.existsSync(paths.lock), "lock removed on stop");
  assert.equal(watcher.closed, true);
  assert.deepEqual(signals.SIGINT, []);
  assert.deepEqual(signals.SIGTERM, []);
});

test("run-watch: an eligibility miss settles without enqueuing", async () => {
  const root = tmpRoot();
  const paths = forRoot(root);
  const abs = rawFile(root, "b.md");

  const watcher = new FakeWatcher();
  const { onSignal, offSignal, signals } = recordSignals();
  const ticks: (() => void)[] = [];
  let now = 0;
  const sweepSets: Set<string>[] = [
    new Set(["raw/other.md"]),
    new Set(["raw/b.md"]),
  ];
  let sweepRuns = 0;

  const done = runWatch(paths, {
    debounceSeconds: 30,
    makeWatcher: () => watcher,
    clock: () => now,
    sweep: async () => sweepSets[sweepRuns++],
    onSignal,
    offSignal,
    schedule: (cb) => {
      ticks.push(cb);
      return () => {};
    },
    log: () => {},
  });

  watcher.emit("all", "add", abs);
  now = 100;
  ticks[0]();
  await flush();
  assert.deepEqual(readQueue(paths.queue), []);
  assert.equal(sweepRuns, 1);

  // A settled-and-missed file stops being tracked: the next tick has nothing
  // to sweep, even though the sweep would now offer the file.
  ticks[0]();
  await flush();
  assert.equal(sweepRuns, 1);
  assert.deepEqual(readQueue(paths.queue), []);

  signals.SIGTERM?.[0]();
  await done;
});
