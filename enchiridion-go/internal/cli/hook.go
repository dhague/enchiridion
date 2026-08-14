package cli

import (
	"fmt"
	"io"
	"strings"

	"github.com/spf13/cobra"

	"github.com/dhague/enchiridion/enchiridion-go/internal/hooks"
)

// newHookCommand replaces the Python hook scripts, one child subcommand per
// hook event. Each reads the hook payload as JSON on stdin.
//
// Hooks fire automatically rather than being agent-invoked, so they **fail
// open** (#153): every handler error is swallowed and the command exits 0, and
// the session continues with that hook's side effect missing for this run.
// hooks.json extends the same tolerance one level out, to the bootstrap that
// fetches this binary — a failed lazy download must not block session start
// either.
func newHookCommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "hook",
		Short: "Handle a Claude Code hook payload read from stdin",
		// A bare `hook`, or an unrecognised event name, is an error rather than
		// cobra's default "print help, exit 0" — a hooks.json typo must not
		// look like it worked.
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			return fmt.Errorf("hook: name the event, one of %s", strings.Join(hookEvents(cmd), ", "))
		},
	}

	cmd.AddCommand(hookEventCommand(
		"session-start",
		"Record this session's transcript_path for /save-conversation",
		hooks.SessionStart,
	))
	cmd.AddCommand(hookEventCommand(
		"post-tool-use",
		"Append this tool call to the session's tool-call log",
		hooks.PostToolUse,
	))

	return cmd
}

// hookEvents names the events `hook` handles, for its error message.
func hookEvents(cmd *cobra.Command) []string {
	var names []string
	for _, child := range cmd.Commands() {
		names = append(names, child.Name())
	}
	return names
}

// hookEventCommand wraps one handler as a fail-open subcommand.
func hookEventCommand(use, short string, handle func(io.Reader) error) *cobra.Command {
	return &cobra.Command{
		Use:   use,
		Short: short,
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			// The error is deliberately dropped, not reported: stderr from a
			// hook is surfaced to the user mid-session, and there is nothing
			// they can act on.
			_ = handle(cmd.InOrStdin())
			return nil
		},
	}
}
