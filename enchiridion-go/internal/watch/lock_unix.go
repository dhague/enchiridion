//go:build !windows

package watch

import (
	"os"
	"syscall"
)

// exclusiveLock takes an exclusive advisory lock on f, held until
// exclusiveUnlock. Released automatically if the process dies.
func exclusiveLock(f *os.File) error {
	return syscall.Flock(int(f.Fd()), syscall.LOCK_EX)
}

func exclusiveUnlock(f *os.File) error {
	return syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
}

// defaultPIDAlive probes whether pid names a live process via `kill(pid, 0)`,
// mirroring Python's os.kill(pid, 0) semantics: ESRCH means dead, EPERM means
// alive-but-not-ours.
func defaultPIDAlive(pid int) bool {
	if err := syscall.Kill(pid, 0); err != nil {
		return err != syscall.ESRCH
	}
	return true
}
