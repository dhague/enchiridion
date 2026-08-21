/**
 * ingest — the IngestPlan schema and its single-call executor. Plan in,
 * commit SHA out.
 *
 * A [Plan] is the decided outcome of an ingestion: which pages to
 * create/update, with what frontmatter and typed edges. Semantic chunking and
 * overlap classification are judgment and stay with the ingesting agent;
 * everything downstream of that decision is mechanics and lives here.
 *
 * **A plan names link targets by vault-relative page reference only** —
 * `edges` and `supersedes` hold paths like `wiki/concepts/foo.md`, never
 * composed `[Title](../dest.md)` strings. Composing the link (title lookup,
 * `../` relativisation, percent-encoding, YAML quoting) is this module's job.
 * `raw_source` uses a boolean sentinel for the same reason:
 * `frontmatter: {"raw_source": true}` marks the page as the stub for
 * [Plan.raw], and the link is composed from that. Body links are re-encoded
 * on write by [normalizeBodyLinks].
 *
 * Pipeline: [resolve] -> [Resolved.validate] -> [Resolved.execute] -> derive
 * a [commit.Manifest] -> commit.
 *
 * [resolve] is the single place placement ([place.path]), frontmatter
 * projection and edge/`raw_source` link composition happen: it turns a plan
 * into the exact (pageRef, page) pairs the vault will end up holding.
 * Validation then reads only resolved facts, and execution writes only
 * resolved pages — so the plan that was checked and the plan that gets
 * written cannot diverge. Resolve is pure apart from vault reads.
 *
 * Validation runs entirely before any write, shape (required fields, valid
 * op) then semantic (an update's pageRef exists, a create's target doesn't
 * yet, every edge target resolves to a page already on disk *or* created by
 * this same plan, and [chainofevidence.check] holds). That last check is a
 * courtesy to the agent — [commit.commit] re-runs it as the hard gate, so a
 * hand-built manifest can't route around validation into history.
 *
 * Ingestion isn't the only caller: wiki-retrieval's confirmed synthesis-page
 * save is the same shape (one `create` of kind `synthesis`, `source` edges,
 * no raw artifact) and passes `action: "synthesize"` so the history
 * distinguishes the two without reading the diff.
 *
 * [Plan.raw] is never renamed or moved — a file with external identity keeps
 * its name forever. Ingestion reads it and stages it; `raw_source` links
 * point at it where it sits, percent-encoded by the link machinery rather
 * than sanitized on disk.
 *
 * **No rollback on failure, deliberately.** A page written before a later
 * step fails stays on disk, uncommitted. Every write here is idempotent, so
 * re-running the plan after fixing the cause is always safe.
 */

import path from "node:path";
import {
  Page,
  composeLink,
  normalizeBodyLinks,
  splitFrontmatter,
} from "./wikipage.js";
import { path as placePath, Kinds } from "./place.js";
import { Vault } from "./vault.js";
import { check as checkChainOfEvidence } from "./chainofevidence.js";
import { commit, type Git, type Supersession } from "./commit.js";
import {
  CANONICAL_DATE_FORMAT,
  parseSourceDate,
  truncateSourceDate,
} from "./sourcedate.js";

/** Caps a full path (vault root plus vault-relative path), for Windows'
 * 255-char limit (#70). */
export const MaxPathLength = 255;

/** The two verbs a plan page may carry. */
export const OpCreate = "create";
export const OpUpdate = "update";

/** Thrown when a plan fails shape or semantic validation. The message lists
 * every problem found, not just the first. */
export class ErrPlan extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ErrPlan";
  }
}

/** A JSON object that remembers its key order.
 *
 * Frontmatter keys and edge keys are applied to a page in the order the plan
 * lists them, and JS object key iteration is deterministic but not the plan's
 * order — so decoding into a plain object would make the frontmatter key order
 * of an ingested page vary run to run. ADR-0012 relaxes the *byte-identical*
 * round-trip contract; it does not license nondeterministic output.
 */
