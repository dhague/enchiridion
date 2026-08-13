package cli

import (
	"strings"

	"github.com/spf13/cobra"

	"github.com/dhague/enchiridion/enchiridion-go/internal/supersededby"
	"github.com/dhague/enchiridion/enchiridion-go/internal/vault"
)

// newSupersededByCommand ports `wiki-plugin/scripts/superseded_by.py`, flag
// for flag: resolve a candidate set's supersession chains to current heads.
func newSupersededByCommand() *cobra.Command {
	var asJSON bool

	cmd := &cobra.Command{
		Use:   "superseded-by <page_ref>...",
		Short: "Resolve page refs to their current supersession heads",
		Args:  cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			root, err := vault.ResolveRoot("", nil)
			if err != nil {
				return err
			}
			records, err := vault.New(root).Pages()
			if err != nil {
				return err
			}
			resolutions := supersededby.Resolve(args, records)

			if asJSON {
				for _, res := range resolutions {
					if err := printJSONLine(cmd, res); err != nil {
						return err
					}
				}
				return nil
			}
			for _, res := range resolutions {
				if len(res.Chain) == 0 {
					cmd.Printf("%s  (current)\n", res.Seed)
					continue
				}
				via := ""
				if len(res.Chain) > 1 {
					via = " via " + strings.Join(res.Chain[:len(res.Chain)-1], " -> ")
				}
				cmd.Printf("%s  ->  %s%s\n", res.Seed, res.Active, via)
			}
			return nil
		},
	}

	cmd.Flags().BoolVar(&asJSON, "json", false,
		"emit results as JSON Lines (one object per line)")

	return cmd
}
