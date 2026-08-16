package cli

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/fsnotify/fsnotify"
	"github.com/spf13/cobra"

	"github.com/dhague/enchiridion/enchiridion-go/internal/watch"
)

// fakeSource is the test-side adapter behind [eventSource]: unbuffered
// channels, so a completed send means the loop has taken the value and the
// next send can't land until it has finished processing the previous one.
// That ordering is what makes the loop tests deterministic despite `select`
// picking at random among simultaneously-ready cases.
type fakeSource struct {
	events chan fsnotify.Event
	errors chan error

	mu     sync.Mutex
	added  []string
	addErr error
}

func newFakeSource() *fakeSource {
	return &fakeSource{
		events: make(chan fsnotify.Event),
		errors: make(chan error),
	}
}

func (f *fakeSource) Events() <-chan fsnotify.Event { return f.events }
func (f *fakeSource) Errors() <-chan error          { return f.errors }

func (f *fakeSource) Add(path string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.addErr != nil {
		return f.addErr
	}
	f.added = append(f.added, path)
	return nil
}

func (f *fakeSource) addedPaths() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := append([]string(nil), f.added...)
	slices.Sort(out)
	return out
}

// fakeClock is the debouncer's injected time source. The loop reads it on its
// own goroutine while the test advances it, so both sides go through the
// mutex.
type fakeClock struct {
	mu sync.Mutex
	t  float64
}

func (c *fakeClock) now() float64 {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *fakeClock) set(t float64) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.t = t
}

// loopHarness drives watchLoop on a goroutine and collects its output.
type loopHarness struct {
	t         *testing.T
	src       *fakeSource
	debouncer *watch.Debouncer
	clock     *fakeClock
	paths     watch.Paths
	signals   chan os.Signal
	ticks     chan time.Time
	out       *bytes.Buffer
	done      chan error
}

// startLoop launches watchLoop with a fake event source, fake signal and tick
// channels, and the given eligibility scan.
func startLoop(t *testing.T, root string, scan func(string) (map[string]bool, error)) *loopHarness {
	t.Helper()
	clock := &fakeClock{}
	h := &loopHarness{
		t:         t,
		src:       newFakeSource(),
		debouncer: watch.NewDebouncer(30, clock.now),
		clock:     clock,
		paths:     watch.ForRoot(root),
		signals:   make(chan os.Signal),
		ticks:     make(chan time.Time),
		out:       &bytes.Buffer{},
		done:      make(chan error, 1),
	}
	cmd := &cobra.Command{}
	cmd.SetOut(h.out)
	cmd.SetErr(h.out)
	go func() {
		h.done <- watchLoop(cmd, watchLoopDeps{
			source:    h.src,
			debouncer: h.debouncer,
			paths:     h.paths,
			signals:   h.signals,
			ticks:     h.ticks,
			scan:      scan,
		})
	}()
	return h
}

// waitIdle blocks until the loop has finished handling everything sent so far.
//
// The loop only receives on the errors channel from `select`, so a completed
// send proves it has re-entered `select` — i.e. the previous case returned.
// The errors case itself touches no shared state, so nothing is in flight when
// this returns.
func (h *loopHarness) waitIdle() {
	h.t.Helper()
	h.src.errors <- errors.New("sync barrier")
}

// stop signals the loop and waits for it to return.
func (h *loopHarness) stop() (string, error) {
	h.t.Helper()
	h.signals <- os.Interrupt
	return h.wait()
}

func (h *loopHarness) wait() (string, error) {
	h.t.Helper()
	select {
	case err := <-h.done:
		return h.out.String(), err
	case <-time.After(5 * time.Second):
		h.t.Fatal("watchLoop did not return")
		return "", nil
	}
}

func noScan(string) (map[string]bool, error) { return nil, nil }

func TestWatchLoopRecordsFileEvents(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	file := filepath.Join(root, "raw", "notes.md")
	writeFileTree(t, root, map[string]string{"raw/notes.md": "hello"})

	h := startLoop(t, root, noScan)
	h.clock.set(100)
	h.src.events <- fsnotify.Event{Name: file, Op: fsnotify.Write}
	if _, err := h.stop(); err != nil {
		t.Fatalf("watchLoop: %v", err)
	}

	at, ok := h.debouncer.LastEvent("raw/notes.md")
	if !ok || at != 100.0 {
		t.Fatalf("LastEvent(raw/notes.md) = (%v, %v), want (100, true)", at, ok)
	}
}

func TestWatchLoopWatchesNewlyCreatedDirectories(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	writeFileTree(t, root, map[string]string{"raw/sub/inner/keep.md": "x"})
	sub := filepath.Join(root, "raw", "sub")

	h := startLoop(t, root, noScan)
	h.src.events <- fsnotify.Event{Name: sub, Op: fsnotify.Create}
	if _, err := h.stop(); err != nil {
		t.Fatalf("watchLoop: %v", err)
	}

	want := []string{sub, filepath.Join(sub, "inner")}
	if got := h.src.addedPaths(); !slices.Equal(got, want) {
		t.Fatalf("added = %v, want %v", got, want)
	}
	if _, ok := h.debouncer.LastEvent("raw/sub"); ok {
		t.Fatal("directory create should not be debounced as a file")
	}
}