export class OrderedMap<V> {
  readonly keys: string[] = [];
  readonly values = new Map<string, V>();

  /** The value for key, and whether it was present. */
  get(key: string): { value: V | undefined; ok: boolean } {
    if (!this.values.has(key)) return { value: undefined, ok: false };
    return { value: this.values.get(key), ok: true };
  }

  /** The number of entries. */
  length(): number {
    return this.keys.length;
  }

  /** Iterate the entries in plan order. */
  *all(): Generator<[string, V]> {
    for (const key of this.keys) {
      yield [key, this.values.get(key) as V];
    }
  }

  /** Record one entry, keeping its first position and taking the last value. */
  private set(key: string, value: V): void {
    if (!this.values.has(key)) this.keys.push(key);
    this.values.set(key, value);
  }

  /** Decode a JSON object, recording key order as it goes. A duplicate key
   * keeps its first position and takes the last value. */
  static decode<V>(data: unknown): OrderedMap<V> {
    const m = new OrderedMap<V>();
    if (data === null || typeof data !== "object" || Array.isArray(data)) {
      // an explicit null is an absent object, not an error
      return m;
    }
    for (const key of Object.keys(data as Record<string, unknown>)) {
      m.set(key, (data as Record<string, unknown>)[key] as V);
    }
    return m;
  }
}

/** One page this plan creates or updates. */
export interface PagePlan {
  op: string;
  title: string;
  kind: string;
  page_ref: string;
  /** Body is null when the plan leaves the existing body alone; an update
   * omitting it keeps what's on disk, whereas `"body": ""` blanks it. */
  body: string | null;
  /** The projected frontmatter, in plan order. */
  frontmatter: OrderedMap<unknown>;
  /** Each typed-edge key maps to its targets, named by vault-relative page
   * reference only. */
  edges: OrderedMap<string[]>;
}

/** The deterministic description of one ingestion's decided outcome. */
export interface Plan {
  title: string;
  /** The structured commit's verb ([Manifest.action]): `ingest`, or
   * `synthesize` for a wiki-retrieval synthesis save. */
  action: string;
  source_date: string;
  raw: string;
  pages: PagePlan[];
}

/** Read one plan from JSON.
 *
 * Action defaults to `ingest`, so a plan that omits it still commits under
 * a verb. */
export function decodePlan(jsonText: string): Plan {
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(jsonText) as Record<string, unknown>;
  } catch (err) {
    throw new Error(`invalid plan JSON: ${(err as Error).message}`, {
      cause: err,
    });
  }
  const action = typeof data["action"] === "string" ? data["action"] : "";
  const rawPages = Array.isArray(data["pages"]) ? data["pages"] : [];
  const pages: PagePlan[] = rawPages.map((p) => {
    const page = p as Record<string, unknown>;
    return {
      op: typeof page["op"] === "string" ? page["op"] : "",
      title: typeof page["title"] === "string" ? page["title"] : "",
      kind: typeof page["kind"] === "string" ? page["kind"] : "",
      page_ref: typeof page["page_ref"] === "string" ? page["page_ref"] : "",
      body:
        page["body"] === null || page["body"] === undefined
          ? null
          : typeof page["body"] === "string"
            ? page["body"]
            : String(page["body"]),
      frontmatter: OrderedMap.decode<unknown>(page["frontmatter"]),
      edges: OrderedMap.decode<string[]>(page["edges"]),
    };
  });
  return {
    title: typeof data["title"] === "string" ? data["title"] : "",
    action: action === "" ? "ingest" : action,
    source_date:
      typeof data["source_date"] === "string" ? data["source_date"] : "",
    raw: typeof data["raw"] === "string" ? data["raw"] : "",
    pages,
  };
}

