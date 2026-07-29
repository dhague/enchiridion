"""The raw/ watcher — event-driven detection + debounce + queue (#62, per #37).

The `/wiki-watch` skill *is* the watcher's orchestrator; this module is the
substantive half it launches in the background and polls. Three pieces:

* :class:`Debouncer` — per-file debounce logic, pure (an injectable clock,
  no threads, no filesystem) so the 30s-default timing is testable without
  real sleeps.
* The lock file (:func:`acquire_lock`/:func:`remove_lock`) at
  ``.wiki-knowledge/watch.lock`` — one watcher per vault, with stale-lock
  recovery for a hard-killed previous instance (crash, ``kill -9``, VS Code
  window closed mid-session).
* The queue file (:func:`append_queue`/:func:`read_queue`/
  :func:`remove_from_queue`) at ``.wiki-knowledge/watch-queue.jsonl`` — a
  wake-up signal with a file path, nothing more. The SKILL.md re-checks via
  ``ingest_scan.py`` if it cares about the reason; this queue doesn't carry
  one.

CLI::

    python watch_raw.py [--vault <root>] [--debounce 30]
"""
from __future__ import annotations

import fcntl
import json
import os
import signal
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

import ingest_scan
import vault as vault_mod

DEFAULT_DEBOUNCE_SECONDS = 30.0
STALE_LOCK_SECONDS = 600  # 10 minutes
DEFAULT_POLL_INTERVAL_SECONDS = 5.0


# --- debounce --------------------------------------------------------------


class Debouncer:
    """Tracks the most recent event time per (vault-relative) file path.

    ``clock`` defaults to :func:`time.monotonic` but is injectable so tests
    can drive settling with fake timestamps instead of real sleeps.
    """

    def __init__(self, debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS, clock: Callable[[], float] | None = None):
        import time as _time

        self.debounce_seconds = debounce_seconds
        self.clock = clock or _time.monotonic
        self._last_event: dict[str, float] = {}

    def record_event(self, rel: str) -> None:
        """Note an event for ``rel`` at the current clock time."""
        self._last_event[rel] = self.clock()

    def settled_files(self) -> list[str]:
        """Return, and stop tracking, every file whose debounce window has elapsed."""
        now = self.clock()
        settled = [
            rel for rel, last in self._last_event.items()
            if now - last >= self.debounce_seconds
        ]
        for rel in settled:
            del self._last_event[rel]
        return settled


