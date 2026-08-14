package cli

import (
	"strings"

	"github.com/spf13/cobra"

	"github.com/dhague/enchiridion/enchiridion-go/internal/place"
)

// newPlaceCommand turns a chosen kind and title into the vault-relative
// path a new page of that kind gets.
//
// *Which* kind a page belongs to is judgment and stays with the ingesting
// agent; kind + title -> `wiki/<kind-folder>/<slug>.md` is mechanics, and
// lives here so filenames are consistent regardless of who — or which model —
// is ingesting.
//
// This resolves no vault root and reads nothing from disk: only the four
// canonical kinds are accepted, never a vault's discovered custom
// kind-folders. place.Path rejects anything else.
func newPlaceCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "place <kind> <title>",
		Short: "Compute a new page's vault-relative path from its kind and title",
		Long: "Compute a new page's vault-relative path from its kind and title.\n\n" +
			"kind is one of: " + strings.Join(place.Kinds, ", "),
		Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			rel, err := place.Path(args[0], args[1], nil)
			if err != nil {
				return err
			}
			cmd.Println(rel)
			return nil
		},
	}
}
