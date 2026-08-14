package cli

import (
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/spf13/cobra"

	"github.com/dhague/enchiridion/enchiridion-go/internal/wikipage"
	"github.com/dhague/enchiridion/enchiridion-go/internal/wikitime"
)

// newPageCommand ports `wiki-plugin/scripts/wikipage.py`'s CLI: frontmatter
// get/set/merge over a single markdown file.
//
// Every subcommand takes a *file path* and resolves no vault root — the same
// split the Python CLI drew, and for the same reason: these are pure page
// operations, so nothing here needs to know where the vault is. Moving a page
// is `enchiridion vault move`, precisely because that one operation does.
func newPageCommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "page",
		Short: "Read and edit one page's frontmatter",
	}
	cmd.AddCommand(newPageGetCommand(), newPageSetCommand(), newPageMergeCommand())
	return cmd
}

func newPageGetCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "get <file> <key>",
		Short: "Print a frontmatter value",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			page, err := loadPage(args[0])
			if err != nil {
				return err
			}
			value, ok, err := page.Get(args[1])
			if err != nil {
				return err
			}
			// Absent, or explicitly null: a non-zero exit and no stdout, as
			// `wikipage.py get` returned 1 for a `None` value.
			if !ok || value == nil {
				return fmt.Errorf("no frontmatter key %q in %s", args[1], args[0])
			}
			cmd.Println(formatFrontmatterValue(args[1], value))
			return nil
		},
	}
}

func newPageSetCommand() *cobra.Command {
	var asJSON bool

	cmd := &cobra.Command{
		Use:   "set <file> <key> <value>",
		Short: "Set a frontmatter value in place",
		Args:  cobra.ExactArgs(3),
		RunE: func(cmd *cobra.Command, args []string) error {
			path, key, raw := args[0], args[1], args[2]
			page, err := loadPage(path)
			if err != nil {
				return err
			}
			var value any = raw
			if asJSON {
				if err := json.Unmarshal([]byte(raw), &value); err != nil {
					return fmt.Errorf("parsing %s as JSON: %w", key, err)
				}
			}
			if key == "source_date" {
				if value, err = canonicalSourceDate(value); err != nil {
					return err
				}
			}
			updated, err := page.Set(key, value)
			if err != nil {
				return err
			}
			return writePageFile(path, updated)
		},
	}

	cmd.Flags().BoolVar(&asJSON, "json", false, "parse value as JSON")

	return cmd
}

func newPageMergeCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "merge <file> <key> <json-list>",
		Short: "Union a JSON list into an existing list-valued key (tags, edge keys)",
		Args:  cobra.ExactArgs(3),
		RunE: func(cmd *cobra.Command, args []string) error {
			path, key, raw := args[0], args[1], args[2]
			page, err := loadPage(path)
			if err != nil {
				return err
			}
			var values []any
			if err := json.Unmarshal([]byte(raw), &values); err != nil {
				return fmt.Errorf("merge expects a JSON list for %s: %w", key, err)
			}
			updated, err := page.Merge(key, values)
			if err != nil {
				return err
			}
			return writePageFile(path, updated)
		},
	}
}

func loadPage(path string) (wikipage.Page, error) {
	text, err := os.ReadFile(path)
	if err != nil {
		return wikipage.Page{}, err
	}
	return wikipage.Page{Text: string(text)}, nil
}

func writePageFile(path string, page wikipage.Page) error {
	return os.WriteFile(path, []byte(page.Text), 0o644)
}

// formatFrontmatterValue renders a frontmatter value the way `print(value)`
// did in Python — notably a list as `['a', 'b']`, the form callers of
// `wikipage.py get` have always parsed.
func formatFrontmatterValue(key string, value any) string {
	items, ok := value.([]any)
	if !ok {
		return formatScalar(key, value)
	}
	strs := make([]string, len(items))
	for i, item := range items {
		strs[i] = formatScalar(key, item)
	}
	return pythonListRepr(strs)
}

// formatScalar renders one frontmatter scalar the way printing it in Python
// did, so a caller parsing `wikipage.py get` output still parses this one.
//
// `source_date` is the one field with a canonical spelling of its own: it is
// valid time — a date, not an instant — so a clock on it is truncated to
// YYYY-MM-DD here, via the same [wikitime.ParseDate] the index reads through.
// Every other scalar keeps the Python rendering.
//
// Two types need help. A bool prints Go-style `true`, where Python printed
// `True`. And a YAML timestamp decodes to a time.Time here but to a ruamel
// date/TimeStamp there, whose three renderings are all unlike Go's default
// "2026-01-15 00:00:00 +0000 UTC":
//
//	created: 2026-01-15                 -> 2026-01-15
//	created: 2026-01-15 10:30:00        -> 2026-01-15 10:30:00
//	created: 2026-01-15T00:00:00+05:00  -> 2026-01-15T00:00:00+05:00
//
// One ambiguity is irreducible: the decoder gives a zone-less scalar and an
// explicit `Z` scalar the same UTC time.Time, so a `Z` timestamp renders in
// the zone-less form (dropping "+00:00") and `2026-01-15T00:00:00Z` renders
// as a bare date. It only bites keys outside the schema's date field —
// `source_date` never reaches here, because the branch above canonicalises it
// first — so it is latent rather than live.
func formatScalar(key string, value any) string {
	if key == "source_date" {
		if date, ok := wikitime.ParseDate(value); ok {
			return date
		}
	}
	switch v := value.(type) {
	case bool:
		if v {
			return "True"
		}
		return "False"
	case time.Time:
		return formatTimestamp(v)
	default:
		return fmt.Sprintf("%v", value)
	}
}

// canonicalSourceDate renders a `source_date` value in its canonical
// YYYY-MM-DD spelling, truncating a clock. A value that isn't a valid date
// at all is refused — the same rejection ingest's validation applies, so no
// write path can ever store a value the read path would silently mis-sort
// under `--since`/`--until` (#192). An explicit null passes through, reading
// back as absent exactly as it does at ingest.
func canonicalSourceDate(value any) (any, error) {
	if value == nil {
		return value, nil
	}
	date, ok := wikitime.ParseDate(value)
	if !ok {
		return nil, fmt.Errorf("source_date must be a valid date (YYYY-MM-DD), got %v", value)
	}
	return date, nil
}

func formatTimestamp(ts time.Time) string {
	_, offset := ts.Zone()
	if offset != 0 {
		return ts.Format("2006-01-02T15:04:05-07:00")
	}
	h, m, s := ts.Clock()
	if h == 0 && m == 0 && s == 0 && ts.Nanosecond() == 0 {
		return ts.Format("2006-01-02")
	}
	return ts.Format("2006-01-02 15:04:05")
}
