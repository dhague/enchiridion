package watch

import (
	"os"
	"path/filepath"
	"reflect"
	"sync"
	"testing"
	"time"
)

// --- debounce timing ---------------------------------------------------------

func TestDebounceNotSettledWithinWindow(t *testing.T) {
	now := 0.0
	d := NewDebouncer(30.0, func() float64 { return now })

	for _, ts := range []float64{0.0, 5.0, 10.0, 15.0, 20.0, 25.0} {
		now = ts
		d.RecordEvent("raw/notes/a.md")
	}
	if got := d.SettledFiles(); len(got) != 0 {
		t.Errorf("settled = %v, want empty", got)
	}
}

func TestDebounceSettlesAfterFinalSilence(t *testing.T) {
	now := 0.0
	d := NewDebouncer(30.0, func() float64 { return now })

	for _, ts := range []float64{0.0, 10.0, 20.0, 35.0} {
		now = ts
		d.RecordEvent("raw/notes/a.md")
	}
	now = 35.0 + 29.0
	if got := d.SettledFiles(); len(got) != 0 {
		t.Errorf("settled early = %v", got)
	}
	now = 35.0 + 30.0
	if got := d.SettledFiles(); !reflect.DeepEqual(got, []string{"raw/notes/a.md"}) {
		t.Errorf("settled = %v", got)
	}
}

func TestDebounceSettledFilesStopBeingTracked(t *testing.T) {
	now := 0.0
	d := NewDebouncer(10.0, func() float64 { return now })
	d.RecordEvent("raw/a.md")
	now = 10.0
	if got := d.SettledFiles(); !reflect.DeepEqual(got, []string{"raw/a.md"}) {
		t.Errorf("first = %v", got)
	}
	now = 100.0
	if got := d.SettledFiles(); len(got) != 0 {
		t.Errorf("re-reported without a new event: %v", got)
	}
}

func TestDebounceIsPerFile(t *testing.T) {
	now := 0.0
	d := NewDebouncer(30.0, func() float64 { return now })
	d.RecordEvent("raw/a.md")
	now = 15.0
	d.RecordEvent("raw/b.md")
	now = 30.0
	if got := d.SettledFiles(); !reflect.DeepEqual(got, []string{"raw/a.md"}) {
		t.Errorf("settled = %v, want only a.md", got)
	}
}

// --- lock file lifecycle -----------------------------------------------------

