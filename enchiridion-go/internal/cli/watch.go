package cli

import (
	"fmt"
	"io/fs"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/fsnotify/fsnotify"
	"github.com/spf13/cobra"

	"github.com/dhague/enchiridion/enchiridion-go/internal/ingestscan"
	"github.com/dhague/enchiridion/enchiridion-go/internal/vault"
	"github.com/dhague/enchiridion/enchiridion-go/internal/watch"
)

// newWatchCommand ports `wiki-plugin/scripts/watch_raw.py`: a long-running
// filesystem watcher over raw/ with per-file debounce, an exclusive lock, and
// a queue file. `--dequeue <raw_rel>` removes one queue entry and exits.
func newWatchCommand() *cobra.Command {
	var (
		vaultFlag    string
		debounce     float64
		pollInterval float64
		dequeue      string
	)

	cmd := &cobra.Command{
		Use:   "watch",
		Short: "Watch raw/ for new files and enqueue eligible ones",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			root := vaultFlag
			if root == "" {
				var err error
				root, err = vault.ResolveRoot("", nil)
				if err != nil {
					return err
				}
			} else if resolved, err := filepath.EvalSymlinks(root); err == nil {
				root = resolved
			}
			paths := watch.ForRoot(root)

			if dequeue != "" {
				return watch.RemoveFromQueue(paths.Queue, dequeue)
			}

			ok, stalePID, err := watch.AcquireLock(paths.Lock, time.Time{}, nil)
			if err != nil {
				return err
			}
			if !ok {
				return fmt.Errorf("another watcher is already running (lock at %s)", paths.Lock)
			}
			if stalePID != nil {
				cmd.Printf("previous watcher exited without cleanup, removing stale lock (pid=%d)\n", *stalePID)
			}
			defer watch.RemoveLock(paths.Lock) //nolint:errcheck

			return runWatch(cmd, paths, debounce, pollInterval)
		},
	}

	flags := cmd.Flags()
	flags.StringVar(&vaultFlag, "vault", "", "vault root; defaults to resolve_vault_root()")
	flags.Float64Var(&debounce, "debounce", watch.DefaultDebounceSeconds, "per-file debounce, seconds")
	flags.Float64Var(&pollInterval, "poll-interval", watch.DefaultPollIntervalSeconds, "how often to check for settled files, seconds")
	flags.StringVar(&dequeue, "dequeue", "",
		"remove this vault-relative path from the watch queue and exit, instead of watching")

	return cmd
}

func runWatch(cmd *cobra.Command, paths watch.Paths, debounceSeconds, pollInterval float64) error {
	rawRoot := filepath.Join(paths.Root, "raw")
	if err := os.MkdirAll(rawRoot, 0o755); err != nil {
		return err
	}

	watcher, err := fsnotify.NewWatcher()
	if err != nil {
		return err
	}
	defer watcher.Close()
	if err := addRecursive(watcher, rawRoot); err != nil {
		return err
	}

	deb := watch.NewDebouncer(debounceSeconds, nil)

	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt, syscall.SIGTERM)
	defer signal.Stop(signals)

	cmd.Printf("watching %s (debounce=%gs, pid=%d)\n", rawRoot, debounceSeconds, os.Getpid())

	ticker := time.NewTicker(time.Duration(pollInterval * float64(time.Second)))
	defer ticker.Stop()

	for {
		select {
		case <-signals:
			cmd.Println("watcher stopped")
			return nil
		case event, open := <-watcher.Events:
			if !open {
				return nil
			}
			handleEvent(watcher, deb, paths.Root, event)
		case _, open := <-watcher.Errors:
			if !open {
				return nil
			}
			// Log-and-keep-watching; a transient read error isn't fatal.
		case <-ticker.C:
			settled := deb.SettledFiles()
			if len(settled) == 0 {
				continue
			}
			result, err := ingestscan.Scan(paths.Root, "", nil)
			eligible := map[string]bool{}
			if err != nil {
				cmd.Printf("error scanning raw/: %v\n", err)
			} else {
				for _, c := range result.Eligible {
					eligible[c.RawRel] = true
				}
			}
			for _, rel := range settled {
				queued, err := watch.CheckAndEnqueue(eligible, rel, paths.Queue)
				if err != nil {
					cmd.Printf("error queuing %s: %v\n", rel, err)
				} else if queued {
					cmd.Printf("queued %s\n", rel)
				}
			}
		}
	}
}

// handleEvent records a non-directory event under root in the debouncer, and
// picks up any newly-created subdirectory so the watch stays recursive.
func handleEvent(watcher *fsnotify.Watcher, deb *watch.Debouncer, root string, event fsnotify.Event) {
	if event.Op&fsnotify.Create != 0 {
		if info, err := os.Stat(event.Name); err == nil && info.IsDir() {
			_ = addRecursive(watcher, event.Name)
			return
		}
	}
	if rel, ok := watch.RelForEvent(root, event.Name); ok {
		deb.RecordEvent(rel)
	}
}

// addRecursive adds dir and every subdirectory to the watcher, so events in
// pre-existing nested folders are seen from the start.
func addRecursive(watcher *fsnotify.Watcher, dir string) error {
	return filepath.WalkDir(dir, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil //nolint:nilerr // a vanished dir mid-walk isn't fatal
		}
		if d.IsDir() {
			return watcher.Add(path)
		}
		return nil
	})
}
