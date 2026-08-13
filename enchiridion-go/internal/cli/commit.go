package cli

import (
	"encoding/json"
	"os"

	"github.com/spf13/cobra"

	"github.com/dhague/enchiridion/enchiridion-go/internal/commit"
	"github.com/dhague/enchiridion/enchiridion-go/internal/vault"
	"github.com/dhague/enchiridion/enchiridion-go/internal/vaultgit"
)

// newCommitCommand ports `wiki-plugin/scripts/commit.py`, flag for flag: a
// hand-built manifest in, one structured commit out. `enchiridion ingest`
// commits its own plan; this is for a manifest an agent assembles directly.
func newCommitCommand() *cobra.Command {
	var manifestPath string

	cmd := &cobra.Command{
		Use:   "commit",
		Short: "Write one structured git commit per manifest",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			root, err := vault.ResolveRoot("", nil)
			if err != nil {
				return err
			}
			file, err := os.Open(manifestPath)
			if err != nil {
				return err
			}
			defer file.Close()

			var manifest commit.Manifest
			if err := json.NewDecoder(file).Decode(&manifest); err != nil {
				return err
			}
			sha, err := commit.Commit(root, manifest, vaultgit.New(root))
			if err != nil {
				return err
			}
			cmd.Println(sha)
			return nil
		},
	}

	cmd.Flags().StringVar(&manifestPath, "manifest", "", "path to a manifest JSON file")
	_ = cmd.MarkFlagRequired("manifest")

	return cmd
}
