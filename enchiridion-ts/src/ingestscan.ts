/**
 * The ingestion sweep — scan `raw/` for files that need ingestion. Ported from
 * enchiridion-go/internal/ingestscan.
 *
 * Two independent gates: derived done-state (computed here) and declared
 * policy (a human-authored `.ingestignore`). A raw file is *offered* when
 * (a) no wiki page's `raw_source` points at it, or (b) one does but the raw
 * file is strictly newer than that page's `git_date`, or `git status
 * --porcelain` reports it dirty.
 *
 * `.ingestignore` is read from the file's own folder only, with **no ancestor
 * walk** — the same rule `INGESTION.md` follows, and what keeps a
 * hand-written policy file from drifting into a machine-written done-list.
 * The parse/append halves live in [ingestignore], shared with `ingest
 * --ignore`.
 */

import fs from "node:fs";
import path from "node:path";
import { Vault } from "./vault.js";
import type { PageWithText } from "./vault.js";
import { VaultGit } from "./vaultgit.js";
import { Filename, parse as parseIngestignore } from "./ingestignore.js";

/** The slice of [VaultGit] the sweep needs, named as an interface so tests can
 * script the git facts rather than standing up a work tree.
 *
 * Both methods are the *lenient* surface: an absent date ("") and an unknown
 * dirty state (false) are read as "fail toward offering", the safe direction. */
export interface Git {
  /** The last commit date of rel (YYYY-MM-DD), or "" when rel was never
   * committed or root isn't a work tree. */
  lastCommitDate(rel: string): Promise<string>;
  /** Whether rel is modified or untracked. */
  porcelainMentions(rel: string): Promise<boolean>;
}

/** The two reasons a raw file is offered. */
/** No page's raw_source points at it. */
export const ReasonNeverIngested = "never-ingested";
/** Pages point at it, and it has moved on. */
export const ReasonChangedSinceIngestion = "changed-since-ingestion";

/** One raw file the sweep wants to offer. RawRel is vault-relative. */
export interface Candidate {
  /** The vault-relative path of the raw file. */
  rawRel: string;
  /** Either [ReasonNeverIngested] (BackPointers empty by construction) or
   * [ReasonChangedSinceIngestion] (BackPointers lists the pointing pages
   * vault-relative — the invoking session passes them to `wiki-ingest` as a
   * reconciliation hint). */
  reason: string;
  /** The pages whose raw_source points at RawRel. */
  backPointers: string[];
}

/** The sweep's verdict on one (vault, folder) pair.
 *
 * Eligible is in walk order. Ignored holds `.ingestignore` matches, reported
 * rather than silently dropped so the sweep can say "3 ignored". */
export interface Result {
  eligible: Candidate[];
  ignored: string[];
}

/** The raw/ files that are instructions and policy, not content. */
const skipNames = new Set<string>(["INGESTION.md", ".ingestignore"]);

/** Return every file under `root/raw/` (or `root/raw/<folder>`), as
 * vault-relative paths in sorted order.
 *
 * Skips `INGESTION.md` and `.ingestignore`. A nonexistent folder yields
 * nothing rather than an error. */
export function walkRaw(root: string, folder: string): string[] {
  let rawRoot = path.join(root, "raw");
  if (folder !== "") rawRoot = path.join(rawRoot, ...folder.split("/"));
  let info: fs.Stats;
  try {
    info = fs.statSync(rawRoot);
  } catch (err) {
    if (isENOENT(err)) return [];
    throw err;
  }
  if (!info.isDirectory()) return [];

  const rels: string[] = [];
  const walk = (dir: string): void => {
    const entries = fs
      .readdirSync(dir, { withFileTypes: true })
      .sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
    for (const entry of entries) {
      const abs = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(abs);
      } else if (!skipNames.has(entry.name)) {
        rels.push(toSlash(path.relative(root, abs)));
      }
    }
  };
  walk(rawRoot);
  return rels;
}

/** Read the `.ingestignore` in folder, if any — this folder only, no ancestor
 * walk. An empty slice when absent.
 *
 * A malformed policy file is an error, not an empty policy: silently reading
 * it as "ignore nothing" would offer every file it was meant to withdraw. */
export function loadIngestignore(folder: string): string[] {
  const filePath = path.join(folder, Filename);
  let text: string;
  try {
    text = fs.readFileSync(filePath, "utf8");
  } catch (err) {
    if (isENOENT(err)) return [];
    throw err;
  }
  return parseIngestignore(text);
}

