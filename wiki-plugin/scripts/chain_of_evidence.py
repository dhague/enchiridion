"""The page -> stub -> raw file chain every raw ingestion must leave (#34 point 4).

A raw file that produces pages at all must produce a ``wiki/source/`` stand-in
for itself (a stub, whose ``raw_source`` points back at the file), and every
other page produced from it must carry a ``source`` edge back to that stub —
so a reader can always walk from a claim to the artifact it came from.

One :func:`check` is called from both the agent-time layer (:mod:`ingest`,
which validates a plan before any write — its ``staged`` pages are projected
from the plan, merged with on-disk state for updates) and the commit-time
hard gate (:mod:`commit`, whose ``staged`` pages are read straight from
disk). Both callers assemble the same ``{vault-rel: WikiPage}`` shape and
call this one function, so a divergence between the two checks is impossible
by construction — nothing here knows or cares which caller it's serving.
"""
from __future__ import annotations

import posixpath

from wikipage import WikiPage, link_dest, resolve_link_dest


def check(staged: dict[str, WikiPage], raw: str) -> list[str]:
    """Check that ``staged`` leaves a valid page -> stub -> ``raw`` chain.

    ``staged`` is every page one ingestion/commit touches, keyed by its
    (post-write) vault-relative path. Returns human-readable error strings;
    empty when the chain holds. Order-independent over ``staged``'s
    iteration order — the stub found and the violations reported don't
    depend on dict order, only on the paths and frontmatter themselves.
    """
    raw = posixpath.normpath(raw)

    stub_rel: str | None = None
    for rel in sorted(staged):
        if posixpath.dirname(rel) != "wiki/source":
            continue
        link = staged[rel].get("raw_source")
        if not isinstance(link, str):
            continue
        dest = link_dest(link)
        if dest is not None and resolve_link_dest(dest, posixpath.dirname(rel), prefix="") == raw:
            stub_rel = rel
            break

    if stub_rel is None:
        return [
            f"{raw} needs a source/ page whose raw_source points at it "
            "— every ingested raw file gets a stand-in, even a thin stub"
        ]

    errors: list[str] = []
    for rel in sorted(staged):
        if rel == stub_rel:
            continue
        source_edges = staged[rel].get("source")
        page_dir = posixpath.dirname(rel)
        targets: set[str] = set()
        if isinstance(source_edges, list):
            for link in source_edges:
                if not isinstance(link, str):
                    continue
                dest = link_dest(link)
                if dest is not None:
                    targets.add(resolve_link_dest(dest, page_dir, prefix=""))
        if stub_rel not in targets:
            errors.append(f"{rel} needs a source edge to the stub {stub_rel}")
    return errors
