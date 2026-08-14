package cli

import (
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/spf13/cobra"

	"github.com/dhague/enchiridion/enchiridion-go/internal/wikipage"
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
			cmd.Println(formatFrontmatterValue(value))
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
func formatFrontmatterValue(value any) string {
	items, ok := value.([]any)
	if !ok {
		return formatScalar(value)
	}
	strs := make([]string, len(items))
	for i, item := range items {
		strs[i] = formatScalar(item)
	}
	return pythonListRepr(strs)
}

// formatScalar renders one frontmatter scalar as Python's `str()` did.
//
// The case that matters is a YAML timestamp: `created: 2026-01-15` decodes to
// a time.Time here but to a `datetime.date` in ruamel, and Go's default
// rendering ("2026-01-15 00:00:00 +0000 UTC") is nothing like `str(date)`.
// A zero clock means the source scalar was date-only, so it prints as a date;
// anything else keeps its time, matching `str(datetime)`.
func formatScalar(value any) string {
	ts, ok := value.(time.Time)
	if !ok {
		return fmt.Sprintf("%v", value)
	}
	if ts.Equal(ts.Truncate(24 * time.Hour)) {
		return ts.Format("2006-01-02")
	}
	return ts.Format("2006-01-02 15:04:05")
}
