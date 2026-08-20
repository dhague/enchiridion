/**
 * vaultgit — the one module for git facts about the vault (#126), ported from
 * enchiridion-go/internal/vaultgit onto isomorphic-git (ADR-0017, #256).
 *
 * Each caller's absent-git policy reads as one of two surfaces, mirroring the
 * Go original:
 *
 *   - **Strict** — `VaultGit.init`, `VaultGit.add`, `VaultGit.commit`: throw
 *     an error when the operation can't be performed. This is `commit`'s "git
 *     is a hard dependency" reading.
 *   - **Lenient** — `VaultGit.isWorkTree`, `VaultGit.committedPages`,
 *     `VaultGit.lastCommitDate`, `VaultGit.porcelainMentions`: a missing or
 *     broken repository yields the documented default (false / an empty
 *     Snapshot / "") rather than throwing. `search` reads "no commits means
 *     nothing to index, never a failure" off this, and a lenient method never
 *     throws.
 *
 * ADR-0015: content is always read from HEAD's git blobs, never from
 * intermediate commits or files on disk.
 */

import * as git from "isomorphic-git";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * One `wiki/**.md` page's committed state as of a `Snapshot`'s `head`.
 */
export interface PageChange {
  /** Vault-relative (ADR-0009). */
  pageRef: string;
  /**
   * Latest non-merge commit date touching this page (YYYY-MM-DD), or "" if
   * it can't be attributed within the read that produced this Snapshot.
   */
  date: string;
  /**
   * The page's bytes at HEAD — always read from HEAD's tree, never from the
   * intermediate commit that changed it. Empty when `deleted`.
   */
  content: string;
  /** Whether the page no longer exists in HEAD's tree. */
  deleted: boolean;
}

/** One `VaultGit.committedPages` read. */
export interface Snapshot {
  /** Resolved HEAD commit SHA, or "" for a repo with no commits. */
  head: string;
  /**
   * Whether this read fell back to (or was asked for) a full tree read rather
   * than an enumerated delta — `pages` then holds every `wiki/**.md` page in
   * HEAD's tree, not a changed subset.
   */
  fullRebuild: boolean;
  /** Per-page delta (or, when fullRebuild, the whole tree). */
  pages: PageChange[];
}

/**
 * The read-only git surface a consumer (here the search index) needs.
 *
 * Defined here per the consumer-first convention (ADR-0017/CLAUDE.md): the
 * interface lives in the module that owns the type it returns, and consumers
 * depend on it; an implementing class satisfies it structurally.
 */
export interface Git {
  committedPages(since: string): Promise<Snapshot>;
}

/** Error thrown by a strict method (init/add/commit) on failure. */
export class VaultGitError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "VaultGitError";
  }
}

// ---------------------------------------------------------------------------
// VaultGit
// ---------------------------------------------------------------------------

/**
 * Git verbs and facts over one vault root, backed by isomorphic-git.
 *
 * Constructing one never touches the filesystem — it just pins the root, and
 * `root` is never resolved or validated here. All probing is lazy, so a
 * caller can build one, ask an availability question, and never pay for
 * opening a repository if git isn't needed.
 */
export class VaultGit implements Git {
  constructor(private readonly root: string) {}

  // -- Strict: throw on failure ---------------------------------------------

  /** Initialise a git repository at root. Strict: throws on failure. */
  async init(): Promise<void> {
    try {
      await git.init({ fs, dir: this.root });
    } catch (err) {
      throw new VaultGitError(`git init ${this.root}: ${messageOf(err)}`);
    }
  }

  /**
   * Stage vault-relative paths (a directory is staged recursively).
   * Strict: throws on failure.
   */
  async add(paths: string[]): Promise<void> {
    for (const path of paths) {
      try {
        await git.add({ fs, dir: this.root, filepath: path });
      } catch (err) {
        throw new VaultGitError(`git add ${path}: ${messageOf(err)}`);
      }
    }
    // isomorphic-git's `git.add` stages additions/modifications but not
    // removals — a deleted-but-tracked file stays in the index. Stage the
    // removals explicitly, matching go-git's `worktree.Add` (which does).
    await this.stageRemovals(paths);
  }

