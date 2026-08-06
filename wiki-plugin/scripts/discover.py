"""Single-call discovery for ingestion: overlap candidates plus the tag
vocabulary, driven off a draft ``IngestPlan``.

Fronts ``Vault.search`` with hint classification, so wiki-ingest's
duplicate-detection step — the one where a miss creates a duplicate page — is
deterministic mechanics rather than an agent re-deriving "too similar" from
prose each run. Each candidate carries back everything the agent would
otherwise open the page to read (summary, tags, volatility, supersession), so
the cite / edge-type / tag-reuse steps collapse into one call instead of N
page reads.

**The query is OR of the candidate's own words** (``raw=True``), never
``tokenize_query``'s default AND-across-terms. An AND query built from a
whole title+summary+body demands every one of those words be present in the
candidate page — so the planned page's necessarily-novel summary and body
text silently zero out real duplicates that title alone would have found.
Precision comes instead from BM25's IDF weighting, where common words
contribute almost nothing to the score.

Thresholds are calibrated against the live dogfooding vault (#63).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, Sequence

import vault as vault_mod
from ingest import IngestPlan, PagePlan
from vault import Vault

Hint = Literal["duplicate", "refines", "related", "distinct"]

#: Calibrated against the dogfooding vault (#63). Also parameters on
#: ``check``, so the eval harness can tune them without editing this module.
DUPLICATE_THRESHOLD = 15.0
RELATED_THRESHOLD = 5.0

#: Deliberately generous: a narrow cap hides exactly the near-duplicates
#: this call exists to surface.
DEFAULT_LIMIT = 200

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class DiscoveryCandidate:
    rel: str
    title: str
    score: float
    hint: Hint
    summary: str
    tags: list[str]
    volatility: str
    superseded_by: str | None


def _title_tokens(title: str) -> set[str]:
    return set(_WORD_RE.findall(title.lower()))


def _or_query(*texts: str) -> str:
    """Unique words across ``texts``, phrase-quoted and OR-joined — an FTS5
    ``raw=True`` expression. OR, not AND; see the module docstring."""
    seen: dict[str, None] = {}
    for text in texts:
        for word in _WORD_RE.findall(text.lower()):
            seen.setdefault(word, None)
    return " OR ".join(f'"{word}"' for word in seen)


@lru_cache(maxsize=None)
def _vault_for(vault_root: Path) -> Vault:
    """One ``Vault``/index connection per root per process. A plan-driven run
    calls :func:`check` once per planned page; a fresh ``Vault`` each time
    would open a new sqlite connection per page and race its own uncommitted
    transaction (``database is locked``)."""
    return Vault(vault_root)


def _classify(
    score: float,
    shares_title_token: bool,
    *,
    duplicate_threshold: float,
    related_threshold: float,
) -> Hint:
    if score >= duplicate_threshold:
        return "duplicate" if shares_title_token else "refines"
    if score >= related_threshold:
        return "related"
    return "distinct"


def check(
    vault_root: Path,
    *,
    title: str,
    summary: str,
    body: str,
    limit: int = DEFAULT_LIMIT,
    duplicate_threshold: float = DUPLICATE_THRESHOLD,
    related_threshold: float = RELATED_THRESHOLD,
) -> list[DiscoveryCandidate]:
    """Search for pages overlapping a planned page, classifying each hit's
    relationship to it.

    ``title``/``summary``/``body`` must be the planned page's own drafted
    text: the query is built from exactly what the candidate says, never a
    paraphrase, which is why retrieval's vocabulary-mismatch problem doesn't
    bite here.
    """
    vault = _vault_for(vault_root)
    query = _or_query(title, summary, body)
    hits = vault.search(query, raw=True, limit=limit) if query else []
    planned_tokens = _title_tokens(title)
    return [
        DiscoveryCandidate(
            rel=hit.rel,
            title=hit.title,
            score=hit.score,
            hint=_classify(
                hit.score,
                bool(planned_tokens & _title_tokens(hit.title)),
                duplicate_threshold=duplicate_threshold,
                related_threshold=related_threshold,
            ),
            summary=hit.summary,
            tags=hit.tags,
            volatility=hit.volatility,
            superseded_by=hit.superseded_by,
        )
        for hit in hits
    ]


def discover_plan(
    vault_root: Path,
    pages: Sequence[PagePlan],
    *,
    limit: int = DEFAULT_LIMIT,
    duplicate_threshold: float = DUPLICATE_THRESHOLD,
    related_threshold: float = RELATED_THRESHOLD,
) -> list[tuple[str, list[DiscoveryCandidate]]]:
    """Run :func:`check` for every page a draft plan proposes — one call
    however many chunks the plan carries."""
    return [
        (
            page.title,
            check(
                vault_root,
                title=page.title,
                summary=page.frontmatter.get("summary", ""),
                body=page.body or "",
                limit=limit,
                duplicate_threshold=duplicate_threshold,
                related_threshold=related_threshold,
            ),
        )
        for page in pages
    ]


# --- CLI ---------------------------------------------------------------------


def _candidate_json(c: DiscoveryCandidate) -> dict:
    return {
        "rel": c.rel,
        "title": c.title,
        "score": c.score,
        "hint": c.hint,
        "summary": c.summary,
        "tags": c.tags,
        "volatility": c.volatility,
        "superseded_by": c.superseded_by,
    }


def _main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - thin CLI wrapper
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plan", default=None,
        help="path to a draft IngestPlan JSON; discovers candidates for every page in it, "
        "plus the vault's tag vocabulary",
    )
    parser.add_argument("--title", default="", help="the planned page's own title (single-page mode)")
    parser.add_argument("--summary", default="", help="the planned page's own summary (single-page mode)")
    parser.add_argument(
        "--body-file", default=None,
        help="path to the planned page's own body text (single-page mode; omit for title/summary-only)",
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"max candidates per page (default {DEFAULT_LIMIT})")
    parser.add_argument("--duplicate-threshold", type=float, default=DUPLICATE_THRESHOLD)
    parser.add_argument("--related-threshold", type=float, default=RELATED_THRESHOLD)
    args = parser.parse_args(argv)

    root = vault_mod.resolve_vault_root()

    if args.plan:
        plan_dict = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        plan = IngestPlan.from_dict(plan_dict)
        per_page = discover_plan(
            root,
            plan.pages,
            limit=args.limit,
            duplicate_threshold=args.duplicate_threshold,
            related_threshold=args.related_threshold,
        )
        payload = {
            "pages": [
                {"title": title, "candidates": [_candidate_json(c) for c in candidates]}
                for title, candidates in per_page
            ],
            "vocabulary": [
                {"tag": tag, "count": n} for tag, n in _vault_for(root).tag_vocabulary()
            ],
        }
        print(json.dumps(payload, indent=2))
        return 0

    body = ""
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")

    candidates = check(
        root,
        title=args.title,
        summary=args.summary,
        body=body,
        limit=args.limit,
        duplicate_threshold=args.duplicate_threshold,
        related_threshold=args.related_threshold,
    )
    for c in candidates:
        print(json.dumps(_candidate_json(c)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
