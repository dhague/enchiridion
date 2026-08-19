/**
 * The pure half of the vault library — frontmatter splitting, the
 * markdown-link machinery, and the mutating page model. Ported from
 * enchiridion-go/internal/wikipage (wikipage.go + page.go). No I/O lives here.
 *
 * Encoding lives at a single decode boundary: [splitDest] splits on the
 * literal `#` first and decodes each half after, so an encoded `#` in a raw
 * filename can never be mistaken for an anchor separator.
 *
 * **Byte-preservation contract.**
 *
 *   - Link rewriting never round-trips the document through a stringifier.
 *     Destinations are spliced into the raw text back-to-front by exact
 *     source offset, so every untouched byte survives — including
 *     frontmatter links, which the same whole-document scan finds.
 *   - A no-op frontmatter [Page.set] is *not* guaranteed to round-trip
 *     byte-identical; see docs/adr/0012-go-frontmatter-round-trip-relaxed.md.
 *     Key *order* is still preserved (frontmatter is edited as a
 *     [yaml.YAMLMap] mapping, so existing keys keep their position and new
 *     ones append), because a reordering edit would make every ingest diff
 *     unreadable — only incidental formatting may change.
 */

import {
  parseDocument,
  stringify,
  Scalar,
  YAMLSeq,
  YAMLMap,
  isMap,
} from "yaml";
import { isDeepStrictEqual } from "node:util";
import path from "node:path";
import MarkdownIt from "markdown-it";

// ---------------------------------------------------------------------------
// Link machinery
// ---------------------------------------------------------------------------

/** The minimal charset that makes a raw/ filename linkable. */
const ENCODE_CHARS = " #%()<>";

/** Match a `---` fence on the VERY first line, closed by the next `---` line. */
const FRONTMATTER_RE = /^---[ \t]*\n(.*?\n)?---[ \t]*(?:\n|$)/s;

/**
 * Build a regex fragment for an unbracketed link destination. Per CommonMark,
 * a destination without `<>` ends at the first *unbalanced* `)` — `(draft)`
 * inside one doesn't terminate it. JS regexes have no recursion, so nesting
 * is bounded at depth levels: plenty for a real filename or URL.
 */
function nestedParenDest(depth: number): string {
  let frag = "[^()\\s]*";
  for (let i = 0; i < depth; i++) {
    frag = `(?:[^()\\s]|\\(${frag}\\))*`;
  }
  return frag;
}

/**
 * Match a markdown inline link or image: `[label](dest ...)` /
 * `![label](dest ...)`. `label` tolerates one level of nested brackets.
 * `dest` is either `<...>` or a whitespace-free run that may contain balanced
 * parens; an optional title after the dest is matched but excluded.
 *
 * The `d` (hasIndices) flag exposes each group's source offsets, mirroring
 * Go's FindAllStringSubmatchIndex.
 */
const LINK_RE = new RegExp(
  `(!?)\\[((?:[^\\[\\]]|\\[[^\\[\\]]*\\])*)\\]` +
    `\\([ \\t]*` +
    `(<[^<>\\n]*>|${nestedParenDest(4)})` +
    `(?:[ \\t]+(?:"[^"]*"|'[^']*'|\\([^)]*\\)))?` +
    `[ \\t]*\\)`,
  "gd",
);

/** Percent-encode [ENCODE_CHARS] in path; all else stays literal. */
export function percentEncode(p: string): string {
  let out = "";
  for (const ch of p) {
    if (ch < "\x80" && ENCODE_CHARS.includes(ch)) {
      out += "%" + ch.charCodeAt(0).toString(16).toUpperCase().padStart(2, "0");
      continue;
    }
    out += ch;
  }
  return out;
}

/** Reverse [percentEncode]. An invalid or truncated escape is left verbatim. */
export function percentDecode(p: string): string {
  let out = "";
  for (let i = 0; i < p.length; i++) {
    const ch = p[i];
    if (ch === "%" && i + 2 < p.length) {
      const hex = p.slice(i + 1, i + 3);
      if (/^[0-9a-fA-F]{2}$/.test(hex)) {
        out += String.fromCharCode(parseInt(hex, 16));
        i += 2;
        continue;
      }
    }
    out += ch;
  }
  return out;
}

