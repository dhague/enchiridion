/**
 * Turns a host session transcript into a vault-ready raw markdown file.
 *
 * The capability behind the /save-conversation skill — reduce a host's wire
 * format to (role, text) turns via one adapter per host (Claude Code JSONL,
 * OpenCode export), render the turns to markdown with a host attribution
 * line, sanitise an agent-authored slug, bind a filename in the vault's raw
 * inbox. Pure or filesystem-local only; argv parsing lives in the CLI.
 *
 * **The name is bound once, at first save.** A re-save finds the existing
 * file by its short session id and rewrites it in place rather than renaming,
 * so inbound raw_source links never break.
 */

import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { mkdirSafe } from "./fsutil.js";
import os from "node:os";
import {
  sessionsDir,
  readTranscriptPath,
  processLookupEnv,
} from "./sessionstate.js";
import type { LookupEnv } from "./sessionstate.js";

/** The default cap on a sanitized slug. */
export const SLUG_MAX_LENGTH = 60;

/** Collapses runs of non-[a-z0-9] to a single separator. */
const NON_SLUG_RE = /[^a-z0-9]+/g;

/**
 * Reduces a free-text phrase to a filesystem-safe kebab-case slug.
 *
 * The phrase is model-authored, so this sanitizes rather than trusts:
 * NFKD-fold to ASCII, lowercase, collapse non-`[a-z0-9]` runs to one `-`,
 * strip the ends, cap on a word boundary where there is one. The result is
 * `[a-z0-9-]` only, so it never needs percent-encoding in a link destination.
 *
 * Returns "" when nothing survives (empty, pure punctuation,
 * non-transliterable script) — the caller then falls back to the bare
 * `<date>-<short_id>` name.
 */
export function sanitizeSlug(phrase: string, maxLength: number): string {
  const cap = maxLength <= 0 ? SLUG_MAX_LENGTH : maxLength;
  if (phrase === "") return "";

  const folded = phrase.normalize("NFKD");
  let ascii = "";
  for (const ch of folded) {
    if (ch.codePointAt(0)! < 128) ascii += ch;
  }
  const slug = ascii
    .toLowerCase()
    .replace(NON_SLUG_RE, "-")
    .replace(/^-+|-+$/g, "");
  if (slug.length <= cap) return slug;

  // Look one character past the cap: if the cut lands on a separator, the
  // word before it is whole. Otherwise fall back to the last boundary inside
  // the window, and to a hard truncation when the slug is one long word with
  // no boundary at all.
  const window = slug.slice(0, cap + 1);
  let head = "";
  const idx = window.lastIndexOf("-");
  if (idx >= 0) head = window.slice(0, idx);
  if (head === "") head = slug.slice(0, cap);
  return head.replace(/^-+|-+$/g, "");
}

/** One JSONL line of a Claude Code transcript. */
interface TranscriptEntry {
  type?: string;
  isMeta?: boolean;
  isSidechain?: boolean;
  message?: { role?: string; content?: unknown };
}

/**
 * Pulls the prose out of a transcript entry's content field.
 *
 * Two shapes: a plain string, or a list of blocks. Only `text` blocks count —
 * tool_use / tool_result / image aren't part of the conversation anyone
 * re-reads later.
 */
function extractText(content: unknown): string {
  if (typeof content === "string") return content.trim();
  if (Array.isArray(content)) {
    const parts: string[] = [];
    for (const block of content) {
      if (typeof block !== "object" || block === null) continue;
      const m = block as Record<string, unknown>;
      if (m["type"] !== "text") continue;
      const text = typeof m["text"] === "string" ? m["text"].trim() : "";
      if (text !== "") parts.push(text);
    }
    return parts.join("\n\n");
  }
  return "";
}

/**
 * One (role, text) exchange — the domain turn shape. Each host's adapter
 * (parseClaudeTranscript, normalizeExport) reduces its wire format to this,
 * and transcriptToPage renders it.
 */
export interface Turn {
  role: string;
  text: string;
}

/**
 * The Claude Code host adapter: parses a JSONL transcript into (role, text)
 * turns.
 *
 * Only user/assistant messages count; meta and sidechain entries are filtered
 * out, as is anything that isn't a `text` block — tool_use / tool_result /
 * image aren't part of the back-and-forth anyone re-reads later. Multiple
 * text blocks in one message join with a blank line. A garbled line is
 * skipped rather than fatal, so a transcript interrupted mid-write still
 * parses as far as it got.
 */