  /** Remove from the index any tracked file, under a staged path, missing on disk. */
  private async stageRemovals(paths: string[]): Promise<void> {
    let tracked: string[];
    try {
      tracked = await git.listFiles({ fs, dir: this.root, ref: "HEAD" });
    } catch {
      // No HEAD yet (first commit) — nothing is tracked, so nothing to remove.
      return;
    }
    for (const file of tracked) {
      if (!coveredByPaths(file, paths)) continue;
      if (!fs.existsSync(path.join(this.root, file))) {
        await git.remove({ fs, dir: this.root, filepath: file });
      }
    }
  }

  /** Write one commit with `message` and return its SHA. Strict: throws. */
  async commit(message: string): Promise<string> {
    const signature = await this.signature();
    try {
      // Match go-git's `worktree.Commit` (which refuses an empty commit via
      // its AllowEmptyCommits=false default): make sure something is staged
      // against HEAD before committing. statusMatrix mis-reports staged
      // deletions (an index removal reports STAGE == HEAD), so compare the
      // HEAD tree to the index at the blob level instead.
      if (!(await this.hasStagedChanges())) {
        throw new VaultGitError("git commit: nothing to commit");
      }
      return await git.commit({
        fs,
        dir: this.root,
        message,
        author: signature,
        committer: signature,
      });
    } catch (err) {
      if (err instanceof VaultGitError) throw err;
      throw new VaultGitError(`git commit: ${messageOf(err)}`);
    }
  }

  /** Whether any blob differs between HEAD's tree and the staged index. */
  private async hasStagedChanges(): Promise<boolean> {
    let staged = false;
    await git.walk({
      fs,
      dir: this.root,
      trees: [git.TREE({ ref: "HEAD" }), git.STAGE()],
      map: async (
        filepath: string,
        [head, stage]: (git.WalkerEntry | null)[],
      ) => {
        const headType = head ? await head.type() : null;
        const stageType = stage ? await stage.type() : null;
        // Only blobs have meaningful on-disk content worth committing; a tree
        // oid difference alone isn't a reliable "staged change" signal.
        if (headType === "blob" || stageType === "blob") {
          const headOid = head ? await head.oid() : null;
          const stageOid = stage ? await stage.oid() : null;
          if (headOid !== stageOid) staged = true;
        }
        // Keep descending into directories on either side.
        return headType === "tree" || stageType === "tree" ? filepath : null;
      },
    });
    return staged;
  }

  // -- Lenient: never throw, return the documented default -------------------

  /** Whether root is a git work tree. Lenient: false when absent/unreadable. */
  async isWorkTree(): Promise<boolean> {
    try {
      await git.findRoot({ fs, filepath: this.root });
      return true;
    } catch {
      return false;
    }
  }

  /**
   * The vault's `wiki/**.md` pages changed since commit `since`, read from
   * HEAD's tree. `since == ""` means "all of HEAD's tree", so a first build
   * and a full rebuild are the same call.
   *
   * Lenient: a missing repository or a repository with no commits yields an
   * empty Snapshot (`head == ""`), never an error.
   *
   * Reachability is not a separate query: the range walk stops the moment it
   * finds `since`, and reaching a history that doesn't contain it — an
   * unreachable or unrecognised watermark, from an amend, rebase, `reset
   * --hard`, or a re-clone over an existing index — falls back to a full tree
   * read (`fullRebuild == true`).
   */
  async committedPages(since: string): Promise<Snapshot> {
    let headOid: string;
    try {
      headOid = await git.resolveRef({ fs, dir: this.root, ref: "HEAD" });
    } catch {
      // Missing or empty repo — nothing committed, nothing indexed.
      return { head: "", fullRebuild: false, pages: [] };
    }

    if (!since) {
      return this.fullSnapshot(headOid);
    }

    const range = await this.rangeSnapshot(headOid, since);
    if (range.found) {
      return { head: headOid, fullRebuild: false, pages: range.pages };
    }
    // since unreachable (or unrecognisable) — fall back to a full read.
    return this.fullSnapshot(headOid);
  }