func TestWatchLoopEnqueuesSettledEligibleFiles(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	writeFileTree(t, root, map[string]string{
		"raw/eligible.md": "a",
		"raw/ignored.md":  "b",
	})

	scans := 0
	h := startLoop(t, root, func(string) (map[string]bool, error) {
		scans++
		return map[string]bool{"raw/eligible.md": true}, nil
	})

	h.src.events <- fsnotify.Event{Name: filepath.Join(root, "raw", "eligible.md"), Op: fsnotify.Write}
	h.src.events <- fsnotify.Event{Name: filepath.Join(root, "raw", "ignored.md"), Op: fsnotify.Write}
	h.waitIdle()
	h.clock.set(100) // both files are now past the 30s debounce window
	h.ticks <- time.Now()

	out, err := h.stop()
	if err != nil {
		t.Fatalf("watchLoop: %v", err)
	}
	if scans != 1 {
		t.Fatalf("scan called %d times, want 1 (one sweep per tick, not per file)", scans)
	}
	if !strings.Contains(out, "queued raw/eligible.md") {
		t.Fatalf("output missing queued line:\n%s", out)
	}
	if strings.Contains(out, "raw/ignored.md") {
		t.Fatalf("ineligible file was queued:\n%s", out)
	}

	queued, err := watch.ReadQueue(h.paths.Queue)
	if err != nil {
		t.Fatalf("ReadQueue: %v", err)
	}
	if !slices.Equal(queued, []string{"raw/eligible.md"}) {
		t.Fatalf("queue = %v, want [raw/eligible.md]", queued)
	}
}

func TestWatchLoopSkipsScanWhenNothingSettled(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	scans := 0
	h := startLoop(t, root, func(string) (map[string]bool, error) {
		scans++
		return nil, nil
	})

	h.ticks <- time.Now()
	if _, err := h.stop(); err != nil {
		t.Fatalf("watchLoop: %v", err)
	}
	if scans != 0 {
		t.Fatalf("scan called %d times on an empty tick, want 0", scans)
	}
}

func TestWatchLoopKeepsWatchingAfterAScanError(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	writeFileTree(t, root, map[string]string{"raw/notes.md": "a"})

	h := startLoop(t, root, func(string) (map[string]bool, error) {
		return nil, errors.New("disk on fire")
	})

	h.src.events <- fsnotify.Event{Name: filepath.Join(root, "raw", "notes.md"), Op: fsnotify.Write}
	h.waitIdle()
	h.clock.set(100)
	h.ticks <- time.Now()

	out, err := h.stop()
	if err != nil {
		t.Fatalf("watchLoop: %v", err)
	}
	if !strings.Contains(out, "error scanning raw/: disk on fire") {
		t.Fatalf("output missing scan error:\n%s", out)
	}
	if queued, _ := watch.ReadQueue(h.paths.Queue); len(queued) != 0 {
		t.Fatalf("queue = %v, want empty after a failed scan", queued)
	}
}

func TestWatchLoopReportsQueueWriteFailures(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	writeFileTree(t, root, map[string]string{"raw/notes.md": "a"})
	// A directory where the queue file belongs makes every queue write fail.
	if err := os.MkdirAll(watch.ForRoot(root).Queue, 0o755); err != nil {
		t.Fatal(err)
	}

	h := startLoop(t, root, func(string) (map[string]bool, error) {
		return map[string]bool{"raw/notes.md": true}, nil
	})

	h.src.events <- fsnotify.Event{Name: filepath.Join(root, "raw", "notes.md"), Op: fsnotify.Write}
	h.waitIdle()
	h.clock.set(100)
	h.ticks <- time.Now()

	out, err := h.stop()
	if err != nil {
		t.Fatalf("watchLoop: %v", err)
	}
	if !strings.Contains(out, "error queuing raw/notes.md") {
		t.Fatalf("output missing queue error:\n%s", out)
	}
}

func TestWatchLoopKeepsWatchingAfterAWatcherError(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	writeFileTree(t, root, map[string]string{"raw/notes.md": "a"})

	h := startLoop(t, root, noScan)
	h.src.errors <- errors.New("transient read error")
	// A completed send on the next channel proves the loop survived the error.
	h.src.events <- fsnotify.Event{Name: filepath.Join(root, "raw", "notes.md"), Op: fsnotify.Write}

	if _, err := h.stop(); err != nil {
		t.Fatalf("watchLoop: %v", err)
	}
	if _, ok := h.debouncer.LastEvent("raw/notes.md"); !ok {
		t.Fatal("event after a watcher error was not recorded")
	}
}

