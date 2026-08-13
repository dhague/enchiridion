package cli

import (
	"time"

	"github.com/spf13/cobra"

	"github.com/dhague/enchiridion/enchiridion-go/internal/transcriptcapture"
	"github.com/dhague/enchiridion/enchiridion-go/internal/vault"
)

// newSaveSessionCommand ports `wiki-plugin/scripts/save-session-to-vault.py`:
// find, render, and write this session's transcript, printing the
// vault-relative path of the raw file written.
func newSaveSessionCommand() *cobra.Command {
	var slug string

	cmd := &cobra.Command{
		Use:   "save-session",
		Short: "Save this session's transcript as a raw file in the vault",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			root, err := vault.ResolveRoot("", nil)
			if err != nil {
				return err
			}
			rel, err := transcriptcapture.CaptureSession(root, slug, "", nil, time.Time{})
			if err != nil {
				return err
			}
			cmd.Println(rel)
			return nil
		},
	}

	cmd.Flags().StringVar(&slug, "slug", "",
		"phrase naming what this session covered; sanitized, first-save only")

	return cmd
}
