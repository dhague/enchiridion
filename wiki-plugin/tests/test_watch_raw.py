"""TDD for ``watch_raw.py`` — the raw/ watcher's debounce/lock/queue plumbing (#62, per #37).

The watcher itself (a live ``watchdog`` observer, a signal-driven main loop)
is exercised at the unit level via its pure/injectable pieces
(:class:`~watch_raw.Debouncer`, the lock functions, the queue functions);
the SIGTERM test spawns the real CLI as a subprocess since that's the only
way to exercise signal handling honestly.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from watchdog.events import FileCreatedEvent, FileMovedEvent

import watch_raw
from watch_raw import (
    Debouncer,
    _RawEventHandler,
    acquire_lock,
    append_queue,
    read_queue,
    remove_from_queue,
    remove_lock,
    write_lock,
)


# --- debounce timing -----------------------------------------------------


def test_debounce_not_settled_within_window():
    """N events spread over 25s (< the 30s default) never settle."""
    now = [0.0]
    debouncer = Debouncer(debounce_seconds=30.0, clock=lambda: now[0])

    for t in (0.0, 5.0, 10.0, 15.0, 20.0, 25.0):
        now[0] = t
        debouncer.record_event("raw/notes/a.md")

    assert debouncer.settled_files() == []


def test_debounce_settles_after_final_silence():
    """Events over 35s, then 30s of silence, settles the file."""
    now = [0.0]
    debouncer = Debouncer(debounce_seconds=30.0, clock=lambda: now[0])

    for t in (0.0, 10.0, 20.0, 35.0):
        now[0] = t
        debouncer.record_event("raw/notes/a.md")

    now[0] = 35.0 + 29.0
    assert debouncer.settled_files() == []

    now[0] = 35.0 + 30.0
    assert debouncer.settled_files() == ["raw/notes/a.md"]


def test_debounce_settled_files_stop_being_tracked():
    now = [0.0]
    debouncer = Debouncer(debounce_seconds=10.0, clock=lambda: now[0])
    debouncer.record_event("raw/a.md")
    now[0] = 10.0
    assert debouncer.settled_files() == ["raw/a.md"]
    # already-settled file isn't re-reported without a new event
    now[0] = 100.0
    assert debouncer.settled_files() == []


def test_debounce_is_per_file():
    now = [0.0]
    debouncer = Debouncer(debounce_seconds=30.0, clock=lambda: now[0])
    debouncer.record_event("raw/a.md")
    now[0] = 15.0
    debouncer.record_event("raw/b.md")  # a flurry on a different file
    now[0] = 30.0
    # a.md has been silent for 30s; b.md has only been silent for 15s
    assert debouncer.settled_files() == ["raw/a.md"]


# --- lock file lifecycle ---------------------------------------------------


def test_write_lock_then_remove(tmp_path):
    lock_path = tmp_path / ".wiki-knowledge" / "watch.lock"
    write_lock(lock_path, pid=1234)
    assert lock_path.exists()
    payload = json.loads(lock_path.read_text())
    assert payload["pid"] == 1234
    remove_lock(lock_path)
    assert not lock_path.exists()


def test_remove_lock_missing_is_a_noop(tmp_path):
    lock_path = tmp_path / ".wiki-knowledge" / "watch.lock"
    remove_lock(lock_path)  # doesn't raise


def test_acquire_lock_live_pid_bails(tmp_path):
    lock_path = tmp_path / ".wiki-knowledge" / "watch.lock"
    write_lock(lock_path, pid=os.getpid())  # our own pid is definitely alive
    assert acquire_lock(lock_path) is False
    # the live lock is left untouched
    assert json.loads(lock_path.read_text())["pid"] == os.getpid()


def test_acquire_lock_dead_pid_is_stale(tmp_path, capsys):
    lock_path = tmp_path / ".wiki-knowledge" / "watch.lock"
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_pid = proc.pid
    proc.wait()  # exited already, but the pid is a real recently-used one
    write_lock(lock_path, pid=dead_pid)

    assert acquire_lock(lock_path) is True
    assert "stale lock" in capsys.readouterr().out
    # replaced with this process's own lock
    assert json.loads(lock_path.read_text())["pid"] == os.getpid()


def test_acquire_lock_old_timestamp_is_stale(tmp_path):
    lock_path = tmp_path / ".wiki-knowledge" / "watch.lock"
    old = datetime.now(timezone.utc) - timedelta(minutes=11)
    write_lock(lock_path, pid=os.getpid(), started_at=old)  # alive pid, but stale timestamp

    assert acquire_lock(lock_path) is True


def test_acquire_lock_recent_timestamp_not_stale(tmp_path):
    lock_path = tmp_path / ".wiki-knowledge" / "watch.lock"
    recent = datetime.now(timezone.utc) - timedelta(minutes=9)
    write_lock(lock_path, pid=os.getpid(), started_at=recent)

    assert acquire_lock(lock_path) is False


def test_acquire_lock_no_existing_lock_succeeds(tmp_path):
    lock_path = tmp_path / ".wiki-knowledge" / "watch.lock"
    assert acquire_lock(lock_path) is True
    assert lock_path.exists()


def test_acquire_lock_unparsable_lock_is_stale(tmp_path):
    lock_path = tmp_path / ".wiki-knowledge" / "watch.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("not json", encoding="utf-8")

    assert acquire_lock(lock_path) is True


def test_concurrent_stale_takeover_only_one_winner(tmp_path):
    """Two racing takeovers of the same stale lock (#67): exactly one must
    win. Without the mutex, both threads can pass ``_lock_is_stale`` before
    either has written its own lock, and both would return ``True``."""
    lock_path = tmp_path / ".wiki-knowledge" / "watch.lock"
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_pid = proc.pid
    proc.wait()
    write_lock(lock_path, pid=dead_pid)

    results: list[bool] = []
    barrier = threading.Barrier(8)

    def _race():
        barrier.wait()
        results.append(acquire_lock(lock_path))

    threads = [threading.Thread(target=_race) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    assert json.loads(lock_path.read_text())["pid"] == os.getpid()


# --- queue atomicity --------------------------------------------------------


def test_append_queue_creates_and_appends(tmp_path):
    queue_path = tmp_path / ".wiki-knowledge" / "watch-queue.jsonl"
    append_queue(queue_path, "raw/a.md")
    append_queue(queue_path, "raw/b.md")
    assert read_queue(queue_path) == ["raw/a.md", "raw/b.md"]


def test_append_queue_is_idempotent(tmp_path):
    queue_path = tmp_path / ".wiki-knowledge" / "watch-queue.jsonl"
    append_queue(queue_path, "raw/a.md")
    append_queue(queue_path, "raw/a.md")
    assert read_queue(queue_path) == ["raw/a.md"]


def test_remove_from_queue(tmp_path):
    queue_path = tmp_path / ".wiki-knowledge" / "watch-queue.jsonl"
    append_queue(queue_path, "raw/a.md")
    append_queue(queue_path, "raw/b.md")
    remove_from_queue(queue_path, "raw/a.md")
    assert read_queue(queue_path) == ["raw/b.md"]


def test_read_queue_missing_file_is_empty(tmp_path):
    queue_path = tmp_path / ".wiki-knowledge" / "watch-queue.jsonl"
    assert read_queue(queue_path) == []


# --- eligibility check on settle ---------------------------------------------


def test_check_and_enqueue_enqueues_when_eligible(tmp_path):
    queue_path = tmp_path / ".wiki-knowledge" / "watch-queue.jsonl"
    assert watch_raw.check_and_enqueue({"raw/a.md"}, "raw/a.md", queue_path) is True
    assert read_queue(queue_path) == ["raw/a.md"]


def test_check_and_enqueue_skips_when_not_eligible(tmp_path):
    queue_path = tmp_path / ".wiki-knowledge" / "watch-queue.jsonl"
    assert watch_raw.check_and_enqueue({"raw/other.md"}, "raw/a.md", queue_path) is False
    assert read_queue(queue_path) == []


def test_check_and_enqueue_takes_no_vault_root(tmp_path):
    """`check_and_enqueue` no longer runs its own scan (#66) — it only
    consults the eligible set the caller already computed for this batch."""
    import inspect

    params = list(inspect.signature(watch_raw.check_and_enqueue).parameters)
    assert params == ["eligible_rels", "settled_rel", "queue_path"]


@settings(deadline=None, max_examples=25)
@given(st.lists(
    st.text(alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7e), min_size=1, max_size=20),
    min_size=2, max_size=12, unique=True,
))
def test_concurrent_appends_dont_corrupt_queue(tmp_path_factory, rels):
    """Threads appending distinct entries concurrently all land, none is lost or mangled."""
    queue_path = tmp_path_factory.mktemp("watch-queue") / ".wiki-knowledge" / "watch-queue.jsonl"

    threads = [threading.Thread(target=append_queue, args=(queue_path, rel)) for rel in rels]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = read_queue(queue_path)
    assert set(lines) == set(rels)
    assert len(lines) == len(set(rels))  # no duplicated or mangled lines


# --- event handler -----------------------------------------------------------


def test_handler_records_created_event_by_src_path(tmp_path):
    debouncer = Debouncer(clock=lambda: 0.0)
    handler = _RawEventHandler(tmp_path, debouncer)

    event = FileCreatedEvent(str(tmp_path / "raw" / "note.md"))
    handler.on_any_event(event)

    assert "raw/note.md" in debouncer._last_event


def test_handler_records_moved_event_by_dest_path_not_src_path(tmp_path):
    """Atomic saves (vim/VSCode/Obsidian) surface as one moved event: the
    real file is at ``dest_path``, ``src_path`` is a temp file that's
    already gone. Debouncing the temp path would silently drop the save (#65)."""
    debouncer = Debouncer(clock=lambda: 0.0)
    handler = _RawEventHandler(tmp_path, debouncer)

    event = FileMovedEvent(
        str(tmp_path / "raw" / ".note.md.tmp12345"),
        str(tmp_path / "raw" / "note.md"),
    )
    handler.on_any_event(event)

    assert "raw/note.md" in debouncer._last_event
    assert "raw/.note.md.tmp12345" not in debouncer._last_event


def test_handler_ignores_directory_events(tmp_path):
    debouncer = Debouncer(clock=lambda: 0.0)
    handler = _RawEventHandler(tmp_path, debouncer)

    event = FileCreatedEvent(str(tmp_path / "raw" / "subdir"))
    event.is_directory = True
    handler.on_any_event(event)

    assert debouncer._last_event == {}


# --- CLI: --dequeue ----------------------------------------------------------


def test_cli_dequeue_removes_entry(tmp_path):
    queue_path = tmp_path / ".wiki-knowledge" / "watch-queue.jsonl"
    append_queue(queue_path, "raw/a.md")
    append_queue(queue_path, "raw/b.md")

    rc = watch_raw._main(["--vault", str(tmp_path), "--dequeue", "raw/a.md"])
    assert rc == 0
    assert read_queue(queue_path) == ["raw/b.md"]


def test_cli_dequeue_missing_entry_is_a_noop(tmp_path):
    queue_path = tmp_path / ".wiki-knowledge" / "watch-queue.jsonl"
    append_queue(queue_path, "raw/b.md")

    rc = watch_raw._main(["--vault", str(tmp_path), "--dequeue", "raw/a.md"])
    assert rc == 0
    assert read_queue(queue_path) == ["raw/b.md"]


# --- SIGTERM graceful shutdown ----------------------------------------------


def test_sigterm_stops_observer_and_removes_lock_and_exits_zero(tmp_path):
    root = tmp_path
    (root / "raw").mkdir(parents=True)
    lock_path = root / ".wiki-knowledge" / "watch.lock"
    script = Path(watch_raw.__file__)

    proc = subprocess.Popen(
        [sys.executable, str(script), "--vault", str(root), "--poll-interval", "0.2"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not lock_path.exists():
            time.sleep(0.1)
        assert lock_path.exists(), "watcher never wrote its lock file"

        proc.send_signal(signal.SIGTERM)
        returncode = proc.wait(timeout=10)

        assert returncode == 0
        assert not lock_path.exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
