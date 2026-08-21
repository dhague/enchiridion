/**
 * Maps a session_id to its transcript_path.
 *
 * The `SessionStart` hook writes; the save-conversation skill reads by
 * `$CLAUDE_CODE_SESSION_ID`, so it never guesses which concurrently running
 * session is "current". State lives under the *project's*
 * `.claude/wiki-knowledge/sessions/` (gitignored), not the vault — in
 * query-from-anywhere mode the vault is somewhere else entirely. One JSON file
 * per session_id, so parallel sessions sharing a project don't clobber each
 * other.
 */

import fs from "node:fs";
import path from "node:path";

/** A lookupEnv matching `process.env`'s semantics: (value, wasPresent). */
export type LookupEnv = (key: string) => [string | undefined, boolean];

/** Node's process.env, as a [value, present] pair. */
export function processLookupEnv(key: string): [string | undefined, boolean] {
  const value = process.env[key];
  return [value, value !== undefined];
}

/**
 * The sessions directory for this project.
 *
 * Resolution order, highest priority first:
 *
 *  1. root if non-empty — caller-injected (tests, the hook).
 *  2. `$CLAUDE_PROJECT_DIR` — the most reliable statement of the current
 *     project.
 *  3. The nearest ancestor of cwd containing `.claude/` — writer and reader
 *     must agree on a root even when cwd is a subdirectory.
 *  4. cwd, so a path is always returned. It may not exist yet.
 */
export function sessionsDir(
  root: string,
  cwd: string,
  lookupEnv: LookupEnv = processLookupEnv,
): string {
  let base = "";
  if (root !== "") {
    base = root;
  } else {
    const [projectDir, ok] = lookupEnv("CLAUDE_PROJECT_DIR");
    if (ok && projectDir) {
      base = projectDir;
    } else {
      let dir = cwd === "" ? process.cwd() : cwd;
      const start = dir;
      for (;;) {
        try {
          if (fs.statSync(path.join(dir, ".claude")).isDirectory()) {
            base = dir;
            break;
          }
        } catch {
          // not present — keep walking up
        }
        const parent = path.dirname(dir);
        if (parent === dir) break;
        dir = parent;
      }
      if (base === "") base = start;
    }
  }
  return path.join(base, ".claude", "wiki-knowledge", "sessions");
}

function statePath(sessionID: string, stateDir: string): string {
  return path.join(stateDir, `${sessionID}.json`);
}

/** Records transcriptPath for sessionID, creating the state directory as needed. */
export function writeTranscriptPath(
  sessionID: string,
  transcriptPath: string,
  stateDir: string,
): void {
  const file = statePath(sessionID, stateDir);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify({ transcript_path: transcriptPath }), {
    mode: 0o644,
  });
}

/**
 * The recorded transcript path for sessionID, or undefined when no state
 * exists or it is unparsable.
 */
export function readTranscriptPath(
  sessionID: string,
  stateDir: string,
): string | undefined {
  let data: string;
  try {
    data = fs.readFileSync(statePath(sessionID, stateDir), "utf8");
  } catch {
    return undefined;
  }
  try {
    const payload = JSON.parse(data) as Record<string, unknown>;
    const transcriptPath = payload["transcript_path"];
    return typeof transcriptPath === "string" ? transcriptPath : undefined;
  } catch {
    return undefined;
  }
}