  /**
   * The last commit date of `rel` (YYYY-MM-DD), or "" when root isn't a work
   * tree, rel was never committed, or the history can't be walked.
   * Lenient: "" is the default, never an error.
   */
  async lastCommitDate(rel: string): Promise<string> {
    try {
      const commits = await git.log({
        fs,
        dir: this.root,
        ref: "HEAD",
        filepath: rel,
      });
      if (commits.length === 0) return "";
      return formatDate(commits[0].commit.author.timestamp);
    } catch {
      return "";
    }
  }

  /**
   * Whether `rel` is modified or untracked in the working tree — the
   * `git status --porcelain -- rel` signal. Untracked counts: a brand-new
   * file isn't in git's index at all, and finding it is the point.
   * Lenient: false when root isn't a work tree or the status can't be read.
   *
   * The working-tree-vs-blob content comparison is done here, not via
   * isomorphic-git's `status`: it doesn't apply `core.autocrlf` reliably (its
   * normalisation only reads the *local* config and compares the value to the
   * literal string `"true"`), so a clean CRLF checkout of an LF blob — the
   * norm under `core.autocrlf=true` on Windows — reports `*modified`. We read
   * the blob and the working-tree file ourselves and compare them
   * line-ending-insensitively, so a CRLF/LF-only difference is not a false
   * "modified".
   */
  async porcelainMentions(rel: string): Promise<boolean> {
    try {
      const diskPath = path.join(this.root, rel);

      let work: Buffer | null = null;
      try {
        work = await fs.promises.readFile(diskPath);
      } catch {
        work = null; // not on disk
      }
      const onDisk = work !== null;

      let headBlob: Buffer | null = null;
      try {
        const headOid = await git.resolveRef({
          fs,
          dir: this.root,
          ref: "HEAD",
        });
        const oid = await resolveFilePath(this.root, headOid, rel);
        const { blob } = await git.readBlob({ fs, dir: this.root, oid });
        headBlob = Buffer.from(blob);
      } catch {
        headBlob = null; // no HEAD, or not in HEAD
      }
      const inHead = headBlob !== null;

      if (!inHead && !onDisk) return false; // absent everywhere
      if (!inHead && onDisk) return true; // untracked (a brand-new file)
      if (inHead && !onDisk) return true; // deleted from the working tree

      // Tracked and on disk. Byte-identical is clean. Otherwise a CRLF/LF-only
      // difference is autocrlf's doing, not a real change — but only for text
      // (no NUL byte): binary files aren't subject to autocrlf conversion, so
      // a differing binary file is genuinely modified.
      if (headBlob!.equals(work!)) return false;
      if (!headBlob!.includes(0) && !work!.includes(0)) {
        if (
          normalizeEol(headBlob!.toString("utf8")) ===
          normalizeEol(work!.toString("utf8"))
        ) {
          return false;
        }
      }
      return true;
    } catch {
      return false;
    }
  }

  // -------------------------------------------------------------------------

  /**
   * Committer identity from git config, falling back to `OS-user@hostname`
   * without error when unset (the same fallback the `git` CLI derives).
   * ADR-0003: attribution comes from ingested content, not git identity, so
   * the committer here is bookkeeping, not provenance.
   */
  private async signature(): Promise<{
    name: string;
    email: string;
    timestamp: number;
    timezoneOffset: number;
  }> {
    let name = await this.tryConfig("user.name");
    let email = await this.tryConfig("user.email");
    if (!name) name = fallbackUser();
    if (!email) email = `${name}@${fallbackHost()}`;
    const timestamp = Math.floor(Date.now() / 1000);
    return {
      name,
      email,
      timestamp,
      timezoneOffset: new Date().getTimezoneOffset(),
    };
  }

  private async tryConfig(path: string): Promise<string> {
    try {
      const value = await git.getConfig({ fs, dir: this.root, path });
      return typeof value === "string" ? value : "";
    } catch {
      return "";
    }
  }