/** One plan page, resolved to the exact file the vault will hold.
 *
 * PageRef is "" and Page is null together, when placement couldn't be computed
 * (an invalid Kind, a missing PageRef) — its own shape error, reported by
 * [Resolved.validate]. */
export interface ResolvedPage {
  plan: PagePlan;
  pageRef: string;
  /** The full post-write content: projected frontmatter plus body. A create
   * starts blank, an update from its on-disk copy. */
  page: Page | null;
  /** Whether anything was already at PageRef when the plan was resolved — a
   * create may not claim it. A *directory* counts. */
  occupied: boolean;
  /** Whether an existing page was read as this page's base. An update needs
   * one; a create never has one. */
  loaded: boolean;
}

/** A plan with every derived fact computed exactly once.
 *
 * Constructible directly (no vault needed) for tests; [resolve] is the
 * production path. */
export class Resolved {
  constructor(
    readonly plan: Plan,
    readonly pages: ResolvedPage[],
    /** "" when resolved without a vault — shape checks only, no reads. */
    readonly root: string,
    /** {kind: folder} for vault-discovered kind-folders beyond the four
     * canonical ones; empty when resolved without a vault. */
    readonly extraKindFolders: Record<string, string>,
  ) {}

  /** A handle on the vault this plan resolved against, or null when it
   * resolved without one. */
  private vault(): Vault | null {
    if (this.root === "") return null;
    return new Vault(this.root);
  }

  /** One page's plan verb, `create` or `update`. */
  opOf(page: ResolvedPage): string {
    return page.plan.op;
  }

  /** Check this plan, shape then semantic, before any write. Throws [ErrPlan]
   * naming every problem found. */
  validate(): void {
    const problems = [...this.shapeErrors(), ...this.semanticErrors()];
    if (problems.length > 0) {
      throw new ErrPlan(`invalid plan: ${problems.join("; ")}`);
    }
  }

  /** Shape errors cover required fields and valid ops — everything checkable
   * without a vault. */
  private shapeErrors(): string[] {
    const problems: string[] = [];
    if (this.plan.title === "") {
      problems.push("plan.title is required");
    }
    if (this.plan.pages.length === 0) {
      problems.push("plan.pages must contain at least one page");
    }

    for (let i = 0; i < this.pages.length; i++) {
      const page = this.pages[i].plan;
      const prefix = `pages[${i}]`;

      if (page.op !== OpCreate && page.op !== OpUpdate) {
        problems.push(
          `${prefix}.op must be 'create' or 'update', got ${JSON.stringify(page.op)}`,
        );
        continue;
      }
      if (page.title === "") {
        problems.push(`${prefix}.title is required`);
      }

      if (page.op === OpCreate) {
        if (page.page_ref !== "") {
          problems.push(`${prefix}.page_ref must not be set for op=create`);
        }
        if (page.kind === "") {
          problems.push(`${prefix}.kind is required for op=create`);
        } else if (
          !Kinds.includes(page.kind) &&
          !(page.kind in this.extraKindFolders)
        ) {
          problems.push(
            `${prefix}.kind ${JSON.stringify(page.kind)} is not a valid kind`,
          );
        }
        if (page.body === null) {
          problems.push(`${prefix}.body is required for op=create`);
        }
      } else {
        if (page.kind !== "") {
          problems.push(`${prefix}.kind must not be set for op=update`);
        }
        if (page.page_ref === "") {
          problems.push(`${prefix}.page_ref is required for op=update`);
        }
      }

      // An explicit null reads as absent, so a plan with `raw_source: null`
      // is treated the same as one that omits it.
      const rawSource = page.frontmatter.get("raw_source");
      if (rawSource.ok && rawSource.value !== null) {
        if (rawSource.value !== true) {
          problems.push(
            `${prefix}.frontmatter.raw_source must be true (derived from plan.raw), got ${String(rawSource.value)}`,
          );
        } else if (this.plan.raw === "") {
          problems.push(
            `${prefix}.frontmatter.raw_source is true but plan.raw is not set`,
          );
        }
      }

      // `source_date` is valid time and has one canonical spelling
      // (YYYY-MM-DD). A clock on it truncates on write, but a value that
      // isn't a valid date at all can't be — the plan is refused (#192). Null
      // reads as absent, like raw_source. The rule itself lives in
      // [sourcedate.parseSourceDate], the one owner (#309).
      const sourceDate = page.frontmatter.get("source_date");
      if (sourceDate.ok && sourceDate.value !== null) {
        if (parseSourceDate(sourceDate.value) === null) {
          problems.push(
            `${prefix}.frontmatter.source_date must be a valid date (${CANONICAL_DATE_FORMAT}), got ${String(sourceDate.value)}`,
          );
        }
      }
    }
    return problems;
  }

