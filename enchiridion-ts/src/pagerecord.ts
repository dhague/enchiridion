/**
 * The one package that reads the frontmatter schema.
 *
 * Frontmatter text in, one typed record out. Every caller that needs a page's
 * frontmatter goes through here rather than re-parsing keys, so the schema
 * changes in exactly one place.
 *
 * Every path this module touches is vault-relative — a page reference
 * (`wiki/concepts/a.md`), ADR-0009. Kind is derived from the page's folder via
 * [folderToKind] (ADR-0008 singularization rule): canonical folders resolve
 * from [FolderKinds]; custom folders are singularized and used verbatim.
 * Edges recovers each of [EdgeKeys]' targets, resolved from the page's own
 * directory to true vault-relative by construction; SupersededBy is derived by
 * inverting every other page's `supersedes` edge, never read from frontmatter.
 */

import path from "node:path";
import { parse as parseYaml } from "yaml";
import { linkDest, resolveLinkDest, splitFrontmatter } from "./wikipage.js";
import { FolderKinds, folderToKind } from "./place.js";
import { parseSourceDate } from "./sourcedate.js";

/** Lists the frontmatter keys that hold markdown links to other pages. Order
 * mirrors the frontmatter schema block in the conventions spec. `raw_source`
 * holds a single link; every other key holds a list. */
export const EdgeKeys: string[] = [
  "raw_source",
  "supersedes",
  "refines",
  "contradicts",
  "example-of",
  "source",
  "related",
];

/** The [EdgeKeys] whose YAML value is one scalar link rather than a list of
 * them. */
const singleLinkKeys: Record<string, boolean> = { raw_source: true };

/** One frontmatter edge key with its resolved, vault-relative targets. */
export interface Edge {
  key: string;
  targets: string[];
}

/** One page's frontmatter, decoded to plain values. */
export interface PageRecord {
  pageRef: string;
  kind: string;
  title: string;
  summary: string;
  tags: string[];
  sourceDate: string;
  volatility: string;
  edges: Edge[];
  supersededBy: string[];
}

/** Returns the targets of this record's `supersedes` edge, or null. */
export function supersedes(r: PageRecord): string[] | null {
  for (const e of r.edges) {
    if (e.key === "supersedes") return e.targets;
  }
  return null;
}

function linkTarget(markdownLink: string, pageDir: string): string {
  const { dest, ok } = linkDest(markdownLink);
  if (!ok) {
    throw new Error(`not a markdown link: "${markdownLink}"`);
  }
  return resolveLinkDest(dest, pageDir);
}

/**
 * Decodes one page's frontmatter. SupersededBy is always empty here — it needs
 * every other page, so only [loadRecords] fills it in.
 */
export function newPageRecord(pageRef: string, text: string): PageRecord {
  // The kind-folder is the directory directly under `wiki/` that holds this
  // page (`wiki/concepts/a.md` → folder `concepts`). A page not at that exact
  // depth (e.g. `wiki/foo.md` or `wiki/concepts/nested/deep.md`) is a
  // structural error.
  let pageDir = path.posix.dirname(pageRef);
  if (pageDir === ".") pageDir = "";
  const folder = path.posix.basename(pageDir);
  if (path.posix.dirname(pageDir) !== "wiki") {
    throw new Error(`"${pageRef}": not directly under a wiki kind-folder`);
  }
  const kind = FolderKinds[folder] ?? folderToKind(folder);

  const data = frontmatterMap(text);

  const edges: Edge[] = [];
  for (const key of EdgeKeys) {
    const raw = data[key];
    if (raw === undefined || raw === null) continue;
    let links: string[];
    if (singleLinkKeys[key]) {
      if (typeof raw !== "string" || raw === "") continue;
      links = [raw];
    } else {
      if (!Array.isArray(raw) || raw.length === 0) continue;
      links = raw.map((item) => {
        if (typeof item !== "string") {
          throw new Error(
            `${pageRef}: ${key} entry is not a markdown link: ${String(item)}`,
          );
        }
        return item;
      });
    }
    const targets = links.map((link) => {
      try {
        return linkTarget(link, pageDir);
      } catch (err) {
        throw new Error(`${pageRef}: ${key}: ${(err as Error).message}`, {
          cause: err,
        });
      }
    });
    edges.push({ key, targets });
  }

  return {
    pageRef,
    kind,
    title: scalar(data["title"]),
    summary: scalar(data["summary"]),
    tags: stringList(data["tags"]),
    sourceDate: sourceDate(data["source_date"]),
    volatility: scalar(data["volatility"]),
    edges,
    supersededBy: [],
  };
}

/**
 * Parses a page's YAML frontmatter into a plain map. A page with no
 * frontmatter, or with an empty block, decodes to an empty map rather than an
 * error — a body-only file is indexable, just featureless.
 */
function frontmatterMap(text: string): Record<string, unknown> {
  const { frontmatter, hasFrontmatter } = splitFrontmatter(text);
  if (!hasFrontmatter || frontmatter === "") return {};
  const data = parseYaml(frontmatter) as unknown;
  if (data === null || typeof data !== "object") return {};
  return data as Record<string, unknown>;
}

/**
 * Renders the frontmatter `source_date` scalar in its canonical YYYY-MM-DD
 * spelling, truncating any clock (the minimal wikitime analogue — #192). A
 * value that isn't a valid date at all is stored verbatim: the read path
 * tolerates legacy and hand-written values, while the write paths (ingest,
 * `page set`) reject them. The parse/validate/canonicalise rule itself lives
 * in [sourcedate.parseSourceDate], the one owner (#309).
 */
function sourceDate(v: unknown): string {
  const date = parseSourceDate(v);
  if (date !== null) return date;
  return scalar(v);
}

/**
 * Renders a frontmatter value as a string — a missing key and an explicit
 * null both give "".
 *
 * `source_date` never reaches here — [sourceDate] canonicalises it first. A
 * date landing in any other field is rendered date-only when it has no clock,
 * RFC3339 otherwise.
 */
function scalar(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  if (v instanceof Date) {
    const dateOnly = `${v.getUTCFullYear()}-${String(v.getUTCMonth() + 1).padStart(2, "0")}-${String(v.getUTCDate()).padStart(2, "0")}`;
    const midnight = Date.UTC(
      v.getUTCFullYear(),
      v.getUTCMonth(),
      v.getUTCDate(),
    );
    return v.getTime() === midnight ? dateOnly : v.toISOString();
  }
  return String(v);
}

function stringList(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.map((item) => scalar(item));
}

/**
 * Decodes every page in pages ({pageRef: text}, keys vault-relative), filling
 * in SupersededBy by inverting the `supersedes` edges.
 *
 * Pages in any `wiki/<folder>/` are decoded and included; custom kind-folders
 * are fully supported via [folderToKind]. Pages at the wrong depth (not
 * directly under a kind-folder) are an error.
 */
export function loadRecords(
  pages: Record<string, string>,
): Record<string, PageRecord> {
  const records: Record<string, PageRecord> = {};
  for (const [pageRef, text] of Object.entries(pages)) {
    records[pageRef] = newPageRecord(pageRef, text);
  }

  const supersededBy: Record<string, string[]> = {};
  for (const [pageRef, rec] of Object.entries(records)) {
    for (const target of supersedes(rec) ?? []) {
      (supersededBy[target] ??= []).push(pageRef);
    }
  }
  for (const [pageRef, targets] of Object.entries(supersededBy)) {
    const rec = records[pageRef];
    if (rec) {
      rec.supersededBy = targets;
      records[pageRef] = rec;
    }
  }
  return records;
}