  /**
   * Range walk: the `wiki/**.md` paths touched from head's history back to
   * `since`, read from HEAD's tree. `found` is false when the history is
   * walked without ever seeing `since` — the caller's cue to fall back to a
   * full tree read.
   */
  private async rangeSnapshot(
    headOid: string,
    since: string,
  ): Promise<{ pages: PageChange[]; found: boolean }> {
    if (since === headOid) {
      // HEAD already accounted for — no pages to report, no filesystem work.
      return { pages: [], found: true };
    }

    let commits: git.ReadCommitResult[];
    try {
      commits = await git.log({
        fs,
        dir: this.root,
        ref: headOid,
        includeChanges: true,
      });
    } catch {
      return { pages: [], found: false };
    }

    const changed = new Set<string>();
    // path -> latest non-merge commit timestamp (ms); max kept, not log order.
    const latest = new Map<string, number>();
    let found = false;

    for (const commit of commits) {
      if (commit.oid === since) {
        found = true;
        break;
      }
      const paths = changedWikiPaths(commit);
      if (paths.length === 0) continue;
      for (const p of paths) changed.add(p);
      // Merge commits contribute to path enumeration above (so a path touched
      // only by a conflict resolution is still enumerated) but not to date
      // attribution (matching the Go original's CommitDates semantics).
      if (commit.commit.parent.length <= 1) {
        const when = commit.commit.author.timestamp * 1000;
        for (const p of paths) {
          const prev = latest.get(p);
          if (prev === undefined || when > prev) latest.set(p, when);
        }
      }
    }

    if (!found) return { pages: [], found: false };
    if (changed.size === 0) return { pages: [], found: true };

    const pages: PageChange[] = [];
    for (const filePath of changed) {
      const result = await this.tryReadFromHead(headOid, filePath);
      const when = latest.get(filePath);
      // If the bounded walk couldn't attribute a date (a path surfaced by a
      // merge's own diff but whose introducing commit lies on a side branch
      // the walk didn't credit), fall back to a dedicated per-path walk —
      // rare, so the extra cost stays bounded to the pages that need it.
      const date =
        when !== undefined
          ? formatDate(when / 1000)
          : await this.pathDate(headOid, filePath);
      pages.push({
        pageRef: filePath,
        content: result ?? "",
        date,
        deleted: !result,
      });
    }
    return { pages, found: true };
  }

  /** Read every `wiki/**.md` page out of head's tree. */
  private async fullSnapshot(headOid: string): Promise<Snapshot> {
    const dates = await this.commitDates(headOid);
    const pages: PageChange[] = [];
    await this.walkTree(headOid, async (filepath, entry) => {
      if (!isWikiPage(filepath)) return;
      const oid = await entry.oid();
      const content = await readBlobAsString(this.root, oid);
      pages.push({
        pageRef: filepath,
        content,
        date: dates.get(filepath) ?? "",
        deleted: false,
      });
    });
    return { head: headOid, fullRebuild: true, pages };
  }

  /**
   * `{path: YYYY-MM-DD}` — the most recent non-merge commit date per
   * `wiki/**.md` path over every commit reachable from head. Used by the full
   * read; the range-walk counterpart is inlined in `rangeSnapshot`.
   */
  private async commitDates(headOid: string): Promise<Map<string, string>> {
    const latest = new Map<string, number>();
    try {
      const commits = await git.log({
        fs,
        dir: this.root,
        ref: headOid,
        includeChanges: true,
      });
      for (const commit of commits) {
        if (commit.commit.parent.length > 1) continue;
        const paths = changedWikiPaths(commit);
        if (paths.length === 0) continue;
        const when = commit.commit.author.timestamp * 1000;
        for (const p of paths) {
          const prev = latest.get(p);
          if (prev === undefined || when > prev) latest.set(p, when);
        }
      }
    } catch {
      // Lenient: empty dates when the history can't be walked.
    }
    const out = new Map<string, string>();
    for (const [path, when] of latest) out.set(path, formatDate(when / 1000));
    return out;
  }

