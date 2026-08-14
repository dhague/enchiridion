// Package cli wires up the enchiridion CLI's subcommand dispatch, one file
// per subcommand (see docs/adr/0011 for the design rationale).
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
	root.AddCommand(newVaultCommand())
	root.AddCommand(newPageCommand())
	root.AddCommand(newPlaceCommand())

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
