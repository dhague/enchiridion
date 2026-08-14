// Package cli wires up the enchiridion CLI's subcommand dispatch.
//
// Per docs/adr/0011, migration from the Python script layer is incremental
// per subcommand: #149 laid down the dispatch skeleton, #150 hung `search`
// and `init` off it, #151 added `ingest`, and the rest land in #152-#153. Each
// subcommand is a flag-for-flag port of the Python script it replaces, so a
// migrated SKILL.md's invocation differs only in the program name.
package cli

import (
	"github.com/spf13/cobra"

	"github.com/dhague/enchiridion/enchiridion-go/internal/version"
)

func NewRootCommand() *cobra.Command {
	root := &cobra.Command{
		Use:           "enchiridion",
		Short:         "enchiridion is the wiki-knowledge plugin's script layer, as a single static binary",
		SilenceUsage:  true,
		SilenceErrors: true,
	}

	root.AddCommand(newVersionCommand())
	root.AddCommand(newSearchCommand())
	root.AddCommand(newInitCommand())
	root.AddCommand(newIngestCommand())
	root.AddCommand(newCommitCommand())
	root.AddCommand(newDiscoverCommand())
	root.AddCommand(newIngestScanCommand())
	root.AddCommand(newSupersededByCommand())
	root.AddCommand(newSaveSessionCommand())
	root.AddCommand(newToolCallStatsCommand())
	root.AddCommand(newWatchCommand())
	root.AddCommand(newHookCommand())

	return root
}

func newVersionCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "version",
		Short: "Print the enchiridion binary version",
		RunE: func(cmd *cobra.Command, args []string) error {
			cmd.Println(version.Version)
			return nil
		},
	}
}
