/**
 * watch tests — mirror enchiridion-go/internal/watch/watch_test.go, ported to
 * the TS module.
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
  writeLock,
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

test("defaults: exported constants match Go", () => {
  assert.equal(DefaultDebounceSeconds, 30);
  assert.equal(DefaultPollIntervalSeconds, 5);
  assert.equal(StaleLockSeconds, 600);
});
