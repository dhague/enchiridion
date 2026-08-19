/**
 * Summarises the tool-call log written by the PostToolUse hook. Ported from
 * enchiridion-go/internal/toolcallstats (toolcallstats.go).
 *
 * Makes a run's cost visible: total tool calls, a per-tool histogram, and a
 * prompt count with calls-per-prompt.
 *
 * **"Prompts" is a proxy, not a turn count** (#99). The PostToolUse payload
 * carries no per-assistant-message identifier and no timestamp, so exact
 * assistant turns aren't recoverable. prompt_id is the closest grouping key
 * available, but it spans a whole user-prompt turn — which may itself cover
 * several assistant turns. Labelled honestly wherever it's printed.
 */

import fs from "node:fs";
import path from "node:path";
import { sessionsDir, processLookupEnv } from "./sessionstate.js";

/** The tool-call log path for sessionID under stateDir. An empty stateDir
 * resolves the session state directory. */
export function logPath(sessionID: string, stateDir: string): string {
  const dir =
    stateDir === "" ? sessionsDir("", "", processLookupEnv) : stateDir;
  return path.join(dir, `${sessionID}-tool-calls.jsonl`);
}

/**
 * The logged events for sessionID, oldest first. Empty array when no log
 * exists. Malformed or blank lines are skipped.
 */
export function readLog(
  sessionID: string,
  stateDir: string,
): Array<Record<string, unknown>> {
  const file = logPath(sessionID, stateDir);
  if (!fs.existsSync(file)) return [];
  const data = fs.readFileSync(file, "utf8");
  const events: Array<Record<string, unknown>> = [];
  for (const line of data.split("\n")) {
    if (line.trim() === "") continue;
    try {
      events.push(JSON.parse(line) as Record<string, unknown>);
    } catch {
      // malformed line — skip
    }
  }
  return events;
}

/** One tool with the number of calls, in histogram order. */
export interface ToolCount {
  tool: string;
  count: number;
}

/** The aggregate of one run's tool-call log. */
export interface Summary {
  total: number;
  /** The per-tool histogram, most-called first (ties broken by first-seen order). */
  byTool: ToolCount[];
  /** The prompt-count proxy; see the module comment. */
  prompts: number;
  /** Valid only when hasCallsPerPrompt is true (there is at least one prompt to divide by). */
  callsPerPrompt: number;
  hasCallsPerPrompt: boolean;
}

/** Aggregates events into totals, a per-tool histogram, and the prompt-count proxy. */
export function summarize(events: Array<Record<string, unknown>>): Summary {
  const total = events.length;

  const counts = new Map<string, number>();
  const order: string[] = [];
  const promptIDs = new Set<string>();
  for (const event of events) {
    let tool = typeof event["tool"] === "string" ? event["tool"] : "";
    if (tool === "") tool = "?";
    if (!counts.has(tool)) order.push(tool);
    counts.set(tool, (counts.get(tool) ?? 0) + 1);
    const id = event["prompt_id"];
    if (typeof id === "string" && id !== "") promptIDs.add(id);
  }

  const byTool: ToolCount[] = order.map((tool) => ({
    tool,
    count: counts.get(tool) ?? 0,
  }));
  // Stable sort by count descending, preserving first-seen order on ties.
  byTool.sort((a, b) => b.count - a.count);

  const prompts = promptIDs.size;
  const s: Summary = {
    total,
    byTool,
    prompts,
    callsPerPrompt: 0,
    hasCallsPerPrompt: false,
  };
  if (prompts > 0) {
    s.callsPerPrompt = total / prompts;
    s.hasCallsPerPrompt = true;
  }
  return s;
}

/** Renders a summary as the fixed text the CLI prints. */
export function formatSummary(s: Summary): string {
  let out = `Total tool calls: ${s.total}\n`;
  for (const tc of s.byTool) {
    out += `  ${String(tc.count).padStart(3)}  ${tc.tool}\n`;
  }
  if (s.prompts > 0) {
    out +=
      `Prompts (proxy for turns, not exact — see #99): ${s.prompts}, ` +
      `${s.callsPerPrompt.toFixed(1)} calls/prompt`;
  }
  return out;
}
