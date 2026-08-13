//go:build windows

package watch

import (
	"os"

	"golang.org/x/sys/windows"
)

// exclusiveLock takes an exclusive lock on one byte of f, held until
// exclusiveUnlock. Released automatically if the process dies.
func exclusiveLock(f *os.File) error {
	return windows.LockFileEx(
		windows.Handle(f.Fd()),
		windows.LOCKFILE_EXCLUSIVE_LOCK,
		0, 1, 0,
		&windows.Overlapped{},
	)
}

func exclusiveUnlock(f *os.File) error {
	return windows.UnlockFileEx(
		windows.Handle(f.Fd()),
		0, 1, 0,
		&windows.Overlapped{},
	)
}

// defaultPIDAlive probes whether pid names a live process. NOT `kill(pid, 0)`:
// on Windows, any signal other than CTRL_C_EVENT/CTRL_BREAK_EVENT calls
// TerminateProcess — it would kill the live process it is supposed to be
// probing.
func defaultPIDAlive(pid int) bool {
	handle, err := windows.OpenProcess(
		windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(pid))
	if err != nil {
		return false
	}
	_ = windows.CloseHandle(handle)
	return true
}
