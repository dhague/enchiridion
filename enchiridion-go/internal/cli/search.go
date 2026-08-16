package cli

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/spf13/cobra"

	"github.com/dhague/enchiridion/enchiridion-go/internal/searchindex"
	"github.com/dhague/enchiridion/enchiridion-go/internal/vault"
)

// dateFieldFlag is a pflag.Value with a fixed choice set, mirroring
// search.py's `argparse.choices=("source_date", "git_date")` — a bad value
// is a usage error at flag-parsing time, not a query-time error surfaced
// only after Search runs. It writes straight into the [searchindex.Query]
// field it flags, rather than a local var later copied in.
type dateFieldFlag struct{ target *string }

func (f dateFieldFlag) String() string { return *f.target }
func (f dateFieldFlag) Type() string   { return "string" }
func (f dateFieldFlag) Set(value string) error {
	switch value {
	case "source_date", "git_date":
		*f.target = value
		return nil
	default:
		return fmt.Errorf("must be 'source_date' or 'git_date', got %q", value)
	}
}

// commaListFlag is a pflag.Value taking a comma-separated string and
// splitting it straight into the []string [searchindex.Query] field it
// flags, so --kind/--volatility need no local string var copied in later.
type commaListFlag struct{ target *[]string }

func (f commaListFlag) String() string { return strings.Join(*f.target, ",") }
func (f commaListFlag) Type() string   { return "string" }
func (f commaListFlag) Set(value string) error {
	*f.target = splitCommaList(value)
	return nil
}

// newSearchCommand ports `wiki-plugin/scripts/search.py`, flag for flag, so
// a migrated SKILL.md's invocation differs only in the program name.
//
// Default mode is a query: positional text plus any metadata filter.
// --reindex / --status switch to index management. --json emits one
// [searchindex.Hit] per line; the default is a compact one-line-per-hit
// table a Haiku agent can read directly.
func newSearchCommand() *cobra.Command {
	// query is bound to directly by the flags below (see dateFieldFlag /
	// commaListFlag), so there is no local-var-per-flag copied field-for-field
	// into a Query at RunE time.
	query := searchindex.Query{DateField: "source_date", Limit: 20}

	var (
		asJSON bool

		reindex bool
		full    bool
		status  bool
	)

	cmd := &cobra.Command{
		Use:   "search [text]",
		Short: "Search the wiki vault via the lexical index",
		Long: "Search the wiki vault via the lexical index.\n\n" +
			"Default mode is a query: positional text plus any metadata filter.\n" +
			"--reindex / --status switch to index management.",
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			root, err := vault.ResolveRoot("", nil)
			if err != nil {
				return err
			}
			index, err := searchindex.Open(root)
			if err != nil {
				return err
			}
			defer index.Close()

			switch {
			case status:
				return runStatus(cmd, index, asJSON)
			case reindex:
				return runReindex(cmd, index, full, asJSON)
			}

			if len(args) == 1 {
				query.Text = args[0]
			}
			hits, err := index.Search(query)
			if err != nil {
				return err
			}
			return renderHits(cmd, hits, asJSON)
		},
	}

	flags := cmd.Flags()
	flags.StringArrayVar(&query.TagsAll, "tag", nil,
		"filter by tag; repeat for tags_all (AND) and combine with --tag-any for OR")
	flags.StringArrayVar(&query.TagsAny, "tag-any", nil,
		"filter by tag (OR semantics across the listed tags)")
	flags.Var(commaListFlag{&query.Kinds}, "kind",
		"filter by kind (concept|entity|source|synthesis); comma-separated for multiple")
	flags.StringVar(&query.Since, "since", "", "ISO date; inclusive lower bound on date_field")
	flags.StringVar(&query.Until, "until", "", "ISO date; inclusive upper bound on date_field")
	flags.Var(dateFieldFlag{&query.DateField}, "date-field",
		"which date the --since/--until bounds apply to (source_date|git_date)")
	flags.Var(commaListFlag{&query.Volatility}, "volatility",
		"filter by volatility (stable|evolving|volatile); comma-separated for multiple")
	flags.IntVar(&query.Limit, "limit", 20, "max hits")
	flags.BoolVar(&query.IncludeSuperseded, "include-superseded", false,
		"include pages that have been superseded (default: filter them out)")
	flags.BoolVar(&query.Raw, "raw", false,
		"pass the text through as a literal FTS5 expression (escape hatch)")
	flags.BoolVar(&asJSON, "json", false, "emit results as JSON Lines (one object per line)")
	flags.BoolVar(&reindex, "reindex", false, "rebuild the index")
	flags.BoolVar(&full, "full", false, "with --reindex: wipe the index and rebuild from scratch")
	flags.BoolVar(&status, "status", false, "print index status and exit")

	return cmd
}