# --- lock file ---------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def write_lock(lock_path: Path, pid: int | None = None, started_at: datetime | None = None) -> None:
    """Write ``lock_path`` with the given (or current) PID and timestamp."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": pid if pid is not None else os.getpid(),
        "started_at": (started_at or datetime.now(timezone.utc)).isoformat(),
    }
    lock_path.write_text(json.dumps(payload), encoding="utf-8")


def remove_lock(lock_path: Path) -> None:
    lock_path.unlink(missing_ok=True)


def _lock_is_stale(
    lock_path: Path,
    now: datetime,
    pid_alive: Callable[[int], bool],
) -> tuple[bool, int | None]:
    """Return ``(is_stale, pid)`` for the lock at ``lock_path``.

    An unparsable lock file counts as stale (fails toward proceeding, not
    toward a permanent bail).
    """
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        pid = payload["pid"]
        started_at = datetime.fromisoformat(payload["started_at"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return True, None

    if not pid_alive(pid):
        return True, pid
    if (now - started_at).total_seconds() > STALE_LOCK_SECONDS:
        return True, pid
    return False, pid


def acquire_lock(
    lock_path: Path,
    now: datetime | None = None,
    pid_alive: Callable[[int], bool] | None = None,
) -> bool:
    """Try to acquire the watch lock. Returns ``True`` iff acquired.

    A live lock (PID alive, timestamp within :data:`STALE_LOCK_SECONDS`)
    means another watcher is already running — this call returns ``False``
    without touching the lock file. A stale lock is logged, removed, and
    replaced with this process's own lock.

    The stale check, unlink, and write happen under a companion
    ``.mutex`` file held with an exclusive ``flock`` for the whole
    sequence, so two processes racing a stale takeover can't both pass
    the staleness check before either has written its own lock (#67).
    """
    now = now or datetime.now(timezone.utc)
    pid_alive = pid_alive or _pid_alive

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    mutex_path = lock_path.with_name(lock_path.name + ".mutex")
    with open(mutex_path, "w", encoding="utf-8") as mutex:
        fcntl.flock(mutex, fcntl.LOCK_EX)
        try:
            if lock_path.exists():
                stale, pid = _lock_is_stale(lock_path, now, pid_alive)
                if not stale:
                    return False
                print(f"previous watcher exited without cleanup, removing stale lock (pid={pid})")
                lock_path.unlink()

            write_lock(lock_path, started_at=now)
            return True
        finally:
            fcntl.flock(mutex, fcntl.LOCK_UN)


# --- queue file ---------------------------------------------------------------


def _queue_writelock_path(queue_path: Path) -> Path:
    return queue_path.with_name(queue_path.name + ".writelock")


def _with_queue_lock(queue_path: Path, fn: Callable[[list[str]], list[str]]) -> None:
    """Run ``fn(current_lines) -> new_lines`` under an exclusive file lock.

    Serializes concurrent writers (threads or processes) so a
    read-modify-write cycle can't lose an update; the new content is then
    written to a ``.tmp`` sibling and renamed into place so a concurrent
    reader never observes a partial write.
    """
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    writelock_path = _queue_writelock_path(queue_path)
    with open(writelock_path, "w", encoding="utf-8") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            existing = read_queue(queue_path)
            new_lines = fn(existing)
            tmp_path = queue_path.with_name(queue_path.name + ".tmp")
            content = "".join(line + "\n" for line in new_lines)
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(queue_path)
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)


def append_queue(queue_path: Path, rel: str) -> None:
    """Append ``rel`` to the queue, unless it's already there (idempotent)."""

    def _append(lines: list[str]) -> list[str]:
        return lines if rel in lines else [*lines, rel]

    _with_queue_lock(queue_path, _append)


def remove_from_queue(queue_path: Path, rel: str) -> None:
    """Remove every occurrence of ``rel`` from the queue."""

    def _remove(lines: list[str]) -> list[str]:
        return [line for line in lines if line != rel]

    _with_queue_lock(queue_path, _remove)


def read_queue(queue_path: Path) -> list[str]:
    if not queue_path.exists():
        return []
    # ``str.split("\n")``, not ``splitlines()`` — splitlines() also breaks on
    # \x0b/\x0c/\x1c-\x1e, any of which could legitimately appear in a path.
    return [line for line in queue_path.read_text(encoding="utf-8").split("\n") if line]


# --- eligibility check on settle ----------------------------------------------


def check_and_enqueue(eligible_rels: set[str], settled_rel: str, queue_path: Path) -> bool:
    """Enqueue ``settled_rel`` iff it's in ``eligible_rels``.

    A settled filesystem event doesn't automatically mean "ingest this" — a
    file matching its folder's ``.ingestignore``, or one whose back-pointer
    page is already up to date, settles too. ``eligible_rels`` comes from one
    ``ingest_scan.scan`` per poll tick (not per file, see caller), keeping
    eligibility semantics identical to the manual sweep.
    """
    if settled_rel in eligible_rels:
        append_queue(queue_path, settled_rel)
        return True
    return False


# --- watchdog wiring -----------------------------------------------------------


class _RawEventHandler(FileSystemEventHandler):
    def __init__(self, root: Path, debouncer: Debouncer):
        self.root = root
        self.debouncer = debouncer

    def on_any_event(self, event) -> None:
        if event.is_directory:
            return
        # Atomic saves (vim, VSCode, Obsidian: write-temp-then-rename) surface
        # as a single moved event whose *dest_path* is the file that matters —
        # src_path is the temp file, already gone by the time we'd re-check it.
        path = getattr(event, "dest_path", "") or event.src_path
        try:
            rel = Path(path).relative_to(self.root).as_posix()
        except ValueError:
            return
        self.debouncer.record_event(rel)


@dataclass
class WatchPaths:
    root: Path
    lock: Path
    queue: Path

    @classmethod
    def for_root(cls, root: Path) -> "WatchPaths":
        wk = root / ".wiki-knowledge"
        return cls(root=root, lock=wk / "watch.lock", queue=wk / "watch-queue.jsonl")


# --- CLI -----------------------------------------------------------------------


def _main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", default=None, help="vault root; defaults to resolve_vault_root()")
    parser.add_argument("--debounce", type=float, default=DEFAULT_DEBOUNCE_SECONDS, help="per-file debounce, seconds")
    parser.add_argument(
        "--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="how often to check for settled files, seconds",
    )
    args = parser.parse_args(argv)

    root = Path(args.vault).resolve() if args.vault else vault_mod.resolve_vault_root()
    paths = WatchPaths.for_root(root)

    if not acquire_lock(paths.lock):
        print(f"another watcher is already running (lock at {paths.lock})")
        return 1

    stop_event = threading.Event()

    def _handle_signal(signum, frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    raw_root = root / "raw"
    raw_root.mkdir(parents=True, exist_ok=True)

    debouncer = Debouncer(args.debounce)
    observer = Observer()
    observer.schedule(_RawEventHandler(root, debouncer), str(raw_root), recursive=True)
    observer.start()

    print(f"watching {raw_root} (debounce={args.debounce}s, pid={os.getpid()})")

    try:
        while not stop_event.is_set():
            settled = debouncer.settled_files()
            if settled:
                try:
                    result = ingest_scan.scan(root)
                    eligible_rels = {c.raw_rel for c in result.eligible}
                except Exception as exc:  # noqa: BLE001 - log and keep watching
                    print(f"error scanning raw/: {exc}")
                    eligible_rels = set()
                for rel in settled:
                    try:
                        if check_and_enqueue(eligible_rels, rel, paths.queue):
                            print(f"queued {rel}")
                    except Exception as exc:  # noqa: BLE001 - log and keep watching
                        print(f"error queuing {rel}: {exc}")
            stop_event.wait(args.poll_interval)
    finally:
        observer.stop()
        observer.join()
        remove_lock(paths.lock)
        print("watcher stopped")

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
