"""TDD for overlap.py -- the mechanical half of wiki-ingest's duplicate-
detection step (issue #60). ``Vault.search`` itself is covered by
test_vault.py / test_search_index.py; this file pins ``overlap.check``'s
query construction and hint classification, plus the four properties named
in #60's Notes.

Thresholds under test are the #63 calibration defaults (duplicate >=15,
related >=5), passed explicitly where a test's whole point is the boundary,
so a future threshold retune doesn't silently break an unrelated test.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from overlap import OverlapCandidate, _classify, check


# --- _classify: pure boundary logic ----------------------------------------


class TestClassify:
    def test_high_score_with_shared_title_token_is_duplicate(self):
        assert _classify(20.0, True, duplicate_threshold=15.0, related_threshold=5.0) == "duplicate"

    def test_high_score_without_shared_title_token_is_refines(self):
        assert _classify(20.0, False, duplicate_threshold=15.0, related_threshold=5.0) == "refines"

    def test_mid_score_is_related_regardless_of_title(self):
        assert _classify(10.0, True, duplicate_threshold=15.0, related_threshold=5.0) == "related"
        assert _classify(10.0, False, duplicate_threshold=15.0, related_threshold=5.0) == "related"

    def test_low_score_is_distinct(self):
        assert _classify(1.0, True, duplicate_threshold=15.0, related_threshold=5.0) == "distinct"

    def test_duplicate_threshold_is_inclusive(self):
        assert _classify(15.0, True, duplicate_threshold=15.0, related_threshold=5.0) == "duplicate"

    def test_related_threshold_is_inclusive(self):
        assert _classify(5.0, False, duplicate_threshold=15.0, related_threshold=5.0) == "related"


# --- check(): integration against a real vault -----------------------------


def _write_page(root: Path, rel: str, title: str, summary: str, body: str) -> None:
    path = root / "wiki" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {title}\nsummary: {summary}\n---\n{body}\n",
        encoding="utf-8",
    )


@pytest.fixture
def vault_root(tmp_path):
    _write_page(
        tmp_path,
        "concept/connection-pooling.md",
        "Connection Pooling in Postgres",
        "Reuse connections instead of opening a new one per request.",
        "Connection pooling reduces per-request handshake overhead by "
        "reusing a fixed set of open connections across callers.",
    )
    _write_page(
        tmp_path,
        "concept/sourdough-starter.md",
        "Feeding a Sourdough Starter",
        "Daily flour-and-water feeding keeps a starter active.",
        "A sourdough starter needs equal parts flour and water once a day, "
        "kept warm, to stay active enough to leaven bread.",
    )
    return tmp_path


class TestCheckFindsOwnTitle:
    """A page in the vault is found by its own title (#60 Notes property 1)."""

    def test_own_title_surfaces_the_page(self, vault_root):
        candidates = check(
            vault_root,
            title="Connection Pooling in Postgres",
            summary="",
            body="",
        )
        assert any(c.rel == "concept/connection-pooling.md" for c in candidates)


class TestCheckSurvivesNoisyNewText:
    """An OR-of-words query is the whole point: an AND-across-title+summary
    +body query needs literally every word present on a candidate page, so
    the planned page's own (necessarily new, not-yet-existing) summary text
    would silently zero out an otherwise exact-title match. Regression for
    that failure mode."""

    def test_unrelated_summary_text_does_not_suppress_a_real_title_match(self, vault_root):
        candidates = check(
            vault_root,
            title="Connection Pooling in Postgres",
            summary="A totally unrelated sentence about zebras and volcanoes.",
            body="",
        )
        assert any(c.rel == "concept/connection-pooling.md" for c in candidates)


class TestCheckDisjointTitlesAreDistinct:
    """Two pages with disjoint titles yield distinct (#60 Notes property 2)."""

    def test_unrelated_topic_does_not_surface_as_overlap(self, vault_root):
        candidates = check(
            vault_root,
            title="Feeding a Sourdough Starter",
            summary="Daily flour-and-water feeding keeps a starter active.",
            body="",
        )
        connection_pooling_hits = [
            c for c in candidates if c.rel == "concept/connection-pooling.md"
        ]
        assert all(c.hint == "distinct" for c in connection_pooling_hits)


class TestCheckBodyIsQueryScoresHighest:
    """A page whose body is the query string yields the highest score
    (#60 Notes property 3)."""

    def test_verbatim_body_match_ranks_first(self, vault_root):
        candidates = check(
            vault_root,
            title="",
            summary="",
            body="Connection pooling reduces per-request handshake overhead by "
            "reusing a fixed set of open connections across callers.",
        )
        assert candidates
        assert candidates[0].rel == "concept/connection-pooling.md"


class TestCheckHintsOnRealHits:
    def test_near_duplicate_title_and_summary_hints_duplicate(self, vault_root):
        # BM25 is IDF-weighted, so its absolute scale depends on corpus size
        # (#63): the #60 calibration (duplicate>=15) was measured against a
        # 65-page vault and doesn't transfer to this two-page fixture, whose
        # matching score sits near zero. Pass fixture-scaled thresholds --
        # this test is pinning check()'s score-plus-title-token composition,
        # not the calibrated numbers (those are the dogfooding hand-run in
        # #63's resolution comment).
        candidates = check(
            vault_root,
            title="Connection Pooling in Postgres",
            summary="Reuse connections instead of opening a new one per request.",
            body="",
            duplicate_threshold=1e-06,
            related_threshold=1e-08,
        )
        (top,) = [c for c in candidates if c.rel == "concept/connection-pooling.md"]
        assert top.hint == "duplicate"

    def test_returns_overlap_candidate_instances(self, vault_root):
        candidates = check(vault_root, title="Connection Pooling", summary="", body="")
        assert all(isinstance(c, OverlapCandidate) for c in candidates)

    def test_limit_is_respected(self, vault_root):
        candidates = check(vault_root, title="Connection Pooling", summary="", body="", limit=1)
        assert len(candidates) <= 1