/**
 * Split an encoded link destination into its decoded path and decoded anchor.
 *
 * **Order matters:** split on the literal `#` first, decode each half after.
 * Decoding up front would turn an encoded `#` in a filename (`%23`) into a
 * false anchor separator.
 */
export function splitDest(dest: string): { path: string; anchor: string } {
  const hash = dest.indexOf("#");
  const encodedPath = hash === -1 ? dest : dest.slice(0, hash);
  const encodedAnchor = hash === -1 ? "" : dest.slice(hash + 1);
  return {
    path: percentDecode(encodedPath),
    anchor: hash === -1 ? "" : percentDecode(encodedAnchor),
  };
}

/**
 * Split a leading YAML frontmatter block off text.
 *
 * `hasFrontmatter` is false when there is none, in which case body is text
 * unchanged and bodyOffset is 0. `text.slice(bodyOffset) == body` always
 * holds.
 */
export function splitFrontmatter(src: string): {
  frontmatter: string;
  body: string;
  bodyOffset: number;
  hasFrontmatter: boolean;
} {
  const m = FRONTMATTER_RE.exec(src);
  if (!m) {
    return { frontmatter: "", body: src, bodyOffset: 0, hasFrontmatter: false };
  }
  const frontmatter = m[1] !== undefined ? m[1] : "";
  const bodyOffset = m[0].length;
  return {
    frontmatter,
    body: src.slice(bodyOffset),
    bodyOffset,
    hasFrontmatter: true,
  };
}

/** One link/image occurrence, positioned in the source text. */
export interface LinkMatch {
  start: number;
  end: number;
  /** the encoded destination (angle brackets and any title excluded) */
  dest: string;
  /** splitDest(dest).path — decoded, anchor-free */
  decodedPath: string;
  /** splitDest(dest).anchor — decoded, "" if no anchor */
  decodedAnchor: string;
  isImage: boolean;
  /** 0-based */
  line: number;
}

const md = new MarkdownIt();

/**
 * Return the set of 0-based line indices that fall inside code blocks.
 *
 * markdown-it's `map` includes a fence's delimiter lines, not just its content
 * lines — immaterial here, since a fence delimiter line is a fence marker plus
 * an info string, which cannot contain a markdown link.
 */
function codeLineRanges(src: string): Set<number> {
  const lines = new Set<number>();
  const tokens = md.parse(src, {});
  for (const token of tokens) {
    if (token.type !== "fence" && token.type !== "code_block") continue;
    const map = token.map;
    if (!map) continue;
    for (let line = map[0]; line < map[1]; line++) lines.add(line);
  }
  return lines;
}

function lineOf(src: string, offset: number): number {
  if (offset > src.length) offset = src.length;
  let count = 0;
  for (let i = 0; i < offset; i++) {
    if (src[i] === "\n") count++;
  }
  return count;
}

/**
 * Return a [LinkMatch] for every link/image in src, in order.
 *
 * Occurrences inside fenced/indented code blocks are skipped. Offsets are
 * absolute into src. Scans the *whole* document, frontmatter included, so
 * typed edges, `supersedes` and `raw_source` are found by the same rule as
 * body links.
 */
export function iterLinks(src: string): LinkMatch[] {
  const codeLines = codeLineRanges(src);
  const out: LinkMatch[] = [];
  for (const m of src.matchAll(LINK_RE)) {
    const idx = m.indices![3];
    let start = idx[0];
    let end = idx[1];
    let dest = m[3]!;
    // Unwrap an angle-bracketed destination: `<path>` -> `path`.
    if (dest.startsWith("<") && dest.endsWith(">")) {
      start += 1;
      end -= 1;
      dest = dest.slice(1, -1);
    }
    const line = lineOf(src, start);
    if (codeLines.has(line)) continue;
    const { path: decodedPath, anchor: decodedAnchor } = splitDest(dest);
    out.push({
      start,
      end,
      dest,
      decodedPath,
      decodedAnchor,
      isImage: m[1] === "!",
      line,
    });
  }
  return out;
}

