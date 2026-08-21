/**
 * discover — single-call discovery for ingestion: overlap candidates plus the
 * tag vocabulary, driven off a draft IngestPlan.
 *
 * It fronts [Index] with hint classification, so wiki-ingest's
 * duplicate-detection step — the one where a miss creates a duplicate page —
 * is deterministic mechanics rather than an agent re-deriving "too similar"
 * from prose each run. Each candidate carries back everything the agent would
 * otherwise open the page to read (summary, tags, volatility, supersession),
 * so the cite / edge-type / tag-reuse steps collapse into one call instead of
 * N page reads.
 *
 * **The query is OR of the candidate's own words** (raw=true), never the
 * default AND-across-terms. An AND query built from a whole
 * title+summary+body demands every one of those words be present in the
 * candidate page — so the planned page's necessarily-novel summary and body
 * text silently zero out real duplicates that title alone would have found.
 * Precision comes instead from BM25's IDF weighting, where common words
 * contribute almost nothing to the score.
 */

import type { Hit, Query, TagCount } from "./searchindex.js";
import type { PagePlan } from "./ingest.js";

/** The relationship a discovery hit has to the planned page. */
export type Hint = "duplicate" | "refines" | "related" | "distinct";

export const HintDuplicate: Hint = "duplicate";
export const HintRefines: Hint = "refines";
export const HintRelated: Hint = "related";
export const HintDistinct: Hint = "distinct";

// Thresholds calibrated against the dogfooding vault (#63). They are
// parameters on [check] rather than hard-coded, so the eval harness can tune
// them without editing this module.

/** The score at or above which a hit sharing a title token is a duplicate. */
export const DuplicateThreshold = 15.0;
/** The score at or above which a hit is related. */
export const RelatedThreshold = 5.0;
/** Deliberately generous: a narrow cap hides exactly the near-duplicates this
 * call exists to surface. */
export const DefaultLimit = 200;

/** Matches the tokens that build a query and are compared for shared title
 * tokens — `[a-z0-9]+` over lowercased text, exactly as discover.py. */
const wordRE = /[a-z0-9]+/g;

/** The search surface [check] and [discover] need. [Index] implements it. */
export interface Searcher {
  search(q: Query): Promise<Hit[]>;
}

/** One overlapping page, classified by relationship to the planned page. */
export interface Candidate {
  page_ref: string;
  title: string;
  score: number;
  hint: Hint;
  summary: string;
  tags: string[];
  volatility: string;
  superseded_by: string | null;
}

/** Tunes [check] and [discover]. */
export interface Options {
  limit: number;
  duplicateThreshold: number;
  relatedThreshold: number;
}

/** Fill the calibrated defaults for zero-valued options. */
function withDefaults(o: Options): Required<Options> {
  return {
    limit: o.limit > 0 ? o.limit : DefaultLimit,
    duplicateThreshold:
      o.duplicateThreshold === 0 ? DuplicateThreshold : o.duplicateThreshold,
    relatedThreshold:
      o.relatedThreshold === 0 ? RelatedThreshold : o.relatedThreshold,
  };
}

/** The set of `[a-z0-9]+` tokens in a lowercased title. */
function titleTokens(title: string): Set<string> {
  const tokens = new Set<string>();
  for (const word of title.toLowerCase().match(wordRE) ?? []) {
    tokens.add(word);
  }
  return tokens;
}

/** The unique words across texts, phrase-quoted and OR-joined — an FTS5 Raw
 * expression. OR, not AND; see the module comment. */
export function orQuery(...texts: string[]): string {
  const seen = new Set<string>();
  const words: string[] = [];
  for (const text of texts) {
    for (const word of text.toLowerCase().match(wordRE) ?? []) {
      if (!seen.has(word)) {
        seen.add(word);
        words.push(word);
      }
    }
  }
  return words.map((w) => `"${w}"`).join(" OR ");
}

