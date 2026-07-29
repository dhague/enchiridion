"""Overlap detection for ingestion candidates.

Fronts ``Vault.search`` with hint classification, so wiki-ingest's
duplicate-detection step -- the one where a miss creates a duplicate page --
is deterministic mechanics, testable in pytest, rather than an agent
re-parsing ``search.py --json`` and re-deriving "too similar" from prose
each run. See issue #60.

The query is deliberately **OR of the candidate's own words**, not
``search_index.tokenize_query``'s default AND-across-terms: an AND query
built from a whole title+summary+body needs literally every one of those
words present in a candidate page, so appending the planned page's own
(necessarily novel) summary/body text to the query silently drops real
duplicates from the result set -- measured directly: adding an unrelated
sentence of new summary text to an otherwise-exact-title query zeroed out
a hit that scored 18 on title alone. BM25's IDF weighting is what keeps an
OR query precise -- common words contribute little to the score -- so it is
the ranking, not query strictness, that separates ``distinct`` from the rest.

Thresholds are calibrated against the live dogfooding vault (issue #63).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

import vault as vault_mod
from vault import Vault

Hint = Literal["duplicate", "refines", "related", "distinct"]

#: Calibrated against the dogfooding vault (#63). Parameters on ``check``,
#: not buried in this module, so the eval harness can tune them.
DUPLICATE_THRESHOLD = 15.0
RELATED_THRESHOLD = 5.0

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class OverlapCandidate:
    rel: str
    title: str
    score: float
    hint: Hint


def _title_tokens(title: str) -> set[str]:
    return set(_WORD_RE.findall(title.lower()))


def _or_query(*texts: str) -> str:
    """Unique words across ``texts``, phrase-quoted and OR-joined -- an FTS5
    ``raw=True`` expression, deliberately not the default AND-across-terms
    (see module docstring)."""
    seen: dict[str, None] = {}
    for text in texts:
        for word in _WORD_RE.findall(text.lower()):
            seen.setdefault(word, None)
    return " OR ".join(f'"{word}"' for word in seen)


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
    limit: int = 20,
    duplicate_threshold: float = DUPLICATE_THRESHOLD,
    related_threshold: float = RELATED_THRESHOLD,
) -> list[OverlapCandidate]:
    """Search the vault for pages overlapping a planned page, classifying
    each hit's relationship to it.

    ``title``/``summary``/``body`` are the planned page's own drafted text
    -- the query is built from exactly what the candidate says, never a
    paraphrase, so the vocabulary-mismatch trap that undermines retrieval's
    query expansion doesn't apply here the same way.
    """
    vault = Vault(vault_root)
    query = _or_query(title, summary, body)
    hits = vault.search(query, raw=True, limit=limit) if query else []
    planned_tokens = _title_tokens(title)
    return [
        OverlapCandidate(
            rel=hit.rel,
            title=hit.title,
            score=hit.score,
            hint=_classify(
                hit.score,
                bool(planned_tokens & _title_tokens(hit.title)),
                duplicate_threshold=duplicate_threshold,
                related_threshold=related_threshold,
            ),
        )
        for hit in hits
    ]


# --- CLI ---------------------------------------------------------------------


def _main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - thin CLI wrapper
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", default="", help="the planned page's own title")
    parser.add_argument("--summary", default="", help="the planned page's own summary")
    parser.add_argument(
        "--body-file", default=None,
        help="path to the planned page's own body text (omit for title/summary-only)",
    )
    parser.add_argument("--limit", type=int, default=20, help="max candidates (default 20)")
    parser.add_argument("--duplicate-threshold", type=float, default=DUPLICATE_THRESHOLD)
    parser.add_argument("--related-threshold", type=float, default=RELATED_THRESHOLD)
    args = parser.parse_args(argv)

    body = ""
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")

    root = vault_mod.resolve_vault_root()
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
        print(json.dumps({"rel": c.rel, "title": c.title, "score": c.score, "hint": c.hint}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
