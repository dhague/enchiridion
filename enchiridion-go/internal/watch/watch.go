// Package watch is the raw/ watcher — event-driven detection + debounce +
// queue. Ported from `wiki-plugin/scripts/watch_raw.py`.
//
// The `/wiki-watch` skill orchestrates; this is the half it launches in the
// background and polls. Three pieces:
//
//   - [Debouncer] — per-file debounce, pure (injectable clock, no threads, no
//     filesystem) so the timing is testable without real sleeps.
//   - The lock file at `.wiki-knowledge/watch.lock` — one watcher per vault,
//     with stale-lock recovery for a hard-killed predecessor.
//   - The queue file at `.wiki-knowledge/watch-queue.jsonl` — one
//     vault-relative path per line (despite the extension, not JSON). A
//     wake-up signal and nothing more: SKILL.md re-checks the sweep when it
//     needs the reason.
package watch

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// DefaultDebounceSeconds is the per-file settle window.
const DefaultDebounceSeconds = 30.0

// StaleLockSeconds is how long a live-PID lock is trusted before it counts as
// stale.
const StaleLockSeconds = 600

// DefaultPollIntervalSeconds is how often the main loop checks for settled
// files.
const DefaultPollIntervalSeconds = 5.0

// Debouncer tracks the most recent event time per vault-relative file path.
// clock defaults to a monotonic seconds source, injectable so tests can drive
// settling with fake timestamps.
type Debouncer struct {
	debounceSeconds float64
	clock           func() float64
	lastEvent       map[string]float64
}

// NewDebouncer returns a Debouncer with the given settle window. A nil clock
// uses a monotonic source anchored at construction.
func NewDebouncer(debounceSeconds float64, clock func() float64) *Debouncer {
	if clock == nil {
		start := time.Now()
		clock = func() float64 { return time.Since(start).Seconds() }
	}
	return &Debouncer{debounceSeconds: debounceSeconds, clock: clock, lastEvent: map[string]float64{}}
}

// RecordEvent notes an event for rel at the current clock time.
func (d *Debouncer) RecordEvent(rel string) {
	d.lastEvent[rel] = d.clock()
}

// SettledFiles returns, and stops tracking, every file whose debounce window
// has elapsed.
func (d *Debouncer) SettledFiles() []string {
	now := d.clock()
	var settled []string
	for rel, last := range d.lastEvent {
		if now-last >= d.debounceSeconds {
			settled = append(settled, rel)
		}
	}
	for _, rel := range settled {
		delete(d.lastEvent, rel)
	}
	return settled
}

// LastEvent returns the recorded event time for rel, for tests that want to
// assert what the handler recorded.
func (d *Debouncer) LastEvent(rel string) (float64, bool) {
	t, ok := d.lastEvent[rel]
	return t, ok
}

// --- lock file ---------------------------------------------------------------

// WriteLock writes lockPath with the given (or current) PID and timestamp.
func WriteLock(lockPath string, pid int, startedAt time.Time) error {
	if err := os.MkdirAll(filepath.Dir(lockPath), 0o755); err != nil {
		return err
	}
	if pid == 0 {
		pid = os.Getpid()
	}
	if startedAt.IsZero() {
		startedAt = time.Now().UTC()
	}
	payload := map[string]any{
		"pid":        pid,
		"started_at": startedAt.UTC().Format(time.RFC3339Nano),
	}
	data, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	return os.WriteFile(lockPath, data, 0o644)
}

