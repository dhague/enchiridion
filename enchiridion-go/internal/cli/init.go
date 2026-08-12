package cli

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/dhague/enchiridion/enchiridion-go/internal/initwiki"
)

// newInitCommand ports `wiki-plugin/scripts/init_wiki.py`'s CLI. It prints
// the resolved vault root on success — the only thing on stdout, so a
// caller can capture it.
func newInitCommand() *cobra.Command {
	var mode string
	var pluginRoot string

	cmd := &cobra.Command{
		Use:   "init <path>",
		Short: "Scaffold a brand-new wiki vault",
		Long: "Scaffold a brand-new, empty wiki vault: folders, git repo, .gitignore,\n" +
			"and (for query-from-anywhere mode) the plugin-registration settings.json.\n\n" +
			"Refuses to run against a directory that already looks like a vault.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			root, err := initwiki.Init(args[0], mode, pluginRoot)
			if err != nil {
				return err
			}
			cmd.Println(root)
			return nil
		},
	}

	cmd.Flags().StringVar(&mode, "mode", "",
		fmt.Sprintf("deployment mode: one of %v", initwiki.Modes))
	cmd.Flags().StringVar(&pluginRoot, "plugin-root", "",
		"this plugin's install dir (required for query-from-anywhere)")
	_ = cmd.MarkFlagRequired("mode")

	return cmd
}