/**
 * Resolve an already-decoded link destination to a normalized path.
 *
 * pageDir is the vault-relative directory the link lives in (e.g.
 * `wiki/concepts`), so the result is vault-relative by construction —
 * ADR-0009.
 */
export function resolveLinkDest(dest: string, pageDir: string): string {
  const base = pageDir === "" ? "." : pageDir;
  return path.posix.normalize(path.posix.join(base, dest));
}

/**
 * Extract a whole markdown-link scalar's destination, decoded.
 *
 * link is a full `[label](dest)` (or image) scalar, as stored in frontmatter
 * or found in body text — not a bare destination. ok is false when link isn't
 * a markdown link at all.
 */
export function linkDest(link: string): { dest: string; ok: boolean } {
  const matches = iterLinks(link);
  if (matches.length === 0) return { dest: "", ok: false };
  return { dest: matches[0].decodedPath, ok: true };
}

// ---------------------------------------------------------------------------
// Page model
// ---------------------------------------------------------------------------

const YAML_INDENT = 2;

/**
 * One page's frontmatter plus body. Pure-functional — no I/O, no mutation:
 * [Page.set], [Page.merge] and [Page.retarget] each return a *new* Page.
 */
export class Page {
  constructor(readonly text: string) {}

  /**
   * Return p's frontmatter as a YAML mapping node, minting an empty one when
   * the page has no frontmatter block (or an empty one).
   *
   * A node rather than a map because a mapping node preserves key order.
   */
  private frontmatterNode(): YAMLMap {
    const { frontmatter, hasFrontmatter } = splitFrontmatter(this.text);
    if (!hasFrontmatter || frontmatter.trim() === "") return new YAMLMap();
    const doc = parseDocument(frontmatter);
    if (doc.errors.length > 0) {
      throw new Error(`invalid frontmatter YAML: ${doc.errors[0].message}`);
    }
    if (doc.contents === null || !isMap(doc.contents)) {
      if (doc.contents === null) return new YAMLMap();
      throw new Error("frontmatter is not a YAML mapping");
    }
    return doc.contents;
  }

  /** Return the full frontmatter mapping, decoded to plain values, or null
   * when this page has no frontmatter block. */
  frontmatter(): Record<string, unknown> | null {
    const { hasFrontmatter } = splitFrontmatter(this.text);
    if (!hasFrontmatter) return null;
    return this.frontmatterNode().toJSON() as Record<string, unknown>;
  }

  /** Return the value of key in this page's frontmatter. ok is false when the
   * page has no frontmatter or the key is absent. */
  get(key: string): { value: unknown; ok: boolean } {
    const data = this.frontmatter();
    if (data === null) return { value: undefined, ok: false };
    if (!(key in data)) return { value: undefined, ok: false };
    return { value: data[key], ok: true };
  }

  /** Return a string-valued frontmatter key, or "" when it is absent, null,
   * or not a string. */
  getString(key: string): string {
    const { value } = this.get(key);
    return typeof value === "string" ? value : "";
  }

  /** Return a list-valued frontmatter key's string entries. A key that is
   * absent, null, or not a list yields []; non-string entries within a list
   * are skipped. */
  getStringList(key: string): string[] {
    const { value } = this.get(key);
    if (!Array.isArray(value)) return [];
    return value.filter((v): v is string => typeof v === "string");
  }

  /**
   * Return a new page with frontmatter key set to value.
   *
   * Mints a frontmatter block when the page has none. Only the block is
   * re-serialised; the body is spliced back verbatim.
   */
  set(key: string, value: unknown): Page {
    const node = this.frontmatterNode();
    const valueNode = newValueNode(value);
    setKey(node, key, valueNode);
    const rendered = renderFrontmatter(node);
    // With no frontmatter yet, body is the whole text — so the same
    // expression prepends a fresh block to the untouched document.
    const { body } = splitFrontmatter(this.text);
    return new Page("---\n" + rendered + "---\n" + body);
  }