// RemoveLock unlinks lockPath; a no-op when absent.
func RemoveLock(lockPath string) error {
	if err := os.Remove(lockPath); err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

// lockIsStale reports (isStale, pid) for the lock at lockPath. pid is nil when
// the lock file is unparsable.
//
// An unparsable lock file counts as stale (fails toward proceeding, not toward
// a permanent bail).
func lockIsStale(lockPath string, now time.Time, pidAlive func(int) bool) (bool, *int) {
	data, err := os.ReadFile(lockPath)
	if err != nil {
		return true, nil
	}
	var payload struct {
		PID       int    `json:"pid"`
		StartedAt string `json:"started_at"`
	}
	if err := json.Unmarshal(data, &payload); err != nil {
		return true, nil
	}
	startedAt, err := time.Parse(time.RFC3339Nano, payload.StartedAt)
	if err != nil {
		return true, nil
	}
	pid := payload.PID
	if !pidAlive(pid) {
		return true, &pid
	}
	if now.Sub(startedAt).Seconds() > StaleLockSeconds {
		return true, &pid
	}
	return false, &pid
}

// AcquireLock tries to acquire the watch lock. Returns (acquired, stalePID,
// error).
//
// A live lock (PID alive, within [StaleLockSeconds]) means another watcher is
// running: returns (false, nil, nil), lock untouched. A stale one is removed
// and replaced; the removed lock's PID is returned (nil when the lock file
// was unparsable), so the caller can log the takeover. Check, unlink and
// write all happen under a companion `.mutex` file held with an exclusive
// lock, so two processes racing a stale takeover can't both pass the
// staleness check before either writes.
func AcquireLock(lockPath string, now time.Time, pidAlive func(int) bool) (bool, *int, error) {
	if now.IsZero() {
		now = time.Now().UTC()
	}
	if pidAlive == nil {
		pidAlive = defaultPIDAlive
	}

	if err := os.MkdirAll(filepath.Dir(lockPath), 0o755); err != nil {
		return false, nil, err
	}
	mutexPath := lockPath + ".mutex"
	mutex, err := os.OpenFile(mutexPath, os.O_CREATE|os.O_RDWR, 0o644)
	if err != nil {
		return false, nil, err
	}
	defer mutex.Close()
	if err := exclusiveLock(mutex); err != nil {
		return false, nil, err
	}
	defer exclusiveUnlock(mutex) //nolint:errcheck

	if _, err := os.Stat(lockPath); err == nil {
		stale, pid := lockIsStale(lockPath, now, pidAlive)
		if !stale {
			return false, nil, nil
		}
		if err := os.Remove(lockPath); err != nil {
			return false, nil, err
		}
		if err := WriteLock(lockPath, 0, now); err != nil {
			return false, nil, err
		}
		return true, pid, nil
	}

	return true, nil, WriteLock(lockPath, 0, now)
}

// --- queue file --------------------------------------------------------------

// ReadQueue returns the queue's entries. An absent queue is empty.
//
// Split on "\n", never anything fancier — a path can legitimately contain
// other control bytes.
func ReadQueue(queuePath string) ([]string, error) {
	data, err := os.ReadFile(queuePath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	var out []string
	for _, line := range strings.Split(string(data), "\n") {
		if line != "" {
			out = append(out, line)
		}
	}
	return out, nil
}

// withQueueLock runs fn(currentLines) -> newLines under an exclusive file
// lock.
//
// The lock serializes concurrent writers so a read-modify-write can't lose an
// update; writing to a `.tmp` sibling and renaming means a concurrent reader
// never sees a partial write.
func withQueueLock(queuePath string, fn func([]string) []string) error {
	if err := os.MkdirAll(filepath.Dir(queuePath), 0o755); err != nil {
		return err
	}
	writelockPath := queuePath + ".writelock"
	lockFile, err := os.OpenFile(writelockPath, os.O_CREATE|os.O_RDWR, 0o644)
	if err != nil {
		return err
	}
	defer lockFile.Close()
	if err := exclusiveLock(lockFile); err != nil {
		return err
	}
	defer exclusiveUnlock(lockFile) //nolint:errcheck

	existing, err := ReadQueue(queuePath)
	if err != nil {
		return err
	}
	newLines := fn(existing)

	var b strings.Builder
	for _, line := range newLines {
		b.WriteString(line)
		b.WriteString("\n")
	}
	tmpPath := queuePath + ".tmp"
	if err := os.WriteFile(tmpPath, []byte(b.String()), 0o644); err != nil {
		return err
	}
	return os.Rename(tmpPath, queuePath)
}

// AppendQueue appends rel to the queue, unless it's already there
// (idempotent).
func AppendQueue(queuePath, rel string) error {
	return withQueueLock(queuePath, func(lines []string) []string {
		for _, line := range lines {
			if line == rel {
				return lines
			}
		}
		return append(lines, rel)
	})
}

// RemoveFromQueue removes every occurrence of rel from the queue.
func RemoveFromQueue(queuePath, rel string) error {
	return withQueueLock(queuePath, func(lines []string) []string {
		var out []string
		for _, line := range lines {
			if line != rel {
				out = append(out, line)
			}
		}
		return out
	})
}

// --- eligibility check on settle ----------------------------------------------

// CheckAndEnqueue enqueues settledRel iff it's in eligibleRels.
//
// A settled event doesn't mean "ingest this" — an `.ingestignore` match, or a
// file whose back-pointer page is already current, settles too. eligibleRels
// comes from one sweep per poll tick, not per file, so eligibility matches the
// manual sweep exactly.
func CheckAndEnqueue(eligibleRels map[string]bool, settledRel, queuePath string) (bool, error) {
	if !eligibleRels[settledRel] {
		return false, nil
	}
	if err := AppendQueue(queuePath, settledRel); err != nil {
		return false, err
	}
	return true, nil
}

// --- watch paths --------------------------------------------------------------

// Paths is the set of files one watcher run touches.
type Paths struct {
	Root  string
	Lock  string
	Queue string
}

// ForRoot returns the watch paths for a vault root.
func ForRoot(root string) Paths {
	wk := filepath.Join(root, ".wiki-knowledge")
	return Paths{
		Root:  root,
		Lock:  filepath.Join(wk, "watch.lock"),
		Queue: filepath.Join(wk, "watch-queue.jsonl"),
	}
}

// RelForEvent maps one filesystem event path to a vault-relative path, or
// ("", false) when the event should be ignored: a directory, or a path outside
// root. The fsnotify handler calls this per event before debouncing.
func RelForEvent(root, path string) (string, bool) {
	if info, err := os.Stat(path); err != nil || info.IsDir() {
		return "", false
	}
	rel, err := filepath.Rel(root, path)
	if err != nil {
		return "", false
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", false
	}
	return filepath.ToSlash(rel), true
}
