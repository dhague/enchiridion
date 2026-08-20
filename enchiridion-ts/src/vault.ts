/**
 * The I/O half of the vault: where the vault is and what's inside it. Ported
 * from enchiridion-go/internal/vault (root.go + vault.go).
 *
 * [resolveRoot] answers "where is the vault" per
 * docs/adr/0004-deployment-modes-and-vault-root-resolution.md; [pageRefs]
 * enumerates every `wiki/**` page; [Vault] owns every read and write inside
 * the vault, plus the cross-page operations ([Vault.movePage],
 * [Vault.rewriteInboundLinks]) that need every other page's text to fix the
 * links pointing at a moved one. Its counterpart [Page] is pure-functional
 * and does no I/O at all.
 */

import fs from "node:fs";
import path from "node:path";
import { Page, planMove } from "./wikipage.js";
import { loadRecords } from "./pagerecord.js";
import type { PageRecord } from "./pagerecord.js";
import { FolderKinds, KindFolders, folderToKind } from "./place.js";

/** The filenames that make a directory a vault root: a `wiki/` directory or a
 * `.wiki-root` sentinel file. */
export const Markers = ["wiki", ".wiki-root"] as const;

/** A lookupEnv matching `process.env`'s semantics: (value, wasPresent). */
export type LookupEnv = (key: string) => [string | undefined, boolean];

function processLookupEnv(key: string): [string | undefined, boolean] {
  const value = process.env[key];
  return [value, value !== undefined];
}

/** Report whether dir itself carries a vault marker — the single check both
 * root resolution and [initwiki.IsVault] build on. */
export function hasMarker(dir: string): boolean {
  for (const marker of Markers) {
    try {
      fs.statSync(path.join(dir, marker));
      return true;
    } catch {
      // not present — keep walking up
    }
  }
  return false;
}

/**
 * Resolve the vault root. See
 * docs/adr/0004-deployment-modes-and-vault-root-resolution.md for why.
 * Order, highest priority first:
 *
 *  1. $WIKI_ROOT if set and non-empty — wins always (query-from-anywhere mode).
 *  2. else the nearest ancestor of start containing a vault marker: a `wiki/`
 *     directory or a `.wiki-root` sentinel file.
 *  3. else start (the dedicated-mode default, cwd).
 *
 * start defaults to cwd when empty, and lookupEnv defaults to the process
 * environment when omitted; both are injectable so the resolution logic is
 * testable without touching the real process environment.
 */
export function resolveRoot(
  start = "",
  lookupEnv: LookupEnv = processLookupEnv,
): { root: string } {
  const [wikiRoot, ok] = lookupEnv("WIKI_ROOT");
  if (ok && wikiRoot !== "" && wikiRoot !== undefined) {
    return { root: resolve(wikiRoot) };
  }

  const startPath = resolve(start === "" ? process.cwd() : start);

  for (let dir = startPath; ;) {
    if (hasMarker(dir)) return { root: dir };
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return { root: startPath };
}

/** Return path as an absolute path with symlinks followed. A path that doesn't
 * exist yet still resolves (init scaffolds one that doesn't), so a failure to
 * walk symlinks falls back to the absolute path. */
function resolve(p: string): string {
  const abs = path.resolve(p);
  try {
    return fs.realpathSync(abs);
  } catch {
    return abs;
  }
}

/** The generated `wiki/_index.md` the old build_index wrote — a derived
 * artifact, not a page, so it is excluded from page enumeration (the same rule
 * the pre-#117 Python layer enforced and the TS port dropped). */
const GeneratedIndexRef = "wiki/_index.md";

/** Return every markdown file under the vault's `wiki/` tree at root, as
 * vault-relative page refs (ADR-0009), sorted. `raw/` is never walked, and the
 * generated `wiki/_index.md` is never a page. */
export function pageRefs(root: string): string[] {
  const wikiDir = path.join(root, "wiki");
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(wikiDir, { withFileTypes: true });
  } catch (err) {
    // A vault with no `wiki/` yet has no pages — not an error.
    if (isENOENT(err)) return [];
    throw err;
  }
  const refs: string[] = [];
  const walk = (dir: string, dirEntries: fs.Dirent[]): void => {
    for (const entry of dirEntries) {
      const abs = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(abs, fs.readdirSync(abs, { withFileTypes: true }));
      } else if (entry.name.endsWith(".md")) {
        const rel = toSlash(path.relative(root, abs));
        if (rel !== GeneratedIndexRef) refs.push(rel);
      }
    }
  };
  walk(wikiDir, entries);
  return refs.sort();
}

