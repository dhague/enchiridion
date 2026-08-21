/**
 * The one definition of what counts as a page (#310).
 *
 * "What is a page" used to be computed three different ways — the disk walk
 * (`vault.pageRefs`), the committed-history walk (`vaultgit`), and the
 * search index's on-disk status count — and the answers diverged on edge
 * vaults: a committed `wiki/_index.md` counted for the git walk but not the
 * disk walk, and a nested `wiki/<folder>/nested/deep.md` counted for the disk
 * walk, was rejected by the schema reader (`pagerecord`), and was missed by
 * the status count. The generated-index bug (#299) was that friction
 * surfacing.
 *
 * This module is the single predicate and the single disk enumerator that
 * both walks and the status count delegate to, so the three can never
 * disagree again. It is deliberately a leaf module — it imports nothing from
 * the rest of the codebase — which is exactly why `searchindex` can use it
 * without the import cycle that proxying back through `vault` would create
 * (the Vault has no search-index facade; ADR-0015).
 *
 * **The page rule:** a page is a markdown file at exactly
 * `wiki/<kind-folder>/<file>.md` — directly under a kind-folder, the same
 * shape `pagerecord.newPageRecord` requires — and never the generated
 * `wiki/_index.md`. Anything else under `wiki/` (a file at the wiki root, a
 * nested page, the generated index) is a structural error, not a page, so it
 * is neither enumerated, indexed, nor counted. The kind-folder axis itself is
 * ADR-0008's (folders pluralize, values stay singular); this predicate is
 * purely structural about it — the folder is one path segment — and says
 * nothing about which folder names are canonical, so custom kind-folders
 * (`wiki/decisions/`) are pages too, exactly as `pagerecord` accepts them.
 */

import fs from "node:fs";
import path from "node:path";

/** The generated `wiki/_index.md` the old build_index wrote — a derived
 * artifact, not a page, so it is excluded from page enumeration (the same
 * rule the pre-#117 Python layer enforced). Handled in exactly this one place;
 * see the module doc. */
const GeneratedIndexRef = "wiki/_index.md";

/**
 * The page predicate: whether a vault-relative path (ADR-0009) names a page.
 *
 * A page is `wiki/<kind-folder>/<file>.md` — exactly three path segments, a
 * `.md` suffix, and never the generated index. Every consumer of the page
 * concept — the disk walk (`enumeratePageRefs`), the git walk
 * (`vaultgit.committedPages`), and the index's status count — filters through
 * this one predicate, so no two of them can disagree about an edge vault.
 *
 * Excluded by the shape: `wiki/a.md` (no kind-folder), `wiki/_index.md` (not
 * directly under a kind-folder — and never a page on principle),
 * `wiki/concepts/nested/deep.md` (not *directly* under a kind-folder), and
 * anything outside `wiki/`. Nested pages are a structural error under the
 * kind-folder model (ADR-0008), the same stance the schema reader takes, so
 * they are not pages.
 */
export function isPageRef(ref: string): boolean {
  if (ref === GeneratedIndexRef) return false;
  if (!ref.startsWith("wiki/")) return false;
  if (!ref.endsWith(".md")) return false;
  return ref.split("/").length === 3;
}

/**
 * Enumerate every page under the vault's `wiki/` tree at root, as
 * vault-relative page refs (ADR-0009), sorted. `raw/` is never walked, and
 * every candidate is filtered through [isPageRef], so the generated
 * `wiki/_index.md`, files at the wiki root, and nested pages are all
 * excluded. A vault with no `wiki/` yields no pages, not an error.
 *
 * This is the disk-walk half of the shared enumeration rule; the git walk
 * enumerates its own tree and filters it through the same [isPageRef]
 * predicate, so both walks count exactly the same pages.
 */
export function enumeratePageRefs(root: string): string[] {
  const wikiDir = path.join(root, "wiki");
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(wikiDir, { withFileTypes: true });
  } catch (err) {
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
        if (isPageRef(rel)) refs.push(rel);
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