func TestWatchLoopStopsOnSignal(t *testing.T) {
	t.Parallel()
	h := startLoop(t, t.TempDir(), noScan)
	out, err := h.stop()
	if err != nil {
		t.Fatalf("watchLoop: %v", err)
	}
	if !strings.Contains(out, "watcher stopped") {
		t.Fatalf("output missing stop line:\n%s", out)
	}
}

func TestWatchLoopStopsWhenChannelsClose(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct {
		name  string
		close func(*fakeSource)
	}{
		{"events", func(f *fakeSource) { close(f.events) }},
		{"errors", func(f *fakeSource) { close(f.errors) }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			h := startLoop(t, t.TempDir(), noScan)
			tc.close(h.src)
			if _, err := h.wait(); err != nil {
				t.Fatalf("watchLoop: %v", err)
			}
		})
	}
}

func TestScanEligibleFlattensSweepCandidates(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	writeFileTree(t, root, map[string]string{
		"raw/fresh.md":      "never ingested",
		"raw/.ingestignore": "skipped.md\n",
		"raw/skipped.md":    "ignored",
	})

	eligible, err := scanEligible(root)
	if err != nil {
		t.Fatalf("scanEligible: %v", err)
	}
	if !eligible["raw/fresh.md"] {
		t.Fatalf("eligible = %v, want raw/fresh.md", eligible)
	}
	if eligible["raw/skipped.md"] {
		t.Fatalf("eligible = %v, want no .ingestignore match", eligible)
	}
}

func TestHandleEventRoutesByPathKind(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	writeFileTree(t, root, map[string]string{
		"raw/created.md":  "a",
		"raw/written.md":  "b",
		"raw/sub/deep.md": "c",
	})
	outside := filepath.Join(t.TempDir(), "elsewhere.md")
	if err := os.WriteFile(outside, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}

	for _, tc := range []struct {
		name      string
		event     fsnotify.Event
		wantRel   string   // "" = nothing debounced
		wantAdded []string // watches the event should have added
	}{
		{
			name:    "create of a regular file debounces it",
			event:   fsnotify.Event{Name: filepath.Join(root, "raw", "created.md"), Op: fsnotify.Create},
			wantRel: "raw/created.md",
		},
		{
			name:    "write to a regular file debounces it",
			event:   fsnotify.Event{Name: filepath.Join(root, "raw", "written.md"), Op: fsnotify.Write},
			wantRel: "raw/written.md",
		},
		{
			name:      "create of a directory extends the watch instead",
			event:     fsnotify.Event{Name: filepath.Join(root, "raw", "sub"), Op: fsnotify.Create},
			wantAdded: []string{filepath.Join(root, "raw", "sub")},
		},
		{
			name:  "event for a path outside the root is ignored",
			event: fsnotify.Event{Name: outside, Op: fsnotify.Write},
		},
		{
			name:  "event for a vanished path is ignored",
			event: fsnotify.Event{Name: filepath.Join(root, "raw", "gone.md"), Op: fsnotify.Remove},
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			src := newFakeSource()
			deb := watch.NewDebouncer(30, func() float64 { return 0 })

			handleEvent(src, deb, root, tc.event)

			if tc.wantRel != "" {
				if _, ok := deb.LastEvent(tc.wantRel); !ok {
					t.Fatalf("%s was not debounced", tc.wantRel)
				}
			} else if _, ok := deb.LastEvent(filepath.Base(tc.event.Name)); ok {
				t.Fatalf("%s should not have been debounced", tc.event.Name)
			}
			if got := src.addedPaths(); !slices.Equal(got, tc.wantAdded) {
				t.Fatalf("added = %v, want %v", got, tc.wantAdded)
			}
		})
	}
}

func TestAddRecursiveAddsDirectoriesOnly(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	writeFileTree(t, root, map[string]string{
		"raw/top.md":            "a",
		"raw/sub/nested.md":     "b",
		"raw/sub/deep/leaf.md":  "c",
		"raw/other/another.txt": "d",
	})
	rawRoot := filepath.Join(root, "raw")

	src := newFakeSource()
	if err := addRecursive(src, rawRoot); err != nil {
		t.Fatalf("addRecursive: %v", err)
	}

	want := []string{
		rawRoot,
		filepath.Join(rawRoot, "other"),
		filepath.Join(rawRoot, "sub"),
		filepath.Join(rawRoot, "sub", "deep"),
	}
	if got := src.addedPaths(); !slices.Equal(got, want) {
		t.Fatalf("added = %v, want %v", got, want)
	}
}

func TestAddRecursivePropagatesAddErrors(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	writeFileTree(t, root, map[string]string{"raw/top.md": "a"})

	src := newFakeSource()
	src.addErr = errors.New("watch limit reached")
	err := addRecursive(src, filepath.Join(root, "raw"))
	if err == nil || !strings.Contains(err.Error(), "watch limit reached") {
		t.Fatalf("addRecursive error = %v, want the Add error", err)
	}
}

// writeFileTree writes each rel->content under root, creating parents.
func writeFileTree(t *testing.T, root string, files map[string]string) {
	t.Helper()
	for rel, content := range files {
		path := filepath.Join(root, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}
