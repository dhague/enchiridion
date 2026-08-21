/**
 * Resolve a candidate set's supersession chains to their current heads.
 *
 * [pagerecord.loadRecords] already inverts every page's `supersedes` edge into
 * `supersededBy`; this module is the retrieval-facing entrypoint into that
 * derivation. It takes the retrieval frontier's candidate set of page refs and
 * returns, for each, its *active* page: the same ref if it's current, or the
 * page at the end of its supersession chain otherwise. A chain head is
 * returned even when it falls outside the given candidate set — `supersedes`
 * is a recorded fact, so the head is surfaced rather than left for the reader
 * to notice a bare page ref is stale.
 */

import type { PageRecord } from "./pagerecord.js";

/** One seed's supersession chain, walked to its current head.
 *
 * Chain lists the intermediate/final pages between Seed and Active
 * (excluding Seed, ending with Active); empty when Seed is already current. */
export interface Resolution {
  seed: string;
  active: string;
  chain: string[];
}

/** Walk each seed's supersededBy pointers to its current head.
 *
 * A page missing from records (outside the vault) resolves to itself with an
 * empty chain. When a page's supersededBy lists more than one successor, the
 * first is followed — the same first-write-wins convention the search index
 * uses, since the schema doesn't model forked supersession. */
export function resolve(
  seeds: string[],
  records: Record<string, PageRecord>,
): Resolution[] {
  const resolutions: Resolution[] = [];
  for (const seed of seeds) {
    const chain: string[] = [];
    let current = seed;
    const seen = new Set<string>([current]);
    for (;;) {
      const rec = records[current];
      const successors = rec ? rec.supersededBy : [];
      if (successors.length === 0) break;
      const next = successors[0];
      if (seen.has(next)) {
        break; // a supersedes cycle would spin forever otherwise
      }
      chain.push(next);
      seen.add(next);
      current = next;
    }
    resolutions.push({ seed, active: current, chain });
  }
  return resolutions;
}