  /**
   * Return a new page with values unioned into key's existing list.
   *
   * Order-preserving: existing entries hold their position, new ones append,
   * duplicates drop. Equivalent to [Page.set] when key is absent.
   */
  merge(key: string, values: unknown[]): Page {
    const existing = this.get(key).value;
    const merged: unknown[] = [];
    if (Array.isArray(existing)) merged.push(...existing);
    for (const value of values) {
      if (!containsValue(merged, value)) merged.push(value);
    }
    return this.set(key, merged);
  }

  /** [Page.merge] over a string list — the shape every caller with typed-edge
   * links or tags already has. */
  mergeStrings(key: string, values: string[]): Page {
    return this.merge(key, values);
  }

  /** Return the document body — everything after the frontmatter block. */
  body(): string {
    return splitFrontmatter(this.text).body;
  }

  /** Return every link/image in this page, body and frontmatter alike. */
  links(): LinkMatch[] {
    return iterLinks(this.text);
  }

  /**
   * Return a new page with links fixed for the vault-wide move oldRel ->
   * newRel.
   *
   * fileRel is where *this* page sits before the move; pass fileRel == oldRel
   * when this page is the one being moved, so its own outbound links are
   * rebased onto newRel's folder too.
   */
  retarget(fileRel: string, oldRel: string, newRel: string): Page {
    return new Page(rewriteText(this.text, fileRel, oldRel, newRel));
  }
}

/**
 * Compute the post-move vault from pages (a {pageRef: text} map).
 *
 * Pure. The moved page appears under newRel; every other page keeps its key.
 * Inbound and outbound links are both fixed.
 *
 * oldRel need not be a key of pages: a caller retargeting links at a non-page
 * file (a `raw/` artifact, say) passes only the markdown pages whose *inbound*
 * links should follow the rename.
 */
export function planMove(
  pages: Record<string, string>,
  oldRel: string,
  newRel: string,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [rel, text] of Object.entries(pages)) {
    const key = rel === oldRel ? newRel : rel;
    out[key] = new Page(text).retarget(rel, oldRel, newRel).text;
  }
  return out;
}

/**
 * Compose a markdown link to targetRel from a page in pageDir.
 *
 * Both are vault-relative (`wiki/concepts/foo.md` / `wiki/synthesis`);
 * pageDir may be "" for a page at the vault root. Relativises the target and
 * percent-encodes the destination — never the label. YAML quoting is not done
 * here: [Page.set]/[Page.merge] already double-quote a fresh `[…]` scalar.
 */
export function composeLink(
  title: string,
  targetRel: string,
  pageDir: string,
): string {
  const dest = relPath(path.posix.normalize(targetRel), pageDir);
  return `[${title}](${percentEncode(dest)})`;
}

/**
 * Re-encode every relative link/image destination in src.
 *
 * An author (human or agent) may write a destination unencoded — a raw
 * filename with a space or paren, taken verbatim. This normalises each one,
 * via the same offset-based splice [Page.retarget] uses, so untouched bytes
 * survive. Idempotent. Absolute paths, scheme-qualified URLs, and bare
 * anchors are left alone.
 */
export function normalizeBodyLinks(src: string): string {
  const edits: Edit[] = [];
  for (const link of iterLinks(src)) {
    if (!isRelativeDest(link.decodedPath)) continue;
    const dest = encodeDest(link.decodedPath, link.decodedAnchor);
    if (dest !== link.dest)
      edits.push({ start: link.start, end: link.end, dest });
  }
  return applyEdits(src, edits);
}

/** One destination splice: replace src[start:end] with dest. */
interface Edit {
  start: number;
  end: number;
  dest: string;
}

/** Splice edits into src back-to-front by source offset, so every untouched
 * byte survives and earlier offsets stay valid as later ones are replaced. */
function applyEdits(src: string, edits: Edit[]): string {
  edits.sort((a, b) => b.start - a.start);
  for (const e of edits) {
    src = src.slice(0, e.start) + e.dest + src.slice(e.end);
  }
  return src;
}

/** Re-encode a decoded path and anchor back into a link destination. */
function encodeDest(p: string, anchor: string): string {
  let dest = percentEncode(p);
  if (anchor !== "") dest += "#" + percentEncode(anchor);
  return dest;
}

/** Report whether path (the pre-anchor part of a destination) is a
 * vault-relative reference. Excludes the empty destination, absolute paths,
 * bare anchors, and any scheme-qualified URL. */
