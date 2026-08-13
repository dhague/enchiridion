package cli

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"

	"github.com/dhague/enchiridion/enchiridion-go/internal/toolcallstats"
)

// newToolCallStatsCommand ports `wiki-plugin/scripts/tool_call_stats.py`, flag
// for flag: summarise the tool-call log for one session.
func newToolCallStatsCommand() *cobra.Command {
	var sessionID string

	cmd := &cobra.Command{
		Use:   "tool-call-stats",
		Short: "Summarise a session's tool-call log",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			id := sessionID
			if id == "" {
				id = os.Getenv("CLAUDE_CODE_SESSION_ID")
			}
			if id == "" {
				return fmt.Errorf("no session_id — pass --session-id or set $CLAUDE_CODE_SESSION_ID")
			}
			events, err := toolcallstats.ReadLog(id, "")
			if err != nil {
				return err
			}
			if len(events) == 0 {
				return fmt.Errorf("no log found at %s", toolcallstats.LogPath(id, ""))
			}
			cmd.Println(toolcallstats.FormatSummary(toolcallstats.Summarize(events)))
			return nil
		},
	}

	cmd.Flags().StringVar(&sessionID, "session-id", "",
		"session to summarise (default: $CLAUDE_CODE_SESSION_ID)")

	return cmd
}