  /** Semantic errors cover the checks that need the vault: target existence,
   * path length, evidence chain. */
  private semanticErrors(): string[] {
    if (this.root === "") return [];
    const v = this.vault() as Vault;
    const problems: string[] = [];

    // A page this same plan is about to create counts as resolvable too, so
    // sibling new pages can link to each other before either exists on disk.
    const prospective = new Set<string>();
    for (const rp of this.pages) {
      if (rp.plan.op === OpCreate && rp.pageRef !== "") {
        prospective.add(rp.pageRef);
      }
    }

    for (let i = 0; i < this.pages.length; i++) {
      const rp = this.pages[i];
      const page = rp.plan;
      const prefix = `pages[${i}]`;
      if (page.op !== OpCreate && page.op !== OpUpdate) {
        continue;
      }

      if (page.op === OpCreate && rp.pageRef !== "") {
        if (rp.occupied) {
          problems.push(
            `${prefix}: create target ${rp.pageRef} already exists`,
          );
        }
        const full = v.path(rp.pageRef);
        if (full.length > MaxPathLength) {
          problems.push(
            `${prefix}: path ${rp.pageRef} exceeds ${MaxPathLength} chars (${full.length} chars with vault root)`,
          );
        }
      } else if (page.op === OpUpdate && rp.pageRef !== "" && !rp.loaded) {
        problems.push(`${prefix}.page_ref ${rp.pageRef} does not exist`);
      }

      for (const target of pageLinkTargets(page, this.plan)) {
        if (prospective.has(target.ref)) continue;
        // Exists is file-only, so a target naming a directory fails here
        // rather than composing a link to something unopenable.
        if (!v.exists(target.ref)) {
          problems.push(
            `${prefix}: ${target.key} target ${JSON.stringify(target.ref)} does not resolve to a real page`,
          );
        }
      }
    }

    if (this.plan.raw !== "") {
      // A courtesy check for the agent; commit re-runs it as the hard gate.
      const staged: Record<string, Page> = {};
      for (const rp of this.pages) {
        if (rp.pageRef !== "" && rp.page !== null) {
          staged[rp.pageRef] = rp.page;
        }
      }
      problems.push(...checkChainOfEvidence(staged, this.plan.raw));
    }
    return problems;
  }

  /** Write every resolved page and commit, returning the commit SHA.
   *
   * Assumes [Resolved.validate] has already passed. No rollback on failure.
   * git is injectable for tests; pass a [VaultGit] over the vault root in
   * production. */
  async execute(git: Git): Promise<string> {
    if (this.root === "") {
      throw new ErrPlan(
        "invalid plan: cannot execute a plan resolved without a vault root",
      );
    }
    const v = this.vault() as Vault;

    const created: string[] = [];
    const updated: string[] = [];
    const superseded: Supersession[] = [];

    for (const resolved of this.pages) {
      if (resolved.pageRef === "" || resolved.page === null) {
        throw new ErrPlan(
          `invalid plan: page ${JSON.stringify(resolved.plan.title)} was not resolved`,
        );
      }
      v.write(resolved.pageRef, resolved.page);
      if (resolved.plan.op !== OpCreate) {
        updated.push(resolved.pageRef);
        continue;
      }
      created.push(resolved.pageRef);
      const targets = resolved.plan.edges.get("supersedes");
      if (targets.ok) {
        for (const target of targets.value ?? []) {
          superseded.push({
            old: path.posix.normalize(target),
            new: resolved.pageRef,
          });
        }
      }
    }

    return commit(
      this.root,
      {
        title: this.plan.title,
        action: this.plan.action,
        created,
        updated,
        superseded,
        source_date: manifestSourceDate(this.plan.source_date),
        raw_source: this.plan.raw,
      },
      git,
    );
  }

