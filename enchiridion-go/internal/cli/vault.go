package cli

import (
	"github.com/spf13/cobra"

	"github.com/dhague/enchiridion/enchiridion-go/internal/vault"
)

// newVaultCommand ports `wiki-plugin/scripts/vault.py`'s CLI: where the vault
// is, and the one operation that needs the whole vault to be correct.
//
// Bare `enchiridion vault` prints the resolved root, as bare `vault.py` did —
// a documented surface, kept rather than made a subcommand-only spelling.
// `vault root` is the explicit form of the same thing.
func newVaultCommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "vault",
		Short: "Resolve the vault root, or move a page within it",
		Args:  cobra.NoArgs,
		RunE:  func(cmd *cobra.Command, args []string) error { return printVaultRoot(cmd) },
	}
	cmd.AddCommand(newVaultRootCommand(), newVaultMoveCommand())
	return cmd
}

func newVaultRootCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "root",
		Short: "Print the resolved vault root (the no-argument default)",
		Args:  cobra.NoArgs,
		RunE:  func(cmd *cobra.Command, args []string) error { return printVaultRoot(cmd) },
	}
}

func newVaultMoveCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "move <old_ref> <new_ref>",
		Short: "Move a page within the vault and fix every link, inbound and outbound",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			root, err := vault.ResolveRoot("", nil)
			if err != nil {
				return err
			}
			changed, err := vault.New(root).MovePage(args[0], args[1])
			if err != nil {
				return err
			}
			for _, pageRef := range changed {
				cmd.Println(pageRef)
			}
			return nil
		},
	}
}

func printVaultRoot(cmd *cobra.Command) error {
	root, err := vault.ResolveRoot("", nil)
	if err != nil {
		return err
	}
	cmd.Println(root)
	return nil
}