function toSlash(p: string): string {
  return p.split(path.sep).join("/");
}

function isENOENT(err: unknown): boolean {
  return (err as NodeJS.ErrnoException).code === "ENOENT";
}

/** Pairs a decoded record with the page text it was decoded from, so a caller
 * needing both doesn't re-read the file. */
export interface PageWithText {
  record: PageRecord;
  text: string;
}

/**
 * Vault owns all vault I/O and cross-page operations over the pages at root.
 *
 * Root is an absolute filesystem path; every page reference this type takes
 * or returns is vault-relative with `/` separators (ADR-0009), so a ref can
 * be handed straight from one method to another, or to an ingest plan.
 */
export class Vault {
  constructor(readonly root: string) {}

  /** Return any singular kind-folders left over from before ADR-0008, sorted
   * — `wiki/concept/` where the vault should now hold `wiki/concepts/`.
   *
   * The migration script that used to fix these is gone, but the check stays,
   * because staying quiet is the one thing that would be genuinely bad:
   * [place.path] resolves canonical kinds from [KindFolders], so an unmigrated
   * vault would split one kind across two spellings of the same folder. A
   * writer asks this and refuses instead. */
  legacyKindFolders(): string[] {
    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(path.join(this.root, "wiki"), {
        withFileTypes: true,
      });
    } catch (err) {
      if (isENOENT(err)) return [];
      throw err;
    }
    const legacy: string[] = [];
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      // A folder is legacy when it is the singular of a canonical kind but
      // not itself canonical: `concept` (→ `concepts`), never `synthesis`,
      // whose folder and kind are the same word.
      if (FolderKinds[entry.name] !== undefined) continue;
      const folder = KindFolders[entry.name];
      if (folder !== undefined && folder !== entry.name)
        legacy.push(entry.name);
    }
    return legacy.sort();
  }

  /** The absolute filesystem path for a vault-relative page ref. */
  path(pageRef: string): string {
    return path.join(this.root, ...pageRef.split("/"));
  }

  /** Read the page at pageRef (vault-relative) into a [Page]. */
  load(pageRef: string): Page {
    return new Page(fs.readFileSync(this.path(pageRef), "utf8"));
  }

  /** Report whether pageRef names an existing *file* in the vault — a page
   * that could be loaded. A directory sitting at that path is not a page, so
   * this is false. */
  exists(pageRef: string): boolean {
    try {
      return !fs.statSync(this.path(pageRef)).isDirectory();
    } catch {
      return false;
    }
  }

  /** Report whether anything at all sits at pageRef, directory included. */
  occupied(pageRef: string): boolean {
    try {
      fs.statSync(this.path(pageRef));
      return true;
    } catch {
      return false;
    }
  }

  /** Write page to pageRef (vault-relative), creating parent directories as
   * needed. */
  write(pageRef: string, page: Page): void {
    const abs = this.path(pageRef);
    fs.mkdirSync(path.dirname(abs), { recursive: true, mode: 0o755 });
    fs.writeFileSync(abs, page.text, { mode: 0o644 });
  }

  /** Return {kind: folder} for every subdirectory of `wiki/` that is not
   * already a canonical kind-folder.
   *
   * The folder must pre-exist; the plugin never auto-creates custom
   * kind-folders on its own. */
  discoveredKinds(): Record<string, string> {
    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(path.join(this.root, "wiki"), {
        withFileTypes: true,
      });
    } catch (err) {
      if (isENOENT(err)) return {};
      throw err;
    }
    const out: Record<string, string> = {};
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      if (FolderKinds[entry.name] !== undefined) continue;
      out[folderToKind(entry.name)] = entry.name;
    }
    return out;
  }

  /** Return every `wiki/**` page as a {pageRef: text} map. Never walks
   * `raw/`. */
  loadWikiPages(): Record<string, string> {
    const refs = pageRefs(this.root);
    const pages: Record<string, string> = {};
    for (const ref of refs)
      pages[ref] = fs.readFileSync(this.path(ref), "utf8");
    return pages;
  }

  /** Return every `wiki/**` page as a {pageRef: record + text} map. */
  pagesWithText(): Record<string, PageWithText> {
    const pages = this.loadWikiPages();
    const records = loadRecords(pages);
    const out: Record<string, PageWithText> = {};
    for (const ref of Object.keys(records)) {
      out[ref] = { record: records[ref], text: pages[ref] };
    }
    return out;
  }

  /** Return every `wiki/**` page as a {pageRef: record} map. `raw/` is never
   * walked. */
  pages(): Record<string, PageRecord> {
    const withText = this.pagesWithText();
    const out: Record<string, PageRecord> = {};
    for (const ref of Object.keys(withText)) out[ref] = withText[ref].record;
    return out;
  }

  /** Load, [Page.set], and write back the page at pageRef. */
  set(pageRef: string, key: string, value: unknown): Page {
    const page = this.load(pageRef);
    const updated = page.set(key, value);
    this.write(pageRef, updated);
    return updated;
  }

  /** Load, [Page.merge], and write back the page at pageRef. */
  merge(pageRef: string, key: string, values: unknown[]): Page {
    const page = this.load(pageRef);
    const updated = page.merge(key, values);
    this.write(pageRef, updated);
    return updated;
  }

  /** Write every page in planned whose text differs from before, returning the
   * changed vault-relative paths, sorted. */
  private writeChanged(
    planned: Record<string, string>,
    before: Record<string, string>,
  ): string[] {
    const changed: string[] = [];
    for (const [pageRef, text] of Object.entries(planned)) {
      // A page absent from before is always written, even when the planned
      // text is empty — "unchanged" means the file already held this text,
      // not that the text is falsy.
      const prev = before[pageRef];
      if (prev !== undefined && text === prev) continue;
      this.write(pageRef, new Page(text));
      changed.push(pageRef);
    }
    return changed.sort();
  }

  /** Rewrite links across the vault's wiki pages and move the page on disk.
   *
   * Reads every `wiki/**` page (never `raw/` — its files aren't rewritten by
   * a page move), plans the move, writes back only the pages whose text
   * changed, then removes the original. Returns the changed vault-relative
   * paths, sorted; empty for oldRef == newRef. */
  movePage(oldRef: string, newRef: string): string[] {
    const files = this.loadWikiPages();
    if (!(oldRef in files)) {
      throw new Error(`${oldRef} not found under ${this.root}`);
    }

    // planned keys the moved page under newRef, so writing every changed page
    // also lays down the moved file (with its outbound links fixed) — all
    // that's left is to drop the original.
    const changed = this.writeChanged(planMove(files, oldRef, newRef), files);
    if (this.path(oldRef) !== this.path(newRef)) {
      fs.unlinkSync(this.path(oldRef));
    }
    return changed;
  }

  /** Rewrite `wiki/**` pages' links pointing at oldRel to newRel.
   *
   * For a target that is not itself a wiki page — e.g. a `raw/` artifact
   * renamed externally — oldRel/newRel are never read, parsed, or written;
   * only *other* pages' inbound links are fixed. Returns the changed
   * vault-relative paths, sorted. */
  rewriteInboundLinks(oldRel: string, newRel: string): string[] {
    const pages = this.loadWikiPages();
    return this.writeChanged(planMove(pages, oldRel, newRel), pages);
  }
}
