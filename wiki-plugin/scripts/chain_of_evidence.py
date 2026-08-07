"""The page -> stub -> raw file chain every raw ingestion must leave.

**The rule** (stated here once; :mod:`ingest` and :mod:`commit` only point at
it): a raw file that produces pages at all must also produce a
``wiki/sources/`` stand-in for itself — a stub whose ``raw_source`` points
back at the file — and every other page produced from it must carry a
``source`` edge back to that stub. So a reader can always walk from a claim
to the artifact it came from.

Two callers, one function, so the two checks cannot diverge: :mod:`ingest`
validates a plan before any write (``staged`` projected from the plan, merged
with on-disk state for updates); :mod:`commit` is the hard gate (``staged``
read straight from disk). Neither knows which one this is serving.
"""
from __future__ import annotations

import posixpath

import place
from wikipage import WikiPage, link_dest, resolve_link_dest

#: The `source` kind's folder — the one hardcoded folder string this module
#: needs, kept in sync with :mod:`place` rather than duplicated.
_SOURCE_DIR = f"wiki/{place.KIND_FOLDERS['source']}"


def check(staged: dict[str, WikiPage], raw: str) -> list[str]:
    """Check that ``staged`` leaves a valid page -> stub -> ``raw`` chain.

    ``staged`` is every page one ingestion/commit touches, keyed by its
    (post-write) vault-relative path. Returns human-readable error strings,
    empty when the chain holds. Both loops iterate ``sorted(staged)``, so the
    result never depends on dict order.
    """
    raw = posixpath.normpath(raw)

    stub_ref: str | None = None
    for page_ref in sorted(staged):
        if posixpath.dirname(page_ref) != _SOURCE_DIR:
            continue
        link = staged[page_ref].get("raw_source")
        if not isinstance(link, str):
            continue
        dest = link_dest(link)
        if dest is not None and resolve_link_dest(dest, posixpath.dirname(page_ref), prefix="") == raw:
            stub_ref = page_ref
            break

    if stub_ref is None:
        return [
            f"{raw} needs a {place.KIND_FOLDERS['source']}/ page whose raw_source points at it "
            "— every ingested raw file gets a stand-in, even a thin stub"
        ]

    errors: list[str] = []
    for page_ref in sorted(staged):
        if page_ref == stub_ref:
            continue
        source_edges = staged[page_ref].get("source")
        page_dir = posixpath.dirname(page_ref)
        targets: set[str] = set()
        if isinstance(source_edges, list):
            for link in source_edges:
                if not isinstance(link, str):
                    continue
                dest = link_dest(link)
                if dest is not None:
                    targets.add(resolve_link_dest(dest, page_dir, prefix=""))
        if stub_ref not in targets:
            errors.append(f"{page_ref} needs a source edge to the stub {stub_ref}")
    return errors