func runStatus(cmd *cobra.Command, index *searchindex.Index, asJSON bool) error {
	st, err := index.Status()
	if err != nil {
		return err
	}
	if asJSON {
		return printJSONLine(cmd, st)
	}
	cmd.Printf("pages:             %d\n", st.Pages)
	cmd.Printf("db_size_bytes:     %d\n", st.DBSizeBytes)
	cmd.Printf("backend:           %s\n", st.Backend)
	cmd.Printf("schema_version:    %s\n", st.SchemaVersion)
	cmd.Printf("git_head:          %s\n", orDash(st.GitHead))
	if st.UncommittedPages > 0 {
		cmd.Printf("uncommitted_pages: %d page(s) on disk not yet committed — not searchable.\n",
			st.UncommittedPages)
	} else {
		cmd.Printf("uncommitted_pages: 0\n")
	}
	return nil
}

func runReindex(cmd *cobra.Command, index *searchindex.Index, full, asJSON bool) error {
	stats, err := index.Reindex(full)
	if err != nil {
		return err
	}
	if asJSON {
		return printJSONLine(cmd, stats)
	}
	action := "reindex"
	if full {
		action = "full reindex"
	}
	cmd.Printf("%s: %d pages (+%d ~%d -%d) in %.1f ms\n",
		action, stats.Pages, stats.Inserted, stats.Updated, stats.Removed, stats.DurationMS)
	return nil
}

// renderHits writes one line per hit: JSON when asJSON, otherwise a compact
// table an agent reader can scan.
//
// Path is right-padded so the title column aligns. Volatility is bracketed
// and both dates are shown: with age, they are the trust signal the agent has
// to carry into any answer. Tags and summary are --json only.
func renderHits(cmd *cobra.Command, hits []searchindex.Hit, asJSON bool) error {
	if asJSON {
		for _, hit := range hits {
			if err := printJSONLine(cmd, hit); err != nil {
				return err
			}
		}
		return nil
	}
	width := 0
	for _, hit := range hits {
		width = max(width, len(hit.PageRef))
	}
	for _, hit := range hits {
		cmd.Printf("%-*s  %7.2f  %s  [%s]  src=%s  git=%s\n",
			width, hit.PageRef, hit.Score,
			orDash(hit.Title), orDash(hit.Volatility),
			orDash(hit.SourceDate), orDashPtr(hit.GitDate))
	}
	return nil
}

func printJSONLine(cmd *cobra.Command, value any) error {
	encoded, err := json.Marshal(value)
	if err != nil {
		return err
	}
	cmd.Println(string(encoded))
	return nil
}

// splitCommaList parses a comma-separated flag value into its non-empty,
// trimmed parts.
func splitCommaList(value string) []string {
	if value == "" {
		return nil
	}
	var out []string
	for _, part := range strings.Split(value, ",") {
		if trimmed := strings.TrimSpace(part); trimmed != "" {
			out = append(out, trimmed)
		}
	}
	return out
}

func orDash(s string) string {
	if s == "" {
		return "-"
	}
	return s
}

func orDashPtr(s *string) string {
	if s == nil {
		return "-"
	}
	return orDash(*s)
}