function isRelativeDest(p: string): boolean {
  return (
    p !== "" && !p.startsWith("/") && !p.startsWith("#") && !p.includes("://")
  );
}

/** Return text with its links fixed for the move oldRel -> newRel. */
function rewriteText(
  text: string,
  fileRel: string,
  oldRel: string,
  newRel: string,
): string {
  const isMovedFile = fileRel === oldRel;
  const oldDir = path.posix.dirname(fileRel);
  let newDir = path.posix.dirname(fileRel);
  if (isMovedFile) newDir = path.posix.dirname(newRel);

  const edits: Edit[] = [];
  for (const link of iterLinks(text)) {
    if (!isRelativeDest(link.decodedPath)) continue;
    // Where this link pointed, resolved from the file's original location.
    const target = resolveLinkDest(link.decodedPath, oldDir);
    // For pages other than the moved one, only links at the moved page change.
    if (!isMovedFile && target !== oldRel) continue;
    // The moved page itself relocates the target of a self-link.
    const movedTarget = target === oldRel ? newRel : target;
    const dest = encodeDest(relPath(movedTarget, newDir), link.decodedAnchor);
    if (dest !== link.dest)
      edits.push({ start: link.start, end: link.end, dest });
  }
  return applyEdits(text, edits);
}

/**
 * relPath is posixpath.relpath over two vault-relative slash paths: the route
 * from base to target, spelled with `../` segments. Both arguments are
 * vault-relative by construction.
 */
function relPath(target: string, base: string): string {
  const targetParts = pathParts(target);
  const baseParts = pathParts(base);

  let common = 0;
  while (
    common < targetParts.length &&
    common < baseParts.length &&
    targetParts[common] === baseParts[common]
  ) {
    common++;
  }

  const parts: string[] = [];
  for (let i = common; i < baseParts.length; i++) parts.push("..");
  parts.push(...targetParts.slice(common));
  if (parts.length === 0) return ".";
  return parts.join("/");
}

function pathParts(p: string): string[] {
  const cleaned = path.posix.normalize(p);
  if (cleaned === "." || cleaned === "") return [];
  return cleaned.split("/");
}

// ---------------------------------------------------------------------------
// Frontmatter YAML (eemeli/yaml AST model — preserves key order)
// ---------------------------------------------------------------------------

/** Return whether values already holds value, via structural comparison. */
function containsValue(values: unknown[], value: unknown): boolean {
  return values.some((existing) => isDeepStrictEqual(existing, value));
}

/** Replace key's value in the mapping, or append the pair when key is absent —
 * so existing keys keep their position and new keys land at the end. */
function setKey(mapping: YAMLMap, key: string, value: Scalar | YAMLSeq): void {
  for (const pair of mapping.items) {
    if ((pair.key as Scalar).value === key) {
      pair.value = value;
      return;
    }
  }
  mapping.add({ key, value });
}

/**
 * Encode a plain value to a YAML node, double-quoting any fresh markdown-link
 * scalar.
 *
 * A first-time value has no prior style to round-trip from. Only strings
 * starting `[` are touched; image embeds (`![…]`) never appear in frontmatter,
 * so that form isn't handled.
 */
function newValueNode(value: unknown): Scalar | YAMLSeq {
  const node = toYamlNode(value);
  quoteLinks(node);
  return node;
}

function toYamlNode(value: unknown): Scalar | YAMLSeq {
  if (Array.isArray(value)) {
    const seq = new YAMLSeq();
    for (const item of value) seq.add(toYamlNode(item));
    return seq;
  }
  return new Scalar(value);
}

function quoteLinks(node: Scalar | YAMLSeq): void {
  if (node instanceof YAMLSeq) {
    for (const item of node.items) {
      if (item instanceof Scalar || item instanceof YAMLSeq) quoteLinks(item);
    }
  } else if (node instanceof Scalar) {
    if (typeof node.value === "string" && node.value.startsWith("[")) {
      node.type = "QUOTE_DOUBLE";
    }
  }
}

function renderFrontmatter(node: YAMLMap): string {
  return stringify(node, { indent: YAML_INDENT });
}
