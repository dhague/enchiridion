/** Session-tracker plugin for OpenCode: records every session_id as it is
 * created so /save-conversation can find the current session without guessing.
 *
 * Mirrors the Claude Code `SessionStart` hook (`hooks/store_transcript_path.py`):
 * state is one JSON file per session_id under the project's own
 * `.opencode/wiki-knowledge/sessions/` (gitignored), so parallel sessions
 * sharing a project don't clobber each other. OpenCode keeps transcripts in
 * its database rather than on disk, so this plugin records only the
 * session_id — the transcript is fetched at save time via
 * `opencode export <session_id>`.
 *
 * OpenCode records no session id in the environment of processes it spawns,
 * so the reader (`save-session-opencode.py`) would have no way to know which
 * session is current. This plugin therefore also hooks `shell.env` — every
 * shell command OpenCode runs receives `$OPENCODE_SESSION_ID` — giving the
 * reader the exact analog of Claude Code's `$CLAUDE_CODE_SESSION_ID`.
 *
 * Events are already scoped to the plugin's directory (OpenCode drops events
 * whose `location.directory` differs), but the session's own directory is
 * preferred anyway so state lands in the project the session actually
 * belongs to, mirroring the CC hook's use of the payload cwd.
 *
 * Never raises or blocks: any failure is swallowed so a broken tracker can't
 * interrupt session start.
 */
import { mkdir, writeFile } from "node:fs/promises"
import { join } from "node:path"
import type { Plugin } from "@opencode-ai/plugin"

const SESSIONS_SUBDIR = join(".opencode", "wiki-knowledge", "sessions")

export const SessionTracker: Plugin = async ({ directory }) => {
  return {
    event: async ({ event }) => {
      if (event.type !== "session.created") return
      const session = event.properties.info
      const root = session.directory || directory
      if (!session.id || !root) return
      const stateDir = join(root, SESSIONS_SUBDIR)
      try {
        await mkdir(stateDir, { recursive: true })
        await writeFile(
          join(stateDir, `${session.id}.json`),
          JSON.stringify({ session_id: session.id }),
          "utf-8",
        )
      } catch {
        // swallow — never break session start
      }
    },
    "shell.env": async (input, output) => {
      if (input.sessionID) {
        output.env.OPENCODE_SESSION_ID = input.sessionID
      }
    },
  }
}
