package cli

import (
	"fmt"
	"os"
	gopath "path"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"

	"github.com/dhague/enchiridion/enchiridion-go/internal/ingest"
	"github.com/dhague/enchiridion/enchiridion-go/internal/ingestignore"
	"github.com/dhague/enchiridion/enchiridion-go/internal/vault"
	"github.com/dhague/enchiridion/enchiridion-go/internal/vaultgit"
)

// newIngestCommand ports `wiki-plugin/scripts/ingest.py`, flag for flag, so a
// migrated SKILL.md's invocation differs only in the program name.
//
// One deliberate omission: the Python CLI prints a tool-call cost summary
// after committing when `$CLAUDE_CODE_SESSION_ID` is set. That reads a log
// the hooks write, and the hooks are ported in #153 — so it lands with them
// rather than being half-wired here.
func newIngestCommand() *cobra.Command {
	var (
		planPath      string
		ignoreRel     string
		ignoreComment string
		dryRun        bool
	)

	cmd := &cobra.Command{
		Use:   "ingest",
		Short: "Execute an IngestPlan against the resolved vault",
		Long: "Execute an IngestPlan against the resolved vault.\n\n" +
			"Validates the whole plan up front — shape, then the vault-dependent\n" +
			"checks (targets resolve, chain of evidence holds) — then writes every\n" +
			"page and commits in one pass, printing the commit SHA.\n\n" +
			"There is no rollback: a page written before a later step fails stays\n" +
			"on disk, uncommitted. Every write is idempotent, so fixing the cause\n" +
			"and rerunning is always safe.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			if dryRun && planPath == "" {
				return fmt.Errorf("--dry-run only applies to --plan; --ignore always writes")
			}
			root, err := vault.ResolveRoot("", nil)
			if err != nil {
				return err
			}
			if ignoreRel != "" {
				return ignoreRawFile(root, ignoreRel, ignoreComment)
			}
			return runPlan(cmd, root, planPath, dryRun)
		},
	}

	flags := cmd.Flags()
	flags.StringVar(&planPath, "plan", "", "path to an IngestPlan JSON file")
	flags.StringVar(&ignoreRel, "ignore", "",
		"never offer this raw/ file again for a sweep (appends it to its folder's .ingestignore)")
	flags.StringVar(&ignoreComment, "ignore-comment", "",
		"optional trailing comment for the --ignore entry")
	flags.BoolVar(&dryRun, "dry-run", false,
		"resolve and validate the plan, print what would be written, write nothing")
	cmd.MarkFlagsMutuallyExclusive("plan", "ignore")
	cmd.MarkFlagsOneRequired("plan", "ignore")

	return cmd
}

func runPlan(cmd *cobra.Command, root, planPath string, dryRun bool) error {
	file, err := os.Open(planPath)
	if err != nil {
		return err
	}
	defer file.Close()

	plan, err := ingest.DecodePlan(file)
	if err != nil {
		return err
	}
	resolved, err := ingest.Resolve(plan, root)
	if err != nil {
		return err
	}
	if err := resolved.Validate(); err != nil {
		return err
	}
	if dryRun {
		cmd.Println(resolved.Describe())
		return nil
	}

	sha, err := resolved.Execute(vaultgit.New(root))
	if err != nil {
		return err
	}
	cmd.Println(sha)
	return nil
}

// ignoreRawFile appends rawRel to its own folder's `.ingestignore`.
//
// rawRel is vault-relative, exactly as the sweep prints it
// (`raw/emails/foo.eml`), so the agent never has to split it into
// `.ingestignore`'s folder/pattern form itself.
func ignoreRawFile(root, rawRel, comment string) error {
	rel := gopath.Clean(rawRel)
	inRaw, ok := strings.CutPrefix(rel, "raw/")
	if !ok || inRaw == "" {
		return fmt.Errorf("--ignore takes a vault-relative path under raw/, got %q", rawRel)
	}
	folder := filepath.Join(root, "raw", filepath.FromSlash(gopath.Dir(inRaw)))
	return ingestignore.Append(folder, gopath.Base(inRaw), comment)
}
