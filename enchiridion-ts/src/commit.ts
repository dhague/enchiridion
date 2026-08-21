/**
 * commit — write one structured git commit per ingestion/edit.
 *
 * The commit message is a compounding asset — audit log, "what changed this
 * week" feed, manager-report source — so it is emitted here, never freehand
 * by the agent. This doc comment is the format's only specification:
 *
 *	ingest: <source doc title>
 *
 *	created: wiki/concepts/prepared-statements.md
 *	updated: wiki/concepts/db-connection-pooling.md
 *	superseded: wiki/sources/deploy-capistrano.md -> wiki/sources/deploy-github-actions.md
 *	source-date: 2026-03-01
 *
 * Git is a **hard dependency**: a root that isn't a work tree is an error,
 * never a silent skip — the time model depends on the history being
 * complete.
 *
 * A manifest naming a RawSource is additionally gated on
 * [checkChainOfEvidence], failing before anything is staged. This is the
 * hard block; the ingest package runs the same check earlier, at
 * plan-validation time, as a courtesy to the agent.
 */

import fs from "node:fs";
import path from "node:path";
import { Page } from "./wikipage.js";
import { check } from "./chainofevidence.js";

/** Thrown when a manifest fails the chain-of-evidence gate.
 *
 * Distinct from a git failure: a rejected manifest is a planning bug, a git
 * failure is an environment problem.
 */
export class ErrGate extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ErrGate";
  }
}

/** The slice of [VaultGit] this module needs, named as an interface so tests
 * can commit against an in-memory fake instead of a real repository. */
export interface Git {
  isWorkTree(): Promise<boolean>;
  add(paths: string[]): Promise<void>;
  commit(message: string): Promise<string>;
}

/** One `old -> new` pair in a manifest. */
export interface Supersession {
  old: string;
  new: string;
}

/** The deterministic description of one ingestion/edit's touched files. */
export interface Manifest {
  title: string;
  action?: string;
  created?: string[];
  updated?: string[];
  superseded?: Supersession[];
  source_date?: string;
  /** The raw/ artifact this ingestion is sourced from, if any. Staged
   * automatically, so the source document always lands in the same commit as
   * the pages it produced. */
  raw_source?: string;
}

/** The verb a manifest that names none commits under. */
const defaultAction = "ingest";

/** Return every path this manifest touches, de-duplicated, in a stable order. */
export function stagedPaths(m: Manifest): string[] {
  const paths: string[] = [];
  paths.push(...(m.created ?? []));
  paths.push(...(m.updated ?? []));
  for (const s of m.superseded ?? []) paths.push(s.old, s.new);
  if (m.raw_source) paths.push(m.raw_source);

  const seen = new Set<string>();
  const ordered: string[] = [];
  for (const p of paths) {
    if (!seen.has(p)) {
      seen.add(p);
      ordered.push(p);
    }
  }
  return ordered;
}

/** Render manifest to the structured commit message (see the package comment
 * for the format). Deterministic. */
export function buildMessage(m: Manifest): string {
  const action = m.action === "" ? defaultAction : (m.action ?? defaultAction);
  const lines: string[] = [`${action}: ${m.title}`, ""];
  for (const pageRef of m.created ?? []) lines.push(`created: ${pageRef}`);
  for (const pageRef of m.updated ?? []) lines.push(`updated: ${pageRef}`);
  for (const s of m.superseded ?? [])
    lines.push(`superseded: ${s.old} -> ${s.new}`);
  if (m.source_date) lines.push(`source-date: ${m.source_date}`);
  return lines.join("\n") + "\n";
}

/**
 * Gate the commit on [check].
 *
 * A no-op when RawSource is unset (a synthesis save has no raw artifact to
 * demand a stub for). Pages are read from disk — the caller has already
 * written them by the time [commit] runs. A staged page missing from disk is
 * silently skipped: that's the caller's bug to report, not this gate's.
 */
async function checkChainOfEvidence(root: string, m: Manifest): Promise<void> {
  if (!m.raw_source) return;

  const staged: Record<string, Page> = {};
  for (const pageRef of [...(m.created ?? []), ...(m.updated ?? [])]) {
    const abs = path.join(root, ...pageRef.split("/"));
    let text: string;
    try {
      text = fs.readFileSync(abs, "utf8");
    } catch {
      continue;
    }
    staged[pageRef] = new Page(text);
  }

  const problems = check(staged, m.raw_source);
  if (problems.length > 0) {
    throw new ErrGate(`commit gated: ${problems.join("; ")}`);
  }
}

/**
 * Stage the manifest's paths and write one structured commit, returning the
 * SHA.
 *
 * git is injectable for tests; pass a [VaultGit] over root in production. Git
 * stays a hard dependency: a root that isn't a work tree is an error, not a
 * skip.
 */
export async function commit(
  root: string,
  m: Manifest,
  git: Git,
): Promise<string> {
  if (!(await git.isWorkTree())) {
    throw new Error(
      `${root} is not a git work tree; the vault's history is not optional`,
    );
  }
  await checkChainOfEvidence(root, m);
  const paths = stagedPaths(m);
  if (paths.length > 0) {
    await git.add(paths);
  }
  return git.commit(buildMessage(m));
}