/** Map a score plus whether the hit shares a title token to its hint. */
export function classify(
  score: number,
  sharesTitleToken: boolean,
  duplicateThreshold: number,
  relatedThreshold: number,
): Hint {
  if (score >= duplicateThreshold) {
    if (sharesTitleToken) return HintDuplicate;
    return HintRefines;
  }
  if (score >= relatedThreshold) return HintRelated;
  return HintDistinct;
}

/** Search for pages overlapping a planned page, classifying each hit's
 * relationship to it. title/summary/body must be the planned page's own
 * drafted text.
 *
 * The query is built from exactly what the candidate page says — never a
 * paraphrase — which is why retrieval's vocabulary-mismatch problem doesn't
 * bite here.
 *
 * The searcher is passed in, never opened here: [Index] is
 * one-per-vault-at-a-time (ADR-0010), and that is enforced by the command
 * owning the only handle rather than by this module guessing whether one is
 * already live. */
export async function check(
  searcher: Searcher,
  title: string,
  summary: string,
  body: string,
  opts: Options,
): Promise<Candidate[]> {
  const o = withDefaults(opts);
  const query = orQuery(title, summary, body);
  if (query === "") return [];

  const hits = await searcher.search({
    text: query,
    raw: true,
    limit: o.limit,
  });

  const planned = titleTokens(title);
  const candidates: Candidate[] = [];
  for (const hit of hits) {
    let shares = false;
    for (const token of titleTokens(hit.title)) {
      if (planned.has(token)) {
        shares = true;
        break;
      }
    }
    candidates.push({
      page_ref: hit.pageRef,
      title: hit.title,
      score: hit.score,
      hint: classify(
        hit.score,
        shares,
        o.duplicateThreshold,
        o.relatedThreshold,
      ),
      summary: hit.summary,
      tags: hit.tags,
      volatility: hit.volatility,
      superseded_by: hit.supersededBy,
    });
  }
  return candidates;
}

/** Pairs one planned page's title with its discovered candidates. */
export interface PageResult {
  title: string;
  candidates: Candidate[];
}

/** A plan page's frontmatter summary as a string, "" when absent — the same
 * `frontmatter.get("summary", "")` discover.py reads. */
function pageSummary(page: PagePlan): string {
  const got = page.frontmatter.get("summary");
  if (!got.ok) return "";
  if (typeof got.value !== "string") return "";
  return got.value;
}

/** A plan page's body, "" when nil — the same `page.body or ""` discover.py
 * reads. */
function pageBody(page: PagePlan): string {
  return page.body ?? "";
}

/** Run [check] for every page a draft plan proposes — one call however many
 * chunks the plan carries, against the one searcher the caller owns, per
 * ADR-0010. */
export async function discover(
  searcher: Searcher,
  pages: PagePlan[],
  opts: Options,
): Promise<PageResult[]> {
  const results: PageResult[] = [];
  for (const page of pages) {
    const candidates = await check(
      searcher,
      page.title,
      pageSummary(page),
      pageBody(page),
      opts,
    );
    results.push({ title: page.title, candidates });
  }
  return results;
}

/** The vocabulary tags whose name contains any of substrings, case-insensitive
 * OR match. Order follows vocabulary (most-used first). */
export function tagsContaining(
  vocabulary: TagCount[],
  substrings: string[],
): string[] {
  const needles = substrings.map((s) => s.toLowerCase());
  const out: string[] = [];
  for (const tc of vocabulary) {
    const name = tc.tag.toLowerCase();
    if (needles.some((needle) => name.includes(needle))) {
      out.push(tc.tag);
    }
  }
  return out;
}

/** The exact-match page count per requested tag, 0 if absent (safe to mint).
 * Order follows the requested tags. */
export function tagCounts(vocabulary: TagCount[], tags: string[]): TagCount[] {
  const counts = new Map<string, number>();
  for (const tc of vocabulary) counts.set(tc.tag, tc.count);
  return tags.map((tag) => ({ tag, count: counts.get(tag) ?? 0 }));
}
