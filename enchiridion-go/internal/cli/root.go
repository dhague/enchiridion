// Package cli wires up the enchiridion CLI's subcommand dispatch.
//
// Per docs/adr/0011, migration from the Python script layer is incremental
// per subcommand — this scaffolding ticket (#149) adds no subcommand logic
// of its own, only the dispatch skeleton later tickets (#150-#153) hang
// real subcommands off.
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