/** Report whether filename matches any pattern, using filepath.Match-style
 * glob semantics (a bare filename or a simple glob are the only supported
 * shapes, since `/`, `!` and `**` are rejected at parse time). */
export function matchesIngestignore(
  filename: string,
  patterns: string[],
): boolean {
  for (const pattern of patterns) {
    if (globMatch(pattern, filename)) return true;
  }
  return false;
}

/** filepath.Match for the only shapes [ingestignore.parse] allows: a `*`
 * matches any sequence of non-separator characters, a `?` any single one. */
function globMatch(pattern: string, name: string): boolean {
  let re = "";
  for (const ch of pattern) {
    if (ch === "*") re += "[^/]*";
    else if (ch === "?") re += "[^/]";
    else re += ch.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }
  return new RegExp(`^${re}$`).test(name);
}

/** Return `{raw_rel: [page_ref, …]}` for every page with a raw_source. Both
 * sides vault-relative.
 *
 * pagerecord hands back each raw_source target already resolved to
 * vault-relative by construction (ADR-0009), so there is no re-resolution step
 * to write here. */
export function backPointersByRaw(
  pages: Record<string, PageWithText>,
): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const [pageRef, page] of Object.entries(pages)) {
    for (const edge of page.record.edges) {
      if (edge.key !== "raw_source") continue;
      for (const target of edge.targets) {
        (out[target] ??= []).push(pageRef);
      }
    }
  }
  return out;
}

/** Report whether rawDate > pageDate (YYYY-MM-DD lexicographic).
 *
 * An absent page date — never committed, or not a git repo — fails toward
 * true, so the file is offered rather than silently skipped. */
export function strictlyNewer(rawDate: string, pageDate: string): boolean {
  if (pageDate === "") return true;
  if (rawDate === "") return false;
  return rawDate > pageDate;
}

/** Walk `root/raw/` and return the sweep's verdict (see the module comment
 * for the eligibility rule).
 *
 * Policy trumps the eligibility signal: a file matching its own folder's
 * `.ingestignore` lands in Ignored without being evaluated.
 *
 * git is injectable for tests; pass null for the real repository at root. The
 * absent-git policy — fail toward offering — is read off [Git.lastCommitDate]
 * and [Git.porcelainMentions], whose lenient defaults this sweep relies on. */
export async function scan(
  root: string,
  folder: string,
  git: Git | null,
): Promise<Result> {
  const vault = new Vault(root);
  const pages = vault.pagesWithText();
  const backPointers = backPointersByRaw(pages);
  if (git === null) git = new VaultGit(root);

  const rels = walkRaw(root, folder);

  const result: Result = { eligible: [], ignored: [] };
  for (const rel of rels) {
    // Own folder, no ancestor walk: a raw/emails/.ingestignore does not
    // govern raw/emails/sub/ — that folder needs its own.
    const dir = path.dirname(path.join(root, ...rel.split("/")));
    let patterns: string[];
    try {
      patterns = loadIngestignore(dir);
    } catch (err) {
      throw new Error(`${rel}: ${(err as Error).message}`, { cause: err });
    }
    if (matchesIngestignore(path.basename(rel), patterns)) {
      result.ignored.push(rel);
      continue;
    }

    const pointing = backPointers[rel] ?? [];
    if (pointing.length === 0) {
      result.eligible.push({
        rawRel: rel,
        reason: ReasonNeverIngested,
        backPointers: [],
      });
      continue;
    }

    // Ingested at least once. Offer it again only if it has moved on:
    // dirty working tree, or newer than a back-pointer page.
    if (await git.porcelainMentions(rel)) {
      result.eligible.push({
        rawRel: rel,
        reason: ReasonChangedSinceIngestion,
        backPointers: pointing,
      });
      continue;
    }

    const rawDate = await git.lastCommitDate(rel);
    for (const pageRel of pointing) {
      if (strictlyNewer(rawDate, await git.lastCommitDate(pageRel))) {
        result.eligible.push({
          rawRel: rel,
          reason: ReasonChangedSinceIngestion,
          backPointers: pointing,
        });
        break;
      }
    }
  }
  return result;
}

function toSlash(p: string): string {
  return p.split(path.sep).join("/");
}

function isENOENT(err: unknown): boolean {
  return (err as NodeJS.ErrnoException).code === "ENOENT";
}