  /** A human-readable summary of what [Resolved.execute] would write. */
  describe(): string {
    const lines = [`${this.plan.action}: ${this.plan.title}`];
    for (const resolved of this.pages) {
      lines.push(`  ${resolved.plan.op.padEnd(6)} ${resolved.pageRef}`);
    }
    return lines.join("\n");
  }
}

/** Turn a plan into the exact pages the vault will hold.
 *
 * Pure apart from vault reads: placement, frontmatter projection and link
 * composition each happen here and nowhere else, so validation and execution
 * read the same facts by construction. Pass root == "" to resolve without a
 * vault, for shape checks alone. */
export function resolve(plan: Plan, root: string): Resolved {
  let v: Vault | null = null;
  const extraKindFolders: Record<string, string> = {};
  if (root !== "") {
    v = new Vault(root);
    // Refuse an unmigrated vault outright rather than filing pages into the
    // plural folders while the old ones sit in the singular.
    const legacy = v.legacyKindFolders();
    if (legacy.length > 0) {
      // The #114 migration script this used to name is retired, so the remedy
      // is spelled out here instead: one `git mv` per folder.
      const moves = legacy.map(
        (kind) => `git mv wiki/${kind}/* wiki/${kind}s/`,
      );
      throw new Error(
        `${root} holds pre-ADR-0008 kind-folders (wiki/${legacy.join(", wiki/")}); ` +
          `move their pages into the plural folders before ingesting (${moves.join("; ")}), ` +
          `then remove the empty singular folders. Merge by hand where both spellings hold the same filename`,
      );
    }
    Object.assign(extraKindFolders, v.discoveredKinds());
  }

  const refs = plan.pages.map((page) => pageRef(page, extraKindFolders));

  // First page wins, so a link's title matches the earliest plan page claiming
  // that pageRef.
  const titles = new Map<string, string>();
  for (let i = 0; i < refs.length; i++) {
    const ref = refs[i];
    if (ref === "") continue;
    if (!titles.has(ref)) titles.set(ref, plan.pages[i].title);
  }

  const resolvedPages: ResolvedPage[] = [];
  for (let i = 0; i < plan.pages.length; i++) {
    const planPage = plan.pages[i];
    const pageRef_ = refs[i];
    if (pageRef_ === "") {
      resolvedPages.push({
        plan: planPage,
        pageRef: "",
        page: null,
        occupied: false,
        loaded: false,
      });
      continue;
    }

    let base = new Page("");
    let loaded = false;
    if (planPage.op === OpUpdate && v !== null && v.exists(pageRef_)) {
      base = v.load(pageRef_);
      loaded = true;
    }

    let page = applyFrontmatter(
      base,
      planPage,
      path.posix.dirname(pageRef_),
      plan,
      titles,
      v,
    );
    page = applyBody(page, planPage.body);

    resolvedPages.push({
      plan: planPage,
      pageRef: pageRef_,
      page,
      occupied: v !== null && v.occupied(pageRef_),
      loaded,
    });
  }
  return new Resolved(plan, resolvedPages, root, extraKindFolders);
}

/** The vault-relative path a plan page will occupy, or "" when it can't be
 * computed yet (an invalid kind or a missing pageRef, each already recorded
 * as its own shape error). */
