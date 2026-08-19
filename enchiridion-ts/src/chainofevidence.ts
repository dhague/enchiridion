/**
 * chainofevidence — the page -> stub -> raw file chain every raw ingestion
 * must leave. Ported from enchiridion-go/internal/chainofevidence.
 *
 * **The rule** (stated here once; the ingest and commit packages only point
 * at it): a raw file that produces pages at all must also produce a
 * `wiki/sources/` stand-in for itself — a stub whose `raw_source` points back
 * at the file — and every other page produced from it must carry a `source`
 * edge back to that stub. So a reader can always walk from a claim to the
 * artifact it came from.
 *
 * Two callers, one function, so the two checks cannot diverge: `ingest`
 * validates a plan before any write; `commit` is the hard gate. Neither knows
 * which one this is serving.
 */

import path from "node:path";
import { KindFolders } from "./place.js";
import { Page, linkDest, resolveLinkDest } from "./wikipage.js";

/** The `source` kind's folder — the one hardcoded folder string this module
 * needs, kept in sync with place rather than duplicated. */
const sourceDir = `wiki/${KindFolders["source"]}`;

/**
 * Report whether staged leaves a valid page -> stub -> raw chain.
 *
 * staged is every page one ingestion/commit touches, keyed by its
 * (post-write) vault-relative path. Returns human-readable error strings,
 * empty when the chain holds. Both loops iterate the staged refs in sorted
 * order, so the result never depends on map order.
 *
 * A page whose frontmatter cannot be parsed is an error in its own right,
 * thrown rather than silently treated as edge-less.
 */
export function check(staged: Record<string, Page>, raw: string): string[] {
  raw = path.posix.normalize(raw);
  const refs = sortedRefs(staged);

  let stubRef = "";
  for (const pageRef of refs) {
    if (path.posix.dirname(pageRef) !== sourceDir) continue;
    const link = staged[pageRef].getString("raw_source");
    if (link === "") continue;
    const { dest, ok } = linkDest(link);
    if (ok && resolveLinkDest(dest, path.posix.dirname(pageRef)) === raw) {
      stubRef = pageRef;
      break;
    }
  }

  if (stubRef === "") {
    return [
      `${raw} needs a ${KindFolders["source"]}/ page whose raw_source points at it ` +
        `— every ingested raw file gets a stand-in, even a thin stub`,
    ];
  }

  const problems: string[] = [];
  for (const pageRef of refs) {
    if (pageRef === stubRef) continue;
    const links = staged[pageRef].getStringList("source");
    const pageDir = path.posix.dirname(pageRef);
    let found = false;
    for (const link of links) {
      const { dest, ok } = linkDest(link);
      if (ok && resolveLinkDest(dest, pageDir) === stubRef) {
        found = true;
        break;
      }
    }
    if (!found) {
      problems.push(`${pageRef} needs a source edge to the stub ${stubRef}`);
    }
  }
  return problems;
}

function sortedRefs(staged: Record<string, Page>): string[] {
  return Object.keys(staged).sort();
}