func TestWriteLockThenRemove(t *testing.T) {
	lockPath := filepath.Join(t.TempDir(), ".wiki-knowledge", "watch.lock")
	if err := WriteLock(lockPath, 1234, time.Time{}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(lockPath); err != nil {
		t.Fatalf("lock not written: %v", err)
	}
	if err := RemoveLock(lockPath); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(lockPath); !os.IsNotExist(err) {
		t.Error("lock not removed")
	}
}

func TestRemoveLockMissingIsANoop(t *testing.T) {
	if err := RemoveLock(filepath.Join(t.TempDir(), "watch.lock")); err != nil {
		t.Fatal(err)
	}
}

func TestAcquireLockLivePIDBails(t *testing.T) {
	lockPath := filepath.Join(t.TempDir(), ".wiki-knowledge", "watch.lock")
	if err := WriteLock(lockPath, os.Getpid(), time.Time{}); err != nil {
		t.Fatal(err)
	}
	ok, _, err := AcquireLock(lockPath, time.Time{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if ok {
		t.Fatal("live lock reported acquired")
	}
}

func TestAcquireLockOldTimestampIsStale(t *testing.T) {
	lockPath := filepath.Join(t.TempDir(), ".wiki-knowledge", "watch.lock")
	old := time.Now().UTC().Add(-11 * time.Minute)
	if err := WriteLock(lockPath, os.Getpid(), old); err != nil {
		t.Fatal(err)
	}
	ok, _, err := AcquireLock(lockPath, time.Time{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !ok {
		t.Fatal("stale (old timestamp) lock not acquired")
	}
}

func TestAcquireLockRecentTimestampNotStale(t *testing.T) {
	lockPath := filepath.Join(t.TempDir(), ".wiki-knowledge", "watch.lock")
	recent := time.Now().UTC().Add(-9 * time.Minute)
	if err := WriteLock(lockPath, os.Getpid(), recent); err != nil {
		t.Fatal(err)
	}
	ok, _, err := AcquireLock(lockPath, time.Time{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if ok {
		t.Fatal("recent live lock reported acquired")
	}
}

func TestAcquireLockNoExistingLockSucceeds(t *testing.T) {
	lockPath := filepath.Join(t.TempDir(), ".wiki-knowledge", "watch.lock")
	ok, _, err := AcquireLock(lockPath, time.Time{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !ok {
		t.Fatal("empty vault did not acquire")
	}
}

func TestAcquireLockUnparsableLockIsStale(t *testing.T) {
	lockPath := filepath.Join(t.TempDir(), ".wiki-knowledge", "watch.lock")
	if err := os.MkdirAll(filepath.Dir(lockPath), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(lockPath, []byte("not json"), 0o644); err != nil {
		t.Fatal(err)
	}
	ok, _, err := AcquireLock(lockPath, time.Time{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !ok {
		t.Fatal("unparsable lock not acquired")
	}
}

func TestAcquireLockDeadPIDIsStale(t *testing.T) {
	lockPath := filepath.Join(t.TempDir(), ".wiki-knowledge", "watch.lock")
	// A pid that is (almost certainly) not alive on any test host.
	if err := WriteLock(lockPath, 1<<30, time.Time{}); err != nil {
		t.Fatal(err)
	}
	ok, stalePID, err := AcquireLock(lockPath, time.Time{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !ok {
		t.Fatal("dead-pid lock not acquired")
	}
	if stalePID == nil || *stalePID != 1<<30 {
		t.Errorf("stalePID = %v, want the removed lock's pid", stalePID)
	}
}

func TestAcquireLockConcurrentStaleTakeoverOnlyOneWinner(t *testing.T) {
	lockPath := filepath.Join(t.TempDir(), ".wiki-knowledge", "watch.lock")
	if err := WriteLock(lockPath, 1<<30, time.Time{}); err != nil {
		t.Fatal(err)
	}

	const n = 8
	start := make(chan struct{})
	var mu sync.Mutex
	var winners []bool
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			ok, _, err := AcquireLock(lockPath, time.Time{}, nil)
			if err != nil {
				t.Errorf("AcquireLock: %v", err)
				return
			}
			mu.Lock()
			winners = append(winners, ok)
			mu.Unlock()
		}()
	}
	close(start)
	wg.Wait()

	count := 0
	for _, w := range winners {
		if w {
			count++
		}
	}
	if count != 1 {
		t.Errorf("%d winners, want exactly 1", count)
	}
}

// --- queue -------------------------------------------------------------------

func TestQueueAppendCreatesAndAppends(t *testing.T) {
	queuePath := filepath.Join(t.TempDir(), ".wiki-knowledge", "watch-queue.jsonl")
	if err := AppendQueue(queuePath, "raw/a.md"); err != nil {
		t.Fatal(err)
	}
	if err := AppendQueue(queuePath, "raw/b.md"); err != nil {
		t.Fatal(err)
	}
	got, err := ReadQueue(queuePath)
	if err != nil {
		t.Fatal(err)
	}
	if want := []string{"raw/a.md", "raw/b.md"}; !reflect.DeepEqual(got, want) {
		t.Errorf("queue = %v, want %v", got, want)
	}
}

func TestQueueAppendIsIdempotent(t *testing.T) {
	queuePath := filepath.Join(t.TempDir(), "watch-queue.jsonl")
	if err := AppendQueue(queuePath, "raw/a.md"); err != nil {
		t.Fatal(err)
	}
	if err := AppendQueue(queuePath, "raw/a.md"); err != nil {
		t.Fatal(err)
	}
	got, _ := ReadQueue(queuePath)
	if want := []string{"raw/a.md"}; !reflect.DeepEqual(got, want) {
		t.Errorf("queue = %v, want %v", got, want)
	}
}

func TestQueueRemove(t *testing.T) {
	queuePath := filepath.Join(t.TempDir(), "watch-queue.jsonl")
	for _, rel := range []string{"raw/a.md", "raw/b.md"} {
		if err := AppendQueue(queuePath, rel); err != nil {
			t.Fatal(err)
		}
	}
	if err := RemoveFromQueue(queuePath, "raw/a.md"); err != nil {
		t.Fatal(err)
	}
	got, _ := ReadQueue(queuePath)
	if want := []string{"raw/b.md"}; !reflect.DeepEqual(got, want) {
		t.Errorf("queue = %v, want %v", got, want)
	}
}

func TestQueueReadMissingFileIsEmpty(t *testing.T) {
	got, err := ReadQueue(filepath.Join(t.TempDir(), "watch-queue.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 0 {
		t.Errorf("queue = %v, want empty", got)
	}
}

func TestQueueConcurrentAppendsDoNotCorrupt(t *testing.T) {
	queuePath := filepath.Join(t.TempDir(), ".wiki-knowledge", "watch-queue.jsonl")
	rels := []string{"raw/a.md", "raw/b.md", "raw/c.md", "raw/d.md", "raw/e.md", "raw/f.md"}
	var wg sync.WaitGroup
	for _, rel := range rels {
		wg.Add(1)
		go func(rel string) {
			defer wg.Done()
			_ = AppendQueue(queuePath, rel)
		}(rel)
	}
	wg.Wait()

	got, _ := ReadQueue(queuePath)
	if len(got) != len(rels) {
		t.Errorf("queue has %d entries, want %d (lost or mangled): %v", len(got), len(rels), got)
	}
	seen := map[string]bool{}
	for _, line := range got {
		if seen[line] {
			t.Errorf("duplicate line %q", line)
		}
		seen[line] = true
	}
}

// --- eligibility check on settle ----------------------------------------------

func TestCheckAndEnqueueEnqueuesWhenEligible(t *testing.T) {
	queuePath := filepath.Join(t.TempDir(), "watch-queue.jsonl")
	ok, err := CheckAndEnqueue(map[string]bool{"raw/a.md": true}, "raw/a.md", queuePath)
	if err != nil || !ok {
		t.Fatalf("ok=%v err=%v", ok, err)
	}
	got, _ := ReadQueue(queuePath)
	if want := []string{"raw/a.md"}; !reflect.DeepEqual(got, want) {
		t.Errorf("queue = %v", got)
	}
}

func TestCheckAndEnqueueSkipsWhenNotEligible(t *testing.T) {
	queuePath := filepath.Join(t.TempDir(), "watch-queue.jsonl")
	ok, err := CheckAndEnqueue(map[string]bool{"raw/other.md": true}, "raw/a.md", queuePath)
	if err != nil || ok {
		t.Fatalf("ok=%v err=%v", ok, err)
	}
	got, _ := ReadQueue(queuePath)
	if len(got) != 0 {
		t.Errorf("queue = %v, want empty", got)
	}
}

// --- RelForEvent --------------------------------------------------------------

func TestRelForEventMapsFileUnderRoot(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "raw", "note.md")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	rel, ok := RelForEvent(root, path)
	if !ok || rel != "raw/note.md" {
		t.Errorf("RelForEvent = (%q, %v)", rel, ok)
	}
}

func TestRelForEventIgnoresDirectory(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "raw", "subdir")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if _, ok := RelForEvent(root, dir); ok {
		t.Error("directory reported as a file event")
	}
}

func TestRelForEventIgnoresPathOutsideRoot(t *testing.T) {
	root := t.TempDir()
	outside := filepath.Join(t.TempDir(), "note.md")
	if err := os.WriteFile(outside, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, ok := RelForEvent(root, outside); ok {
		t.Error("outside-root path reported as a file event")
	}
}