export function parseClaudeTranscript(jsonlLines: string[]): Turn[] {
  const turns: Turn[] = [];
  for (const rawLine of jsonlLines) {
    const line = rawLine.trim();
    if (line === "") continue;
    let entry: TranscriptEntry;
    try {
      entry = JSON.parse(line) as TranscriptEntry;
    } catch {
      continue;
    }
    if (entry.type !== "user" && entry.type !== "assistant") continue;
    if (entry.isMeta || entry.isSidechain) continue;
    const text = extractText(entry.message?.content);
    if (text !== "") {
      turns.push({ role: entry.message!.role!, text });
    }
  }
  return turns;
}

/**
 * Wraps the too-short-transcript failure so the CLI can print a
 * "not enough conversation to save" exit.
 */
export class ErrTooFewTurns extends Error {
  turns: number;
  minTurns: number;
  constructor(turns: number, minTurns: number) {
    super(
      `Transcript has ${turns} non-empty turn(s); need at least ${minTurns}.`,
    );
    this.turns = turns;
    this.minTurns = minTurns;
  }
}

/**
 * Renders a session transcript into a vault-ready page.
 *
 * Pure: no I/O, no env, no filesystem. turns are the domain turn shape — the
 * host adapters (parseClaudeTranscript, normalizeExport) reduce each host's
 * wire format to this before the renderer sees it. hostLabel names the host
 * in the **Source:** attribution line. Returns [filename, markdown].
 *
 * slug is a free-text phrase naming what the session covered; it is sanitized
 * here, not trusted, and a phrase that sanitizes to nothing degrades to the
 * bare `<date>-<short_id>` name. Speaker labels are parameters, not baked in,
 * so a caller can match an existing vault's captures.
 */
