/**
 * The plugin's automatic hook handlers.
 *
 * Both read a Claude Code hook payload as JSON on stdin and write per-session
 * state under the *project's* `.claude/wiki-knowledge/sessions/`, resolved from
 * the payload's own `cwd` (the project root at session start) rather than this
 * process's cwd — so state lands in the project the session belongs to.
 *
 * Unlike a skill, a hook runs automatically and unattended, so it must never
 * interrupt the session that triggered it (#153). These functions return errors
 * for the caller to decide about; the `hook` subcommands swallow them, and
 * hooks.json additionally tolerates the bootstrap itself failing, so a flaky
 * binary download degrades one session's side effects instead of blocking it.
 */

import fs from "node:fs";
import path from "node:path";
import { mkdirSafe } from "./fsutil.js";
import { sessionsDir, writeTranscriptPath } from "./sessionstate.js";
import { processLookupEnv } from "./sessionstate.js";
import { logPath } from "./toolcallstats.js";

/** The subset of the SessionStart payload this hook uses. */
interface SessionStartPayload {
  session_id?: string;
  transcript_path?: string;
  cwd?: string;
}

/**
 * Records this session's transcript_path so /save-conversation can retrieve it
 * later by session_id, rather than guessing "most recently modified
 * transcript" — which breaks when sessions run in parallel (#23).
 *
 * A payload missing either field is a silent no-op: there is nothing to record,
 * and creating the state directory anyway would be a lie about it.
 */
export function sessionStart(payload: unknown): void {
  const p = (payload ?? {}) as SessionStartPayload;
  if (!p.session_id || !p.transcript_path) return;
  writeTranscriptPath(
    p.session_id,
    p.transcript_path,
    sessionsDir(p.cwd ?? "", "", processLookupEnv),
  );
}

/** The subset of the PostToolUse payload this hook logs. */
interface PostToolUsePayload {
  session_id?: string;
  cwd?: string;
  tool_name?: unknown;
  tool_use_id?: unknown;
  prompt_id?: unknown;
  agent_id?: unknown;
  agent_type?: unknown;
  duration_ms?: unknown;
}

/** One line of the tool-call log. `unknown` fields, so an absent payload key
 * is logged as an explicit null rather than being dropped — toolcallstats
 * reads back a stable key set either way. */
interface LoggedCall {
  tool: unknown;
  tool_use_id: unknown;
  prompt_id: unknown;
  agent_id: unknown;
  agent_type: unknown;
  duration_ms: unknown;
}

/**
 * Appends one JSON line per tool call to the session's log, so
 * `enchiridion tool-call-stats` can summarise a run's cost (#100).
 *
 * Per #99's spike the payload carries no per-assistant-message identifier and
 * no timestamp, so tool-call count — not exact turn count — is the recoverable
 * metric. prompt_id is logged anyway as the closest available grouping key, and
 * agent_id/agent_type separate subagent calls from the top-level agent's own.
 */
export function postToolUse(payload: unknown): void {
  const p = (payload ?? {}) as PostToolUsePayload;
  if (!p.session_id) return;

  const line = JSON.stringify({
    tool: p.tool_name ?? null,
    tool_use_id: p.tool_use_id ?? null,
    prompt_id: p.prompt_id ?? null,
    agent_id: p.agent_id ?? null,
    agent_type: p.agent_type ?? null,
    duration_ms: p.duration_ms ?? null,
  } satisfies LoggedCall);

  const logFile = logPath(
    p.session_id,
    sessionsDir(p.cwd ?? "", "", processLookupEnv),
  );
  mkdirSafe(path.dirname(logFile), 0o755);
  fs.appendFileSync(logFile, line + "\n", { mode: 0o644 });
}