function pageRef(
  page: PagePlan,
  extraKindFolders: Record<string, string>,
): string {
  if (page.op !== OpCreate) return page.page_ref;
  if (page.title === "") return "";
  try {
    return placePath(page.kind, page.title, extraKindFolders);
  } catch {
    return "";
  }
}

/** The title a link to targetRef should carry.
 *
 * This plan's own page for that pageRef wins (titles), so an update that
 * corrects a title propagates to every link the same plan writes. Then the
 * on-disk title; then the basename, reachable only if validation let an
 * unresolvable target through. */
function resolveTitle(
  targetRef: string,
  titles: Map<string, string>,
  v: Vault | null,
): string {
  targetRef = path.posix.normalize(targetRef);
  const title = titles.get(targetRef);
  if (title !== undefined) return title;
  if (v !== null && v.exists(targetRef)) {
    const page = v.load(targetRef);
    const diskTitle = page.getString("title");
    if (diskTitle !== "") return diskTitle;
  }
  return path.posix.basename(targetRef);
}

/** The **only** frontmatter projection in this module — see [resolve]. */
function applyFrontmatter(
  page: Page,
  planPage: PagePlan,
  pageDir: string,
  plan: Plan,
  titles: Map<string, string>,
  v: Vault | null,
): Page {
  page = page.set("title", planPage.title);

  const merging = planPage.op === OpUpdate;
  for (const [key, value] of planPage.frontmatter.all()) {
    let v_ = value;
    if (key === "raw_source" && value === true) {
      if (plan.raw === "") {
        // Nothing to point at; validate reports it as a shape error.
        continue;
      }
      v_ = composeLink(path.posix.basename(plan.raw), plan.raw, pageDir);
    }
    if (key === "source_date") {
      v_ = truncateSourceDate(value);
    }
    if (Array.isArray(v_) && merging) {
      page = page.merge(key, v_);
    } else {
      page = page.set(key, v_);
    }
  }

  for (const [key, refs] of planPage.edges.all()) {
    const links = refs.map((ref) =>
      composeLink(resolveTitle(ref, titles, v), ref, pageDir),
    );
    if (merging) {
      page = page.mergeStrings(key, links);
    } else {
      page = page.set(key, links);
    }
  }
  return page;
}

/** Canonicalises the plan's top-level `source_date` — the commit-trailer
 * attribution — to YYYY-MM-DD (#192). A non-date passes through. The rule
 * itself lives in [sourcedate.parseSourceDate], the one owner (#309). */
function manifestSourceDate(s: string): string {
  const date = parseSourceDate(s);
  return date !== null ? date : s;
}

/** Replaces the body while leaving the frontmatter block byte-exact,
 * re-encoding the new body's link destinations on the way in. */
function applyBody(page: Page, newBody: string | null): Page {
  if (newBody === null) return page;
  const { bodyOffset } = splitFrontmatter(page.text);
  return new Page(page.text.slice(0, bodyOffset) + normalizeBodyLinks(newBody));
}

/** One (edge key, normalized target pageRef) pair awaiting existence
 * validation. */
interface LinkTarget {
  key: string;
  ref: string;
}

/** The targets this page's plan asks to link to. Plans name targets by
 * vault-relative page reference only, so this is a plain normalize — no
 * markdown-link parsing. */
function pageLinkTargets(page: PagePlan, plan: Plan): LinkTarget[] {
  const targets: LinkTarget[] = [];
  const rawSource = page.frontmatter.get("raw_source");
  if (rawSource.ok && rawSource.value === true && plan.raw !== "") {
    targets.push({ key: "raw_source", ref: path.posix.normalize(plan.raw) });
  }
  for (const [key, refs] of page.edges.all()) {
    for (const ref of refs) {
      targets.push({ key, ref: path.posix.normalize(ref) });
    }
  }
  return targets;
}