export function transcriptToPage(
  turns: Turn[],
  hostLabel: string,
  sessionID: string,
  now: Date,
  slug: string,
  userLabel: string,
  assistantLabel: string,
  minTurns: number,
): [string, string] {
  if (turns.length < minTurns) {
    throw new ErrTooFewTurns(turns.length, minTurns);
  }

  // The short id goes last so a re-save can find the already-bound file with
  // one '*-<short_id>.md' glob, slug present or not.
  const shortID = sessionID.split("-")[0];
  const safeSlug = sanitizeSlug(slug, SLUG_MAX_LENGTH);
  const middle = safeSlug !== "" ? `${safeSlug}-` : "";
  const filename =
    fmtDate(now, "YYYY-MM-DD-hhmm") + "-" + middle + shortID + ".md";

  const lines: string[] = [
    `# Session ${sessionID}`,
    "",
    `**Saved:** ${fmtDate(now, "YYYY-MM-DD hh:mm")}  `,
    `**Source:** ${hostLabel} session transcript (save-conversation skill, enchiridion repo)`,
    "",
    "---",
    "",
  ];
  for (const t of turns) {
    const label = t.role === "user" ? userLabel : assistantLabel;
    lines.push(`## ${label}`, "", t.text, "");
  }

  return [filename, lines.join("\n")];
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** Formats a Date per a tiny template: YYYY, MM, DD, hh, mm. */
function fmtDate(d: Date, template: string): string {
  const map: Record<string, string> = {
    YYYY: String(d.getFullYear()),
    MM: pad(d.getMonth() + 1),
    DD: pad(d.getDate()),
    hh: pad(d.getHours()),
    mm: pad(d.getMinutes()),
  };
  return template.replace(/YYYY|MM|DD|hh|mm/g, (m) => map[m]);
}

/** A capture failure; its message is user-facing. */
export class CaptureError extends Error {
  constructor(msg: string) {
    super(msg);
    this.name = "CaptureError";
  }
}

/**
 * Returns the transcriptPath for this session, or raises a CaptureError.
 *
 * Four distinct failures, kept distinct so the user can tell them apart: no
 * `$CLAUDE_CODE_SESSION_ID`; state directory not located; located but no entry
 * for this session (the SessionStart hook never ran); entry pointing at a
 * transcript that no longer exists.
 */
export function findTranscriptPath(
  cwd: string,
  lookupEnv: LookupEnv = processLookupEnv,
): string {
  const [sessionIDRaw, ok] = lookupEnv("CLAUDE_CODE_SESSION_ID");
  const sessionID = sessionIDRaw ?? "";
  if (!ok || sessionID === "") {
    throw new CaptureError(
      "$CLAUDE_CODE_SESSION_ID is not set in this environment.",
    );
  }

  const stateDir = sessionsDir("", cwd, lookupEnv);
  let stateStat: fs.Stats;
  try {
    stateStat = fs.statSync(stateDir);
  } catch {
    throw stateDirNotLocated(cwd);
  }
  if (!stateStat.isDirectory()) {
    throw stateDirNotLocated(cwd);
  }

  const transcriptPath = readTranscriptPath(sessionID, stateDir);
  if (transcriptPath === undefined) {
    throw new CaptureError(
      "No state recorded for session " +
        sessionID +
        " under " +
        stateDir +
        ". (If this session was started before the " +
        "SessionStart hook was installed, its transcript was never " +
        "recorded; start a new session and try again.)",
    );
  }

  let trStat: fs.Stats;
  try {
    trStat = fs.statSync(transcriptPath);
  } catch {
    throw new CaptureError(
      "Recorded transcript file does not exist: " + transcriptPath,
    );
  }
  if (trStat.isDirectory()) {
    throw new CaptureError(
      "Recorded transcript file does not exist: " + transcriptPath,
    );
  }

  return transcriptPath;
}

function stateDirNotLocated(cwd: string): CaptureError {
  return new CaptureError(
    "Could not locate a session state directory. Searched " +
      "$CLAUDE_PROJECT_DIR, then walked up from " +
      cwd +
      " for a '.claude/' ancestor, and did not find one. (Has the " +
      "SessionStart hook ever run in this project? Start a new session " +
      "in the project root and try again.)",
  );
}

/**
 * Writes the capture into `raw/conversations/`; returns its vault-relative
 * path.
 *
 * One file per session. An earlier capture is found by globbing
 * `*-<short_id>.md` and its path reused *verbatim* — same timestamp, same
 * slug — with contents rewritten in place; filename is used only when nothing
 * is found. So no raw file is ever renamed, and inbound raw_source links stay
 * valid with no link rewriting.
 */
export function writeCapture(
  wikiRoot: string,
  filename: string,
  markdown: string,
  shortID: string,
): string {
  const conversationsDir = path.join(wikiRoot, "raw", "conversations");
  mkdirSafe(conversationsDir);

  let matches: string[] = [];
  try {
    matches = fs
      .readdirSync(conversationsDir)
      .filter((f) => f.endsWith(`-${shortID}.md`))
      .sort();
  } catch {
    // directory just created above; no matches
  }
  let outPath = path.join(conversationsDir, filename);
  if (matches.length > 0) {
    outPath = path.join(conversationsDir, matches[0]);
  }

  fs.writeFileSync(outPath, markdown, { mode: 0o644 });

  return path.relative(wikiRoot, outPath).split(path.sep).join("/");
}

/**
 * Finds, renders, and writes this session's transcript; returns its
 * vault-relative path.
 *
 * **The host is detected here**, from which session-id variable the environment
 * carries — Claude Code's `$CLAUDE_CODE_SESSION_ID` or OpenCode's
 * `$OPENCODE_SESSION_ID` — so /save-conversation stays host-neutral and one
 * subcommand serves both. Everything downstream of the host-specific fetch is
 * shared verbatim.
 *
 * **Both can be set at once**, because one host can be run from the other's
 * shell and env vars are inherited by every descendant. Env alone can't say
 * which host is the innermost one, so the tie is broken on evidence instead:
 * OpenCode wins only when its session-tracker plugin actually recorded *that*
 * session id in *this* project, which a leaked variable from an unrelated
 * project or an outer OpenCode process will not satisfy. Otherwise Claude Code,
 * the host whose hook recorded a transcript path on disk.
 *
 * Raises CaptureError with a user-facing message on any failure.
 */
export async function captureSession(
  wikiRoot: string,
  slug: string,
  cwd: string,
  lookupEnv: LookupEnv = processLookupEnv,
  now: Date,
  exportSeam?: Exporter,
): Promise<string> {
  const [claudeCodeID] = lookupEnv("CLAUDE_CODE_SESSION_ID");
  const [openCodeID] = lookupEnv("OPENCODE_SESSION_ID");
  if (openCodeID && !claudeCodeID) {
    return captureOpenCodeSession(
      wikiRoot,
      slug,
      cwd,
      lookupEnv,
      now,
      exportSeam,
    );
  }
  if (openCodeID) {
    try {
      findOpenCodeSessionID(cwd, lookupEnv);
      return captureOpenCodeSession(
        wikiRoot,
        slug,
        cwd,
        lookupEnv,
        now,
        exportSeam,
      );
    } catch {
      // fall through to Claude Code
    }
    return captureClaudeCodeSession(wikiRoot, slug, cwd, lookupEnv, now);
  }
  if (claudeCodeID) {
    return captureClaudeCodeSession(wikiRoot, slug, cwd, lookupEnv, now);
  }
  throw new CaptureError(
    "Neither $CLAUDE_CODE_SESSION_ID nor " +
      "$OPENCODE_SESSION_ID is set in this environment, so there is no way to " +
      "tell which session to save. (Claude Code sets the first; OpenCode's " +
      "session-tracker plugin injects the second.)",
  );
}

/**
 * The Claude Code host path: the SessionStart hook recorded a transcript
 * file, so this is findTranscriptPath -> parseClaudeTranscript ->
 * transcriptToPage -> writeCapture with no subprocess involved.
 */
function captureClaudeCodeSession(
  wikiRoot: string,
  slug: string,
  cwd: string,
  lookupEnv: LookupEnv,
  now: Date,
): string {
  const transcriptPath = findTranscriptPath(cwd, lookupEnv);
  const timestamp = now.getTime() === 0 ? new Date() : now;

  let text: string;
  try {
    text = fs.readFileSync(transcriptPath, "utf8");
  } catch (err) {
    throw new CaptureError(`Could not read transcript: ${errMsg(err)}`);
  }

  const base = path.basename(transcriptPath);
  const ext = path.extname(transcriptPath);
  const sessionID = ext !== "" ? base.slice(0, -ext.length) : base;
  const turns = parseClaudeTranscript(text.split("\n"));
  let filename: string;
  let markdown: string;
  try {
    [filename, markdown] = transcriptToPage(
      turns,
      "Claude Code",
      sessionID,
      timestamp,
      slug,
      "User",
      "Claude",
      2,
    );
  } catch (err) {
    throw new CaptureError("Not enough conversation to save: " + errMsg(err));
  }

  const shortID = sessionID.split("-")[0];
  return writeCapture(wikiRoot, filename, markdown, shortID);
}

function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// ---------------------------------------------------------------------------
// OpenCode host support
// ---------------------------------------------------------------------------

/** Where the session-tracker plugin writes its state. */
const OPEN_CODE_SESSIONS_SUBDIR = path.join(
  ".opencode",
  "wiki-knowledge",
  "sessions",
);

/**
 * The session-tracker state dir for this project: the nearest ancestor of cwd
 * containing `.opencode/` — writer and reader must agree even when cwd is a
 * subdirectory — and cwd itself when no ancestor holds the marker, so a path
 * is always returned (it may not exist yet).
 */
function openCodeSessionsDir(cwd: string): string {
  let dir = cwd === "" ? process.cwd() : cwd;
  let base = dir;
  for (;;) {
    try {
      if (fs.statSync(path.join(dir, ".opencode")).isDirectory()) {
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
  return path.join(base, OPEN_CODE_SESSIONS_SUBDIR);
}

/**
 * Whether the tracker recorded this session: the `<id>.json` file exists,
 * parses, and names sessionID back. A corrupt file counts as untracked,
 * mirroring readTranscriptPath's JSON-decode guard.
 */
function openCodeSessionIsTracked(
  sessionID: string,
  stateDir: string,
): boolean {
  let data: string;
  try {
    data = fs.readFileSync(path.join(stateDir, `${sessionID}.json`), "utf8");
  } catch {
    return false;
  }
  try {
    const payload = JSON.parse(data) as Record<string, unknown>;
    return payload["session_id"] === sessionID;
  } catch {
    return false;
  }
}

/**
 * Returns the sessionID if tracked, or raises a CaptureError.
 *
 * Three distinct failures, kept distinct so the user can tell them apart: no
 * `$OPENCODE_SESSION_ID` (the session-tracker plugin's `shell.env` hook must
 * inject it); state directory not located (no `.opencode/` ancestor of cwd, so
 * the plugin has never recorded state in this project); located but no entry
 * for this session (started before the plugin was installed).
 */
export function findOpenCodeSessionID(
  cwd: string,
  lookupEnv: LookupEnv = processLookupEnv,
): string {
  const [sessionIDRaw, ok] = lookupEnv("OPENCODE_SESSION_ID");
  const sessionID = sessionIDRaw ?? "";
  if (!ok || sessionID === "") {
    throw new CaptureError(
      "$OPENCODE_SESSION_ID is not set in this environment. (The " +
        "session-tracker plugin's shell.env hook injects it; is the plugin " +
        "installed and loaded in this project?)",
    );
  }

  const stateDir = openCodeSessionsDir(cwd);
  let stat: fs.Stats;
  try {
    stat = fs.statSync(stateDir);
  } catch {
    throw openCodeStateNotLocated(cwd, stateDir);
  }
  if (!stat.isDirectory()) {
    throw openCodeStateNotLocated(cwd, stateDir);
  }

  if (!openCodeSessionIsTracked(sessionID, stateDir)) {
    throw new CaptureError(
      "No state recorded for session " +
        sessionID +
        " under " +
        stateDir +
        ", per the session-tracker plugin. (If this session was " +
        "started before the plugin was installed, it was never recorded; " +
        "start a new session and try again.)",
    );
  }

  return sessionID;
}

function openCodeStateNotLocated(cwd: string, stateDir: string): CaptureError {
  return new CaptureError(
    "Could not locate OpenCode session-tracker state. Searched " +
      cwd +
      " and its ancestors for a '.opencode/' directory, and found no " +
      stateDir +
      ". (Has the session-tracker plugin ever run in this " +
      "project? Start a new session in the project root and try again.)",
  );
}

/** Fetches one OpenCode session's export document. Injectable so the pipeline
 * can be tested without the `opencode` CLI. */
export type Exporter = (sessionID: string) => Promise<Uint8Array>;

/**
 * Runs `<command> export <sessionID>` and returns its stdout.
 *
 * **Strict:** errors when the CLI is absent from PATH or the command exits
 * non-zero.
 *
 * `opencode export` truncates its JSON when stdout is a pipe (observed on
 * 1.18.15: output stops ~64KB in), so stdout is written to a real temp file and
 * read back from there — a file redirect carries the whole transcript.
 */
export async function exportTranscript(
  sessionID: string,
  command: string,
): Promise<Uint8Array> {
  let bin = command;
  if (bin === "") bin = "opencode";
  const resolved = findExecutable(bin);
  if (!resolved) {
    throw new CaptureError(`${bin} CLI is required but was not found on PATH`);
  }
  bin = resolved;

  const tmp = path.join(
    os.tmpdir(),
    `opencode-export-${process.pid}-${Date.now()}-${Math.random()
      .toString(36)
      .slice(2)}.json`,
  );

  let stderr = "";
  try {
    await runExport(bin, sessionID, tmp, (chunk) => (stderr += chunk));
    return fs.readFileSync(tmp);
  } finally {
    try {
      fs.rmSync(tmp, { force: true });
    } catch {
      // best-effort cleanup
    }
  }
}

/** Spawns `<bin> export <sessionID>` with stdout redirected to a file. */
function runExport(
  bin: string,
  sessionID: string,
  tmpPath: string,
  onStderr: (chunk: string) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    let fd: number;
    try {
      fd = fs.openSync(tmpPath, "w");
    } catch (err) {
      reject(
        new CaptureError(
          `Could not create a temp file for the export: ${errMsg(err)}`,
        ),
      );
      return;
    }
    let stderr = "";
    const child = spawn(bin, ["export", sessionID], {
      stdio: ["ignore", fd, "pipe"],
    });
    child.stderr?.on("data", (chunk: Buffer) => {
      const text = chunk.toString();
      stderr += text;
      onStderr(text);
    });
    child.on("error", (err) => {
      fs.closeSync(fd);
      reject(err);
    });
    child.on("close", (code, signal) => {
      try {
        fs.closeSync(fd);
      } catch {
        // already closed
      }
      if (code !== 0) {
        reject(
          new CaptureError(
            `opencode export failed (${code ?? signal}): ${stderr.trim()}`,
          ),
        );
        return;
      }
      resolve();
    });
  });
}

/** Minimal PATH lookup matching `exec.LookPath` semantics. */
function findExecutable(name: string): string | null {
  if (path.isAbsolute(name)) {
    return isExecutableFile(name) ? name : null;
  }
  const pathEnv = process.env.PATH ?? "";
  for (const dir of pathEnv.split(path.delimiter)) {
    if (dir === "") continue;
    const candidate = path.join(dir, name);
    if (isExecutableFile(candidate)) return candidate;
  }
  return null;
}

function isExecutableFile(file: string): boolean {
  try {
    const stat = fs.statSync(file);
    if (!stat.isFile()) return false;
    if (process.platform === "win32") return true;
    return (stat.mode & 0o111) !== 0;
  } catch {
    return false;
  }
}

/**
 * Maps an `opencode export` document into (role, text) turns.
 *
 * The export is `info` + `messages[{info:{role}, parts[{type:"text"}]}]`. Only
 * user/assistant messages and `type: "text"` parts count — tool calls,
 * reasoning, step markers, and patches are not the back-and-forth anyone
 * re-reads later. Sub-agent work runs in its own OpenCode session, so it never
 * appears in a parent session's export and needs no sidechain filter here.
 * Multiple text parts in one message join with a blank line.
 *
 * Malformed messages and parts are skipped rather than fatal, mirroring
 * parseClaudeTranscript's tolerance of a garbled JSONL line; only a document
 * that is not a JSON object at all is an error.
 */
export function normalizeExport(exportDoc: Uint8Array): Turn[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(new TextDecoder().decode(exportDoc));
  } catch {
    throw new CaptureError("opencode export returned invalid JSON");
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new CaptureError("opencode export returned an unexpected shape");
  }
  const document = parsed as Record<string, unknown>;

  // A messages key of the wrong shape leaves the list empty rather than
  // failing — same tolerance the per-message decode below applies.
  const rawMessages = document["messages"];
  const messages = Array.isArray(rawMessages) ? rawMessages : [];

  const turns: Turn[] = [];
  for (const raw of messages) {
    if (typeof raw !== "object" || raw === null) continue;
    const message = raw as Record<string, unknown>;
    const info = message["info"];
    const role =
      typeof info === "object" && info !== null
        ? (info as Record<string, unknown>)["role"]
        : undefined;
    if (role !== "user" && role !== "assistant") continue;
    const parts = message["parts"];
    const partList = Array.isArray(parts) ? parts : [];
    const texts: string[] = [];
    for (const rawPart of partList) {
      if (typeof rawPart !== "object" || rawPart === null) continue;
      const part = rawPart as Record<string, unknown>;
      if (part["type"] !== "text") continue;
      const text = typeof part["text"] === "string" ? part["text"].trim() : "";
      if (text !== "") texts.push(text);
    }
    if (texts.length > 0) {
      turns.push({ role: role as string, text: texts.join("\n\n") });
    }
  }
  return turns;
}

/**
 * Resolves the current OpenCode session, exports and normalizes its
 * transcript, and writes the capture; returns its vault-relative path.
 *
 * The whole pipeline (findOpenCodeSessionID -> export -> normalizeExport ->
 * transcriptToPage -> writeCapture) in one call. exportSeam is the injectable
 * fetch seam; undefined runs the real `opencode export`.
 */
export async function captureOpenCodeSession(
  wikiRoot: string,
  slug: string,
  cwd: string,
  lookupEnv: LookupEnv,
  now: Date,
  exportSeam?: Exporter,
): Promise<string> {
  const sessionID = findOpenCodeSessionID(cwd, lookupEnv);
  const timestamp = now.getTime() === 0 ? new Date() : now;
  const fetch =
    exportSeam ?? ((id: string) => exportTranscript(id, "opencode"));

  const document = await fetch(sessionID);
  const turns = normalizeExport(document);

  let filename: string;
  let markdown: string;
  try {
    [filename, markdown] = transcriptToPage(
      turns,
      "OpenCode",
      sessionID,
      timestamp,
      slug,
      "User",
      "Claude",
      2,
    );
  } catch (err) {
    throw new CaptureError("Not enough conversation to save: " + errMsg(err));
  }

  const shortID = sessionID.split("-")[0];
  return writeCapture(wikiRoot, filename, markdown, shortID);
}
