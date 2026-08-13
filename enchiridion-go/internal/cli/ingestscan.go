package cli

import (
	"strings"

	"github.com/spf13/cobra"

	"github.com/dhague/enchiridion/enchiridion-go/internal/ingestscan"
	"github.com/dhague/enchiridion/enchiridion-go/internal/vault"
)

// newIngestScanCommand ports `wiki-plugin/scripts/ingest_scan.py`, flag for
// flag: scan raw/ for files that need ingestion, tabular or JSON Lines.
func newIngestScanCommand() *cobra.Command {
	var asJSON bool

	cmd := &cobra.Command{
		Use:   "ingest-scan [folder]",
		Short: "Scan raw/ for files that need ingestion",
		Args:  cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			root, err := vault.ResolveRoot("", nil)
			if err != nil {
				return err
			}
			folder := ""
			if len(args) == 1 {
				folder = folderArg(args[0])
			}
			result, err := ingestscan.Scan(root, folder, nil)
			if err != nil {
				return err
			}
			if asJSON {
				return renderScanJSON(cmd, result)
			}
			renderScanTable(cmd, result)
			return nil
		},
	}

	cmd.Flags().BoolVar(&asJSON, "json", false,
		"emit JSON Lines (one eligible or ignored record per line)")

	return cmd
}

// folderArg normalises a CLI folder argument: "" and "raw/" both mean all of
// raw/; a "raw/" prefix is stripped, so "notes" and "raw/notes" are
// interchangeable.
func folderArg(arg string) string {
	if arg == "" || arg == "raw/" {
		return ""
	}
	return strings.TrimPrefix(arg, "raw/")
}

type eligibleRecord struct {
	Kind         string   `json:"kind"`
	RawRel       string   `json:"raw_rel"`
	Reason       string   `json:"reason"`
	BackPointers []string `json:"back_pointers"`
}

type ignoredRecord struct {
	Kind   string `json:"kind"`
	RawRel string `json:"raw_rel"`
}

func renderScanJSON(cmd *cobra.Command, result ingestscan.Result) error {
	for _, c := range result.Eligible {
		backPointers := c.BackPointers
		if backPointers == nil {
			backPointers = []string{}
		}
		if err := printJSONLine(cmd, eligibleRecord{
			Kind:         "eligible",
			RawRel:       c.RawRel,
			Reason:       c.Reason,
			BackPointers: backPointers,
		}); err != nil {
			return err
		}
	}
	for _, rawRel := range result.Ignored {
		if err := printJSONLine(cmd, ignoredRecord{Kind: "ignored", RawRel: rawRel}); err != nil {
			return err
		}
	}
	return nil
}

func renderScanTable(cmd *cobra.Command, result ingestscan.Result) {
	if len(result.Eligible) == 0 && len(result.Ignored) == 0 {
		cmd.Println("no eligible files; 0 ignored")
		return
	}
	width := 10
	for _, c := range result.Eligible {
		if len(c.RawRel) > width {
			width = len(c.RawRel)
		}
	}
	for _, r := range result.Ignored {
		if len(r) > width {
			width = len(r)
		}
	}
	for _, c := range result.Eligible {
		cmd.Printf("%-*s  %s\n", width, c.RawRel, c.Reason)
	}
	if len(result.Ignored) > 0 {
		cmd.Printf("\n%d ignored by .ingestignore:\n", len(result.Ignored))
		for _, rawRel := range result.Ignored {
			cmd.Printf("  %s\n", rawRel)
		}
	}
}
