"""The raw/ watcher — event-driven detection + debounce + queue.

The `/wiki-watch` skill orchestrates; this is the half it launches in the
background and polls. Three pieces:

* :class:`Debouncer` — per-file debounce, pure (injectable clock, no threads,
  no filesystem) so the timing is testable without real sleeps.
* The lock file at ``.wiki-knowledge/watch.lock`` — one watcher per vault,
  with stale-lock recovery for a hard-killed predecessor.
* The queue file at ``.wiki-knowledge/watch-queue.jsonl`` — one
  vault-relative path per line (despite the extension, not JSON). A wake-up
  signal and nothing more: SKILL.md re-checks ``ingest_scan.py`` when it
  needs the reason.

CLI::

    python watch_raw.py [--vault <root>] [--debounce 30]
    python watch_raw.py [--vault <root>] --dequeue <raw_rel>
"""
from __future__ import annotations

import contextlib
import json
import os
import signal
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, IO

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

import ingest_scan
import vault as vault_mod

DEFAULT_DEBOUNCE_SECONDS = 30.0
STALE_LOCK_SECONDS = 600  # 10 minutes
DEFAULT_POLL_INTERVAL_SECONDS = 5.0


# --- cross-platform exclusive file lock ---------------------------------------
#
# ``fcntl.flock`` doesn't exist on Windows; ``msvcrt.locking`` is the closest
# equivalent. One byte suffices — the lock is only ever held across a
# read-modify-write, never over file content.

if sys.platform == "win32":
    import msvcrt

    @contextlib.contextmanager
    def _exclusive_lock(fileobj: IO[str]):
        # ``msvcrt.locking`` refuses to lock a region past end-of-file, so an
        # empty (just-truncated) lock/mutex file needs a byte written first.
        fileobj.seek(0, os.SEEK_END)
        if fileobj.tell() == 0:
            fileobj.write(" ")
            fileobj.flush()
        fileobj.seek(0)
        msvcrt.locking(fileobj.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            fileobj.seek(0)
            msvcrt.locking(fileobj.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    @contextlib.contextmanager
    def _exclusive_lock(fileobj: IO[str]):
        fcntl.flock(fileobj, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fileobj, fcntl.LOCK_UN)


# --- debounce --------------------------------------------------------------


class Debouncer:
    """Tracks the most recent event time per vault-relative file path.
    ``clock`` defaults to :func:`time.monotonic`, injectable so tests can
    drive settling with fake timestamps."""

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


if sys.platform == "win32":
    import ctypes

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def _pid_alive(pid: int) -> bool:
        # NOT ``os.kill(pid, 0)``: on Windows, for any signal other than
        # CTRL_C_EVENT/CTRL_BREAK_EVENT, that calls TerminateProcess — it
        # would kill the live process it is supposed to be probing.
        handle = ctypes.windll.kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
else:

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
    """Try to acquire the watch lock. ``True`` iff acquired.

    A live lock (PID alive, within :data:`STALE_LOCK_SECONDS`) means another
    watcher is running: returns ``False``, lock untouched. A stale one is
    logged, removed, and replaced.

    Check, unlink and write all happen under a companion ``.mutex`` file
    held with an exclusive ``flock``, so two processes racing a stale
    takeover can't both pass the staleness check before either writes.
    """
    now = now or datetime.now(timezone.utc)
    pid_alive = pid_alive or _pid_alive

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    mutex_path = lock_path.with_name(lock_path.name + ".mutex")
    with open(mutex_path, "w", encoding="utf-8") as mutex, _exclusive_lock(mutex):
        if lock_path.exists():
            stale, pid = _lock_is_stale(lock_path, now, pid_alive)
            if not stale:
                return False
            print(f"previous watcher exited without cleanup, removing stale lock (pid={pid})")
            lock_path.unlink()

        write_lock(lock_path, started_at=now)
        return True


# --- queue file ---------------------------------------------------------------


def _queue_writelock_path(queue_path: Path) -> Path:
    return queue_path.with_name(queue_path.name + ".writelock")


def _with_queue_lock(queue_path: Path, fn: Callable[[list[str]], list[str]]) -> None:
    """Run ``fn(current_lines) -> new_lines`` under an exclusive file lock.

    The lock serializes concurrent writers so a read-modify-write can't lose
    an update; writing to a ``.tmp`` sibling and renaming means a concurrent
    reader never sees a partial write.
    """
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    writelock_path = _queue_writelock_path(queue_path)
    with open(writelock_path, "w", encoding="utf-8") as lockfile, _exclusive_lock(lockfile):
        existing = read_queue(queue_path)
        new_lines = fn(existing)
        tmp_path = queue_path.with_name(queue_path.name + ".tmp")
        content = "".join(line + "\n" for line in new_lines)
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(queue_path)


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
    # ``split("\n")``, never ``splitlines()``: the latter also breaks on
    # \x0b/\x0c/\x1c-\x1e, any of which can legitimately appear in a path.
    return [line for line in queue_path.read_text(encoding="utf-8").split("\n") if line]


# --- eligibility check on settle ----------------------------------------------


def check_and_enqueue(eligible_rels: set[str], settled_rel: str, queue_path: Path) -> bool:
    """Enqueue ``settled_rel`` iff it's in ``eligible_rels``.

    A settled event doesn't mean "ingest this" — an ``.ingestignore`` match,
    or a file whose back-pointer page is already current, settles too.
    ``eligible_rels`` comes from one ``ingest_scan.scan`` per poll tick, not
    per file, so eligibility matches the manual sweep exactly.
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

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        # Atomic saves (vim, VSCode, Obsidian write-temp-then-rename) arrive
        # as one moved event: dest_path is the real file, src_path the temp
        # one, already gone by the time we'd re-check it.
        path = getattr(event, "dest_path", "") or event.src_path or ""
        path = path.decode() if isinstance(path, bytes) else path
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
    parser.add_argument(
        "--dequeue",
        metavar="RAW_REL",
        help="remove this vault-relative path from the watch queue and exit, instead of watching",
    )
    args = parser.parse_args(argv)

    root = Path(args.vault).resolve() if args.vault else vault_mod.resolve_vault_root()
    paths = WatchPaths.for_root(root)

    if args.dequeue is not None:
        remove_from_queue(paths.queue, args.dequeue)
        return 0

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
