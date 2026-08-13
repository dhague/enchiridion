// Package ingestignore reads and appends to `.ingestignore` — the
// human-authored policy file that permanently withdraws a raw/ file from the
// ingestion sweep. Ported from the `.ingestignore` half of
// `wiki-plugin/scripts/ingest_scan.py`.
//
// Split out rather than landing inside the sweep because `enchiridion ingest
// --ignore` needs the append half now, and the sweep itself (`ingest_scan`)
// arrives with the remaining subcommands in #152 — at which point it reads
// the same [Parse] this file already owns, instead of a second copy.
//
// A `.ingestignore` is read from a raw file's own folder only, with **no
// ancestor walk** — what keeps a hand-written policy file from drifting into
// a machine-written done-list.
package ingestignore

import (
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
)

// Filename is the policy file's fixed name, in the folder whose files it
// governs.
const Filename = ".ingestignore"

// Parse reads `.ingestignore` text into its patterns, in order.
//
// Strips `#` comments (full-line and trailing) and blank lines; the rest is a
// filename glob. `/`, `!` and `**` are rejected outright, so a bare filename
// (`literal.md`) and a simple glob (`*.tmp`) are the only supported shapes —
// deliberately, since anything richer would raise precedence questions a
// per-folder policy file has no way to answer.
func Parse(text string) ([]string, error) {
	var patterns []string
	for line := range strings.SplitSeq(text, "\n") {
		line, _, _ = strings.Cut(line, "#")
		line = strings.TrimRight(line, " \t\r")
		if strings.TrimSpace(line) == "" {
			continue
		}
		if strings.ContainsAny(line, "/!") || strings.Contains(line, "**") {
			return nil, fmt.Errorf(
				"%s patterns must be bare filename globs (no '/', no '!', no '**'): %q",
				Filename, line)
		}
		patterns = append(patterns, line)
	}
	return patterns, nil
}

// Append adds pattern to folder's `.ingestignore`, creating the file if
// absent.
//
// Idempotent — a pattern already present isn't re-added, so a sweep run twice
// doesn't double-list. comment, when non-empty, goes on the same line after
// the `#`. Backs the sweep's `never` answer.
func Append(folder, pattern, comment string) error {
	path := filepath.Join(folder, Filename)

	if existing, err := os.ReadFile(path); err == nil {
		patterns, err := Parse(string(existing))
		if err != nil {
			return err
		}
		if slices.Contains(patterns, pattern) {
			return nil
		}
	} else if !os.IsNotExist(err) {
		return err
	}

	line := pattern
	if comment != "" {
		line += "  # " + comment
	}
	if err := os.MkdirAll(folder, 0o755); err != nil {
		return err
	}
	file, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer file.Close()
	_, err = file.WriteString(line + "\n")
	return err
}
