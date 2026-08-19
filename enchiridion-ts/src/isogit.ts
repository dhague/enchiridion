/**
 * isomorphic-git implementation of the Git interface for searchindex.
 *
 * Mirrors Go's internal/vaultgit — lenient: a missing repository or one with
 * no commits yields an empty Snapshot rather than an error.
 *
 * ADR-0015: content is read from HEAD's blobs, never from intermediate commits.
 */

import * as git from "isomorphic-git";
import fs from "node:fs";
import path from "node:path";
import type { Git, Snapshot, PageChange } from "./searchindex.js";

export class IsoGit implements Git {
  constructor(private readonly root: string) {}

  async committedPages(since: string): Promise<Snapshot> {
    let headOid: string;
    try {
      headOid = await git.resolveRef({ fs, dir: this.root, ref: "HEAD" });
    } catch {
      // Missing or empty repo — nothing committed, nothing indexed.
      return { head: "", fullRebuild: false, pages: [] };
    }

    if (!since) {
      // Full tree read of HEAD.
      const pages = await this.readFullTree(headOid);
      return { head: headOid, fullRebuild: true, pages };
    }

    // Range walk: collect wiki paths touched from HEAD back to `since`.
    const { pages, found } = await this.readRangeTree(headOid, since);
    if (!found) {
      // `since` unreachable — amend/rebase/reset/re-clone; fall back to full.
      const full = await this.readFullTree(headOid);
      return { head: headOid, fullRebuild: true, pages: full };
    }
    if (pages.length === 0) {
      // HEAD already accounted for — no pages to report.
      return { head: headOid, fullRebuild: false, pages: [] };
    }
    return { head: headOid, fullRebuild: false, pages };
  }

  // ---------------------------------------------------------------------------

  private async readFullTree(headOid: string): Promise<PageChange[]> {
    const results: PageChange[] = [];
    await git.walk({
      fs,
      dir: this.root,
      trees: [git.TREE({ ref: headOid })],
      map: async (filepath: string, [entry]: (git.WalkerEntry | null)[]) => {
        if (!entry) return null;
        if (!isWikiMdPath(filepath)) return null;
        const type = await entry.type();
        if (type !== "blob") return null;
        const oid = await entry.oid();
        const content = await readBlobAsString(this.root, oid);
        const date = await this.commitDateForPath(headOid, filepath);
        results.push({ pageRef: filepath, content, date, deleted: false });
        return null;
      },
    });
    return results;
  }

  private async readRangeTree(
    headOid: string,
    since: string,
  ): Promise<{ pages: PageChange[]; found: boolean }> {
    // Walk log from HEAD, stop when we find `since`.
    const touchedPaths = new Set<string>();
    let found = false;

    const commits = await git.log({ fs, dir: this.root, ref: headOid });
    for (const commit of commits) {
      if (commit.oid === since) {
        found = true;
        break;
      }
      // Collect wiki paths changed by this commit.
      const parentOid =
        commit.commit.parent.length > 0 ? commit.commit.parent[0] : null;
      const changed = await this.changedWikiPaths(commit.oid, parentOid);
      for (const p of changed) touchedPaths.add(p);
    }

    if (!found) return { pages: [], found: false };
    if (touchedPaths.size === 0) return { pages: [], found: true };

    // Read each touched path from HEAD's tree (or mark deleted).
    const pages: PageChange[] = [];
    for (const filePath of touchedPaths) {
      const result = await this.tryReadFromHead(headOid, filePath);
      const date = result
        ? await this.commitDateForPath(headOid, filePath)
        : "";
      pages.push({
        pageRef: filePath,
        content: result ?? "",
        date,
        deleted: !result,
      });
    }
    return { pages, found: true };
  }

  /** Paths under wiki/ changed between commit and its parent (or root tree). */
  private async changedWikiPaths(
    commitOid: string,
    parentOid: string | null,
  ): Promise<string[]> {
    const changed: string[] = [];
    const trees = parentOid
      ? [git.TREE({ ref: commitOid }), git.TREE({ ref: parentOid })]
      : [git.TREE({ ref: commitOid })];

    await git.walk({
      fs,
      dir: this.root,
      trees,
      map: async (
        filepath: string,
        entries: (git.WalkerEntry | null)[],
      ) => {
        if (!isWikiMdPath(filepath)) return null;
        const [cur, parent] = entries;
        if (cur === null && parent === null) return null;
        // File added, deleted, or changed.
        const curOid = cur ? await cur.oid() : null;
        const parentOid_ = parent ? await parent.oid() : null;
        if (curOid !== parentOid_) changed.push(filepath);
        return null;
      },
    });
    return changed;
  }

  private async tryReadFromHead(
    headOid: string,
    filePath: string,
  ): Promise<string | null> {
    try {
      return await readBlobAsString(
        this.root,
        await resolveFilePath(this.root, headOid, filePath),
      );
    } catch {
      return null;
    }
  }

  /** Most recent commit date touching filePath (YYYY-MM-DD), or "". */
  private async commitDateForPath(
    headOid: string,
    filePath: string,
  ): Promise<string> {
    try {
      const commits = await git.log({
        fs,
        dir: this.root,
        ref: headOid,
        filepath: filePath,
        depth: 1,
        force: true,
        follow: false,
      });
      if (commits.length === 0) return "";
      const ts = commits[0].commit.committer.timestamp;
      return new Date(ts * 1000).toISOString().slice(0, 10);
    } catch {
      return "";
    }
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isWikiMdPath(filepath: string): boolean {
  return (
    filepath.startsWith("wiki/") &&
    filepath.endsWith(".md") &&
    filepath.split("/").length === 3
  );
}

async function readBlobAsString(root: string, oid: string): Promise<string> {
  const { blob } = await git.readBlob({ fs, dir: root, oid });
  return Buffer.from(blob).toString("utf8");
}

async function resolveFilePath(
  root: string,
  headOid: string,
  filePath: string,
): Promise<string> {
  // Walk HEAD tree to find the oid for filePath.
  let foundOid: string | null = null;
  await git.walk({
    fs,
    dir: root,
    trees: [git.TREE({ ref: headOid })],
    map: async (fp: string, [entry]: (git.WalkerEntry | null)[]) => {
      if (fp === filePath && entry) {
        foundOid = await entry.oid();
      }
      return null;
    },
  });
  if (!foundOid) throw new Error(`${filePath} not found in HEAD`);
  return foundOid;
}