  /**
   * Latest non-merge commit date touching `path`, walking back from head with
   * no stopping point short of the root commit — the per-path fallback the
   * range walk uses when its bounded walk can't attribute a date.
   */
  private async pathDate(headOid: string, path: string): Promise<string> {
    try {
      const commits = await git.log({
        fs,
        dir: this.root,
        ref: headOid,
        filepath: path,
      });
      for (const commit of commits) {
        if (commit.commit.parent.length > 1) continue;
        return formatDate(commit.commit.author.timestamp);
      }
    } catch {
      // Lenient fallthrough to "".
    }
    return "";
  }

  /** Read `filePath` from head's tree, or null when it's deleted there. */
  private async tryReadFromHead(
    headOid: string,
    filePath: string,
  ): Promise<string | null> {
    try {
      const oid = await resolveFilePath(this.root, headOid, filePath);
      return await readBlobAsString(this.root, oid);
    } catch {
      return null;
    }
  }

  /**
   * Walk every blob in head's tree, invoking `visit` for each one. Directories
   * keep being descended into (isomorphic-git's walk prunes a directory whose
   * `map` returns null, so we must return a truthy value for them).
   */
  private async walkTree(
    headOid: string,
    visit: (filepath: string, entry: git.WalkerEntry) => Promise<void>,
  ): Promise<void> {
    await git.walk({
      fs,
      dir: this.root,
      trees: [git.TREE({ ref: headOid })],
      map: async (filepath: string, [entry]: (git.WalkerEntry | null)[]) => {
        if (!entry) return null;
        const type = await entry.type();
        if (type !== "blob") return filepath; // keep descending into trees
        await visit(filepath, entry);
        return null;
      },
    });
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** The `wiki/**.md` paths a logged commit (with includeChanges) touched. */
function changedWikiPaths(commit: git.ReadCommitResult): string[] {
  const changes = commit.commit.changes;
  if (!changes) return [];
  const out: string[] = [];
  for (const change of changes) {
    const filepath = change[2];
    if (filepath && isWikiPage(filepath)) out.push(filepath);
  }
  return out;
}

/** Mirrors Go's vaultgit.isWikiPage: under wiki/, ends in .md, long enough. */
function isWikiPage(name: string): boolean {
  return (
    name.length > "wiki/".length &&
    name.startsWith("wiki/") &&
    name.length > ".md".length &&
    name.endsWith(".md")
  );
}

/** Whether a vault-relative `file` is under one of the staged `paths`. */
function coveredByPaths(file: string, paths: string[]): boolean {
  return paths.some((p) => {
    if (p === "." || p === "./") return true;
    return file === p || file.startsWith(p.endsWith("/") ? p : p + "/");
  });
}

function formatDate(timestampSeconds: number): string {
  return new Date(timestampSeconds * 1000).toISOString().slice(0, 10);
}

/** Collapse CRLF to LF — the `core.autocrlf` clean-filter comparison. */
function normalizeEol(s: string): string {
  return s.replace(/\r\n/g, "\n");
}

function fallbackUser(): string {
  try {
    const u = os.userInfo();
    if (u.username) return u.username;
  } catch {
    // os.userInfo() throws when the user can't be resolved; fall through.
  }
  return "enchiridion";
}

function fallbackHost(): string {
  const host = os.hostname();
  return host || "localhost";
}

function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

async function readBlobAsString(root: string, oid: string): Promise<string> {
  const { blob } = await git.readBlob({ fs, dir: root, oid });
  return Buffer.from(blob).toString("utf8");
}

/** Walk HEAD's tree to find the oid for `filePath`; throws if absent. */
async function resolveFilePath(
  root: string,
  headOid: string,
  filePath: string,
): Promise<string> {
  let foundOid: string | null = null;
  await git.walk({
    fs,
    dir: root,
    trees: [git.TREE({ ref: headOid })],
    map: async (fp: string, [entry]: (git.WalkerEntry | null)[]) => {
      if (!entry) return null;
      const type = await entry.type();
      if (type !== "tree" && fp === filePath) {
        foundOid = await entry.oid();
      }
      return type === "tree" ? fp : null; // keep descending into trees
    },
  });
  if (!foundOid) throw new Error(`${filePath} not found in HEAD`);
  return foundOid;
}
