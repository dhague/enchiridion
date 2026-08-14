package cli

import (
	"encoding/json"
	"os"
	"strings"

	"github.com/spf13/cobra"

	"github.com/dhague/enchiridion/enchiridion-go/internal/discover"
	"github.com/dhague/enchiridion/enchiridion-go/internal/ingest"
	"github.com/dhague/enchiridion/enchiridion-go/internal/searchindex"
	"github.com/dhague/enchiridion/enchiridion-go/internal/vault"
)

// newDiscoverCommand ports `wiki-plugin/scripts/discover.py`, flag for flag.
//
// Two modes. `--plan <draft.json>` discovers candidates for every page in the
// draft plus the vault's tag vocabulary; `--title`/`--summary`/`--body-file`
// is single-page mode, emitting one candidate per line.
func newDiscoverCommand() *cobra.Command {
	var (
		planPath     string
		title        string
		summary      string
		bodyFile     string
		limit        int
		dupThreshold float64
		relThreshold float64
		tagsContain  string
		tagCount     string
	)

	cmd := &cobra.Command{
		Use:   "discover",
		Short: "Find pages overlapping a planned page, plus the tag vocabulary",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			root, err := vault.ResolveRoot("", nil)
			if err != nil {
				return err
			}
			opts := discover.Options{
				Limit:              limit,
				DuplicateThreshold: dupThreshold,
				RelatedThreshold:   relThreshold,
			}
			// The one index handle for this run — one per vault at a time
			// (ADR-0010), owned here because this command is the only thing
			// that needs one.
			index, err := searchindex.Open(root, nil)
			if err != nil {
				return err
			}
			defer index.Close()

			if planPath != "" {
				return runDiscoverPlan(cmd, index, planPath, opts, tagsContain, tagCount)
			}
			body := ""
			if bodyFile != "" {
				text, err := os.ReadFile(bodyFile)
				if err != nil {
					return err
				}
				body = string(text)
			}
			candidates, err := discover.Check(index, title, summary, body, opts)
			if err != nil {
				return err
			}
			for _, c := range candidates {
				if err := printJSONLine(cmd, c); err != nil {
					return err
				}
			}
			return nil
		},
	}

	flags := cmd.Flags()
	flags.StringVar(&planPath, "plan", "", "path to a draft IngestPlan JSON; discovers candidates for every page in it, plus the vault's tag vocabulary")
	flags.StringVar(&title, "title", "", "the planned page's own title (single-page mode)")
	flags.StringVar(&summary, "summary", "", "the planned page's own summary (single-page mode)")
	flags.StringVar(&bodyFile, "body-file", "", "path to the planned page's own body text (single-page mode)")
	flags.IntVar(&limit, "limit", discover.DefaultLimit, "max candidates per page")
	flags.Float64Var(&dupThreshold, "duplicate-threshold", discover.DuplicateThreshold, "")
	flags.Float64Var(&relThreshold, "related-threshold", discover.RelatedThreshold, "")
	flags.StringVar(&tagsContain, "tags-containing", "", "comma-separated substrings (case-insensitive OR match); with --plan, replaces the full tag-vocabulary JSON dump with the plain-text list of matching vault tags")
	flags.StringVar(&tagCount, "tag-count", "", "comma-separated exact tag names; with --plan, replaces the full tag-vocabulary JSON dump with plain-text per-tag page counts (0 if the tag doesn't exist yet)")

	return cmd
}

// pagesPayload is the JSON shape discover --plan emits: one entry per planned
// page with its classified candidates.
type pagesPayload struct {
	Pages []pagePayload `json:"pages"`
}

type pagePayload struct {
	Title      string               `json:"title"`
	Candidates []discover.Candidate `json:"candidates"`
}

// planPayload adds the tag vocabulary to the pages payload, the no-flag form.
type planPayload struct {
	Pages      []pagePayload          `json:"pages"`
	Vocabulary []searchindex.TagCount `json:"vocabulary"`
}

func runDiscoverPlan(cmd *cobra.Command, index *searchindex.Index, planPath string, opts discover.Options, tagsContain, tagCount string) error {
	file, err := os.Open(planPath)
	if err != nil {
		return err
	}
	defer file.Close()

	plan, err := ingest.DecodePlan(file)
	if err != nil {
		return err
	}
	results, err := discover.Discover(index, plan.Pages, opts)
	if err != nil {
		return err
	}

	pages := make([]pagePayload, 0, len(results))
	for _, result := range results {
		pages = append(pages, pagePayload{Title: result.Title, Candidates: result.Candidates})
	}

	vocab, err := index.TagCounts()
	if err != nil {
		return err
	}

	if tagsContain == "" && tagCount == "" {
		return printIndentedJSON(cmd, planPayload{Pages: pages, Vocabulary: vocab})
	}

	if err := printIndentedJSON(cmd, pagesPayload{Pages: pages}); err != nil {
		return err
	}
	if tagsContain != "" {
		matches := discover.TagsContaining(vocab, splitCommaList(tagsContain))
		cmd.Println(pythonListRepr(matches))
	}
	if tagCount != "" {
		counts := discover.TagCounts(vocab, splitCommaList(tagCount))
		for _, tc := range counts {
			cmd.Printf("%s count: %d\n", tc.Tag, tc.Count)
		}
	}
	return nil
}

// printIndentedJSON writes value as indented JSON, the discover.py output
// shape (`json.dumps(payload, indent=2)`).
func printIndentedJSON(cmd *cobra.Command, value any) error {
	encoded, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	cmd.Println(string(encoded))
	return nil
}

// pythonListRepr renders a []string the way Python's `print(["a", "b"])`
// does — the plain-text form `--tags-containing` emits.
func pythonListRepr(items []string) string {
	if len(items) == 0 {
		return "[]"
	}
	quoted := make([]string, len(items))
	for i, item := range items {
		quoted[i] = "'" + item + "'"
	}
	return "[" + strings.Join(quoted, ", ") + "]"
}
