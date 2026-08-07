"""TDD for discover.py -- the mechanical half of wiki-ingest's duplicate-
detection step (issue #60), extended (#102) to return the full discovery
payload (summary/tags/volatility/superseded_by per candidate, plus the
vault's tag vocabulary) off a draft ``IngestPlan``. ``Vault.search`` itself
is covered by test_vault.py / test_search_index.py; this file pins
``discover.check``'s query construction and hint classification, plus the
four properties named in #60's Notes.

Thresholds under test are the #63 calibration defaults (duplicate >=15,
related >=5), passed explicitly where a test's whole point is the boundary,
so a future threshold retune doesn't silently break an unrelated test.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from discover import DiscoveryCandidate, _classify, check, discover_plan
import ingest
from ingest import PagePlan


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
        "concepts/connection-pooling.md",
        "Connection Pooling in Postgres",
        "Reuse connections instead of opening a new one per request.",
        "Connection pooling reduces per-request handshake overhead by "
        "reusing a fixed set of open connections across callers.",
    )
    _write_page(
        tmp_path,
        "concepts/sourdough-starter.md",
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
        assert any(c.page_ref == "wiki/concepts/connection-pooling.md" for c in candidates)


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
        assert any(c.page_ref == "wiki/concepts/connection-pooling.md" for c in candidates)


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
            c for c in candidates if c.page_ref == "wiki/concepts/connection-pooling.md"
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
        assert candidates[0].page_ref == "wiki/concepts/connection-pooling.md"


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
        (top,) = [c for c in candidates if c.page_ref == "wiki/concepts/connection-pooling.md"]
        assert top.hint == "duplicate"

    def test_returns_discovery_candidate_instances(self, vault_root):
        candidates = check(vault_root, title="Connection Pooling", summary="", body="")
        assert all(isinstance(c, DiscoveryCandidate) for c in candidates)

    def test_limit_is_respected(self, vault_root):
        candidates = check(vault_root, title="Connection Pooling", summary="", body="", limit=1)
        assert len(candidates) <= 1


class TestCheckReturnsFullPayload:
    """#102: the fields the agent used to open a page to recover -- summary
    for edge-typing, tags/volatility/superseded_by -- come back on every
    candidate, not just rel/title/score/hint."""

    def test_candidate_carries_summary_tags_volatility(self, vault_root):
        candidates = check(
            vault_root,
            title="Connection Pooling in Postgres",
            summary="",
            body="",
        )
        (top,) = [c for c in candidates if c.page_ref == "wiki/concepts/connection-pooling.md"]
        assert top.summary == "Reuse connections instead of opening a new one per request."
        assert isinstance(top.tags, list)
        assert isinstance(top.volatility, str)
        assert top.superseded_by is None


class TestDiscoverPlan:
    """#102: one call per draft plan, not one call per candidate chunk."""

    def test_runs_check_for_every_page_in_the_plan(self, vault_root):
        pages = [
            PagePlan(op="create", title="Connection Pooling in Postgres", body="", frontmatter={"summary": ""}),
            PagePlan(op="create", title="Feeding a Sourdough Starter", body="", frontmatter={"summary": "Daily flour-and-water feeding keeps a starter active."}),
        ]
        results = discover_plan(vault_root, pages)
        titles = [title for title, _ in results]
        assert titles == ["Connection Pooling in Postgres", "Feeding a Sourdough Starter"]
        pooling_candidates = dict(results)["Connection Pooling in Postgres"]
        assert any(c.page_ref == "wiki/concepts/connection-pooling.md" for c in pooling_candidates)

    def test_update_page_with_no_body_does_not_crash(self, vault_root):
        pages = [PagePlan(op="update", title="Connection Pooling in Postgres", page_ref="wiki/concepts/connection-pooling.md", frontmatter={})]
        results = discover_plan(vault_root, pages)
        assert len(results) == 1


class TestVocabularyCLI:
    """#102: --plan mode's JSON payload carries the vault's tag vocabulary
    alongside per-page candidates."""

    def test_plan_cli_output_carries_pages_and_vocabulary(self, vault_root, monkeypatch, capsys):
        import discover as discover_mod

        plan_path = vault_root / "draft-plan.json"
        plan_path.write_text(
            json.dumps(
                {
                    "title": "test plan",
                    "pages": [
                        {"op": "create", "title": "Connection Pooling in Postgres", "body": "", "frontmatter": {"summary": ""}},
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(discover_mod.vault_mod, "resolve_vault_root", lambda: vault_root)
        discover_mod._main(["--plan", str(plan_path)])
        payload = json.loads(capsys.readouterr().out)
        assert "pages" in payload and "vocabulary" in payload
        assert payload["pages"][0]["title"] == "Connection Pooling in Postgres"
        assert any(c["page_ref"] == "wiki/concepts/connection-pooling.md" for c in payload["pages"][0]["candidates"])


class TestCandidatePageRefIsUsableVerbatum:
    """ADR-0009 acceptance (#123): a discover candidate's ``page_ref`` is
    directly usable as an IngestPlan ``update`` target *and* as an edge
    target — no ``wiki/``-prefix conversion step between the two calls."""

    def test_candidate_page_ref_passes_ingest_validate_as_update_and_edge_target(self, vault_root):
        candidates = check(
            vault_root,
            title="Connection Pooling in Postgres",
            summary="",
            body="",
        )
        (candidate,) = [c for c in candidates if c.page_ref == "wiki/concepts/connection-pooling.md"]
        page_ref = candidate.page_ref

        plan = ingest.IngestPlan.from_dict(
            {
                "title": "Reuse a discover candidate verbatim",
                "pages": [
                    {
                        "op": "update",
                        "title": candidate.title,
                        "page_ref": page_ref,
                        "frontmatter": {"volatility": "stable"},
                        "edges": {},
                    },
                    {
                        "op": "create",
                        "kind": "concept",
                        "title": "New Sibling",
                        "body": "# New Sibling\n",
                        "frontmatter": {},
                        "edges": {"related": [page_ref]},
                    },
                ],
            }
        )
        ingest.validate(plan, vault_root)  # no raise — both uses accepted verbatim
