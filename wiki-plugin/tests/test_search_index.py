"""TDD for ``search_index.py`` — the SQLite FTS5 lexical index for the Vault.

Per ADR-0005 (TDD for scripts, evals for agents), the deterministic layer is
test-first. The design (#36) is settled; these tests pin the behavior that
the agent layer (``wiki-researcher``) and the CLI (``search.py``) rely on.

The unit under test is :class:`search_index.SearchIndex`. ``Vault`` integration
(``Vault.search`` + the inline index update in ``Vault.write``/``set``/``merge``/
``move_page``) is exercised in ``test_wikipage.py`` so this file stays focused
on the index itself.
"""
from __future__ import annotations

import json
import os
import posixpath
import sqlite3
from pathlib import Path

import pytest

import search_index
from search_index import SearchHit, SearchIndex, tokenize_query


# --- helpers --------------------------------------------------------------


def _vault(root: Path, pages: dict[str, str]) -> Path:
    """Materialise a ``wiki/`` tree from ``{wiki-relative rel: text}``."""
    wiki = root / "wiki"
    for rel, text in pages.items():
        p = wiki / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def _page(
    rel: str = "concept/page.md",
    title: str = "Page",
    summary: str = "summary.",
    tags: list[str] | None = None,
    source_date: str = "2026-01-01",
    volatility: str = "stable",
    body: str = "Body text.",
    supersedes: list[str] | None = None,
) -> str:
    """A complete markdown page with frontmatter.

    ``supersedes`` is a list of *wiki-relative* rels (e.g. ``"concept/old.md"``);
    the helper converts each to a page-relative markdown link so
    ``page_record``'s edge-rebase gives the wiki-relative form back.
    """
    parts = [
        "---",
        f"title: {title}",
        f"summary: {summary}",
        f"tags: [{', '.join(tags or [])}]",
        f"source_date: {source_date}",
        f"volatility: {volatility}",
    ]
    if supersedes:
        parts.append("supersedes:")
        page_dir = posixpath.dirname(rel)
        for t in supersedes:
            link_dest = posixpath.relpath(t, page_dir)
            parts.append(f'  - "[{Path(t).stem}]({link_dest})"')
    parts.append("---")
    parts.append(body)
    return "\n".join(parts) + "\n"


# --- tokenize_query -------------------------------------------------------


class TestTokenizeQuery:
    """The FTS5 syntax footgun (#36 §2): hyphenated terms are a syntax error
    in ``MATCH`` unless phrase-quoted. The default path must split-and-quote
    so vault vocabulary can't crash a search."""

    def test_hyphenated_term_is_phrase_quoted(self):
        assert tokenize_query("wiki-knowledge") == '"wiki-knowledge"'

    def test_whitespace_separated_terms_each_quoted(self):
        assert tokenize_query("connection pooling") == '"connection" "pooling"'

    def test_single_word_passes_through(self):
        assert tokenize_query("ingest") == '"ingest"'

    def test_inner_double_quote_is_escaped(self):
        assert tokenize_query('say "hi"') == '"say" "\\"hi\\""'

    def test_empty_string_returns_empty(self):
        assert tokenize_query("") == ""


# --- capability probe / FTS5 fallback ------------------------------------


class TestBackend:
    def test_opens_at_wiki_knowledge_dir(self, tmp_path):
        SearchIndex(_vault(tmp_path, {}))
        assert (tmp_path / ".wiki-knowledge" / "index.db").exists()

    def test_reports_backend(self, tmp_path):
        idx = SearchIndex(_vault(tmp_path, {}))
        assert idx.backend in ("fts5", "re")

    def test_re_fallback_when_fts5_missing(self, monkeypatch, tmp_path):
        """A SQLite without FTS5 must not crash; ``re`` is the fallback."""
        monkeypatch.setattr(
            search_index, "_probe_fts5", lambda _conn: False
        )
        idx = SearchIndex(_vault(tmp_path, {}))
        assert idx.backend == "re"

    def test_re_fallback_still_finds_text(self, monkeypatch, tmp_path):
        """The ``re`` backend must answer the same queries, just slower."""
        monkeypatch.setattr(
            search_index, "_probe_fts5", lambda _conn: False
        )
        pages = {"concept/a.md": _page(rel="concept/a.md", body="alpha content")}
        idx = SearchIndex(_vault(tmp_path, pages))
        hits = idx.search("alpha")
        assert [h.rel for h in hits] == ["concept/a.md"]


# --- tag_counts (#102) -----------------------------------------------------


class TestTagCounts:
    """The vault's whole tag vocabulary with usage counts (#102) -- discovery's
    reuse-first hand-off, so an agent never greps pages for existing tags."""

    def test_empty_vault_returns_empty(self, tmp_path):
        idx = SearchIndex(_vault(tmp_path, {}))
        assert idx.tag_counts() == []

    def test_counts_and_orders_most_used_first(self, tmp_path):
        pages = {
            "concept/a.md": _page(rel="concept/a.md", tags=["python", "testing"]),
            "concept/b.md": _page(rel="concept/b.md", tags=["python"]),
            "concept/c.md": _page(rel="concept/c.md", tags=["zeta"]),
        }
        idx = SearchIndex(_vault(tmp_path, pages))
        idx.reindex()
        assert idx.tag_counts() == [("python", 2), ("testing", 1), ("zeta", 1)]

    def test_scans_before_counting(self, tmp_path):
        idx = SearchIndex(_vault(tmp_path, {}))
        assert idx.tag_counts() == []
        (tmp_path / "wiki" / "concept").mkdir(parents=True, exist_ok=True)
        (tmp_path / "wiki" / "concept" / "a.md").write_text(
            _page(rel="concept/a.md", tags=["fresh"]), encoding="utf-8"
        )
        assert idx.tag_counts() == [("fresh", 1)]


# --- lifecycle / status --------------------------------------------------


class TestLifecycle:
    def test_status_reports_zero_pages_before_scan(self, tmp_path):
        idx = SearchIndex(_vault(tmp_path, {}))
        assert idx.status().pages == 0

    def test_status_reports_pages_after_reindex(self, tmp_path):
        pages = {"concept/a.md": _page(rel="concept/a.md")}
        idx = SearchIndex(_vault(tmp_path, pages))
        idx.reindex()
        assert idx.status().pages == 1

    def test_reindex_stats(self, tmp_path):
        pages = {
            "concept/a.md": _page(rel="concept/a.md"),
            "concept/b.md": _page(rel="concept/b.md"),
        }
        idx = SearchIndex(_vault(tmp_path, pages))
        stats = idx.reindex()
        assert stats.pages == 2
        assert stats.inserted == 2
        assert stats.removed == 0


# --- search --------------------------------------------------------------


class TestSearch:
    def test_finds_page_by_text_in_body(self, tmp_path):
        pages = {
            "concept/a.md": _page(
                rel="concept/a.md", title="A", body="The quick brown fox."
            ),
            "concept/b.md": _page(
                rel="concept/b.md", title="B", body="Something else entirely."
            ),
        }
        idx = SearchIndex(_vault(tmp_path, pages))
        hits = idx.search("fox")
        assert [h.rel for h in hits] == ["concept/a.md"]

    def test_finds_page_by_text_in_title(self, tmp_path):
        pages = {
            "concept/a.md": _page(rel="concept/a.md", title="Prepared statements"),
            "concept/b.md": _page(rel="concept/b.md", title="Other"),
        }
        idx = SearchIndex(_vault(tmp_path, pages))
        hits = idx.search("prepared")
        assert [h.rel for h in hits] == ["concept/a.md"]

    def test_hyphenated_text_does_not_crash(self, tmp_path):
        """The footgun: 'wiki-knowledge' is a syntax error in FTS5 MATCH."""
        pages = {
            "concept/a.md": _page(
                rel="concept/a.md",
                tags=["wiki-knowledge"],
                body="A page about the wiki-knowledge plugin.",
            ),
        }
        idx = SearchIndex(_vault(tmp_path, pages))
        hits = idx.search("wiki-knowledge")
        assert [h.rel for h in hits] == ["concept/a.md"]

    def test_raw_escape_hatch_passes_literal(self, tmp_path):
        pages = {
            "concept/a.md": _page(
                rel="concept/a.md", body="ingest ingestion ingesting"
            ),
            "concept/b.md": _page(rel="concept/b.md", body="nothing related"),
        }
        idx = SearchIndex(_vault(tmp_path, pages))
        hits = idx.search("ingest*", raw=True)
        assert "concept/a.md" in [h.rel for h in hits]
        assert "concept/b.md" not in [h.rel for h in hits]

    def test_score_sign_is_higher_better(self, tmp_path):
        """``bm25()`` returns negative values; we negate so higher-is-better."""
        pages = {
            "concept/match.md": _page(
                rel="concept/match.md", body="fox fox fox fox"
            ),
            "concept/once.md": _page(
                rel="concept/once.md", body="only one fox here"
            ),
        }
        idx = SearchIndex(_vault(tmp_path, pages))
        hits = idx.search("fox")
        scores = [h.score for h in hits]
        # Better match has strictly higher score than worse match.
        assert scores[0] > scores[1]
        # And the best is positive (we negated).
        assert scores[0] > 0

    def test_search_hit_has_full_record(self, tmp_path):
        pages = {
            "concept/a.md": _page(
                rel="concept/a.md",
                title="Foo",
                summary="A foo about things.",
                tags=["alpha", "beta"],
                source_date="2026-07-15",
                volatility="stable",
                body="alpha content about foo",
            ),
        }
        idx = SearchIndex(_vault(tmp_path, pages))
        (hit,) = idx.search("foo")
        assert hit.title == "Foo"
        assert hit.summary == "A foo about things."
        assert hit.tags == ["alpha", "beta"]
        assert hit.kind == "concept"
        assert hit.source_date == "2026-07-15"
        assert hit.volatility == "stable"
        assert hit.superseded_by is None

    def test_limit(self, tmp_path):
        pages = {f"concept/p{i}.md": _page(rel=f"concept/p{i}.md", body="fox") for i in range(5)}
        idx = SearchIndex(_vault(tmp_path, pages))
        hits = idx.search("fox", limit=2)
        assert len(hits) == 2


# --- metadata filters ----------------------------------------------------


class TestMetadataFilters:
    def _vault(self, tmp_path):
        pages = {
            "concept/foo.md": _page(
                rel="concept/foo.md", title="Foo",
                tags=["alpha", "beta"],
                source_date="2026-07-01", volatility="stable",
            ),
            "entity/bar.md": _page(
                rel="entity/bar.md", title="Bar",
                tags=["alpha"],
                source_date="2026-06-15", volatility="evolving",
            ),
            "concept/baz.md": _page(
                rel="concept/baz.md", title="Baz",
                tags=["gamma"],
                source_date="2026-05-01", volatility="stable",
            ),
        }
        return _vault(tmp_path, pages)

    def test_tags_all(self, tmp_path):
        idx = SearchIndex(self._vault(tmp_path))
        hits = idx.search(tags_all=["alpha", "beta"])
        assert [h.rel for h in hits] == ["concept/foo.md"]

    def test_tags_any(self, tmp_path):
        idx = SearchIndex(self._vault(tmp_path))
        hits = idx.search(tags_any=["alpha", "gamma"])
        assert {h.rel for h in hits} == {
            "concept/foo.md", "entity/bar.md", "concept/baz.md"
        }

    def test_kind_filter_scalar(self, tmp_path):
        idx = SearchIndex(self._vault(tmp_path))
        hits = idx.search(kind="concept")
        assert {h.rel for h in hits} == {"concept/foo.md", "concept/baz.md"}

    def test_kind_filter_list(self, tmp_path):
        idx = SearchIndex(self._vault(tmp_path))
        hits = idx.search(kind=["concept", "entity"])
        assert len(hits) == 3

    def test_since_uses_source_date_by_default(self, tmp_path):
        idx = SearchIndex(self._vault(tmp_path))
        hits = idx.search(since="2026-06-15")
        assert {h.rel for h in hits} == {"concept/foo.md", "entity/bar.md"}

    def test_until(self, tmp_path):
        idx = SearchIndex(self._vault(tmp_path))
        hits = idx.search(until="2026-06-15")
        assert {h.rel for h in hits} == {"entity/bar.md", "concept/baz.md"}

    def test_volatility_filter(self, tmp_path):
        idx = SearchIndex(self._vault(tmp_path))
        hits = idx.search(volatility=["stable"])
        assert {h.rel for h in hits} == {"concept/foo.md", "concept/baz.md"}


# --- supersession --------------------------------------------------------


class TestSupersession:
    def test_default_excludes_superseded(self, tmp_path):
        """``include_superseded=False`` (the default) must filter the
        superseded page out — this is the mechanization of #16's hand-run
        rule, and the single highest-value thing the index buys."""
        pages = {
            "concept/new.md": _page(
                rel="concept/new.md", title="New",
                supersedes=["concept/old.md"],
            ),
            "concept/old.md": _page(rel="concept/old.md", title="Old"),
        }
        idx = SearchIndex(_vault(tmp_path, pages))
        rels = [h.rel for h in idx.search()]
        assert "concept/old.md" not in rels
        assert "concept/new.md" in rels

    def test_include_superseded_true_keeps_them(self, tmp_path):
        pages = {
            "concept/new.md": _page(
                rel="concept/new.md", title="New",
                supersedes=["concept/old.md"],
            ),
            "concept/old.md": _page(rel="concept/old.md", title="Old"),
        }
        idx = SearchIndex(_vault(tmp_path, pages))
        rels = [h.rel for h in idx.search(include_superseded=True)]
        assert "concept/old.md" in rels
        assert "concept/new.md" in rels


# --- staleness scan ------------------------------------------------------


class TestStaleness:
    """The scan is the correctness path: ``Vault.write``'s inline update is
    a latency optimisation, not what makes the index right."""

    def test_picks_up_new_page(self, tmp_path):
        (tmp_path / "wiki" / "concept").mkdir(parents=True)
        idx = SearchIndex(tmp_path)
        assert idx.search("beta") == []
        (tmp_path / "wiki" / "concept" / "b.md").write_text(
            _page(rel="concept/b.md", body="beta content"),
            encoding="utf-8",
        )
        assert [h.rel for h in idx.search("beta")] == ["concept/b.md"]

    def test_picks_up_modified_page(self, tmp_path):
        path = tmp_path / "wiki" / "concept" / "a.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_page(rel="concept/a.md", body="original"), encoding="utf-8")
        idx = SearchIndex(tmp_path)
        assert [h.rel for h in idx.search("original")] == ["concept/a.md"]

        path.write_text(_page(rel="concept/a.md", body="rewritten"), encoding="utf-8")
        # Bump mtime so the scan notices (write_text may already, but be explicit).
        os.utime(path, None)
        assert [h.rel for h in idx.search("rewritten")] == ["concept/a.md"]
        # And the old term is no longer findable.
        assert idx.search("original") == []

    def test_drops_deleted_page(self, tmp_path):
        path = tmp_path / "wiki" / "concept" / "a.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_page(rel="concept/a.md", body="alpha"), encoding="utf-8")
        idx = SearchIndex(tmp_path)
        assert [h.rel for h in idx.search("alpha")] == ["concept/a.md"]

        path.unlink()
        assert idx.search("alpha") == []


# --- schema versioning --------------------------------------------------


class TestSchemaVersion:
    def test_mismatched_version_triggers_full_rebuild(self, tmp_path):
        (tmp_path / "wiki" / "concept").mkdir(parents=True)
        (tmp_path / "wiki" / "concept" / "a.md").write_text(
            _page(rel="concept/a.md", body="alpha"), encoding="utf-8"
        )
        # Pre-create the index with a version the code doesn't recognise.
        # Next open should detect the mismatch and rebuild from scratch.
        (tmp_path / ".wiki-knowledge").mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(tmp_path / ".wiki-knowledge" / "index.db") as conn:
            conn.executescript(
                """
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
                INSERT INTO meta VALUES ('schema_version', '999');
                """
            )
            conn.commit()

        idx = SearchIndex(tmp_path)
        assert [h.rel for h in idx.search("alpha")] == ["concept/a.md"]
        assert idx.status().schema_version == search_index.SCHEMA_VERSION


# --- inline update (used by Vault.write / move_page) --------------------


class TestInlineUpdate:
    """``upsert_page``/``remove_page`` are the *latency optimisation*
    Vault.write uses to skip a future scan. The file on disk is the
    source of truth for ``(mtime_ns, size)`` — these methods assume the
    file already exists. Their contract is: after the call, the next
    ``search`` sees the new state without needing to re-read the file."""

    def test_upsert_page_writes_to_index(self, tmp_path):
        path = tmp_path / "wiki" / "concept" / "new.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _page(rel="concept/new.md", body="foo content"), encoding="utf-8"
        )
        idx = SearchIndex(tmp_path)
        idx.upsert_page("concept/new.md", path.read_text(encoding="utf-8"))
        # Inline update populated the page table — the next scan will
        # see matching (mtime, size) and skip.
        rows = idx._conn.execute(
            "SELECT rel FROM page WHERE rel = ?", ("concept/new.md",)
        ).fetchall()
        assert rows == [("concept/new.md",)]

    def test_upsert_page_replaces_existing(self, tmp_path):
        path = tmp_path / "wiki" / "concept" / "a.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_page(rel="concept/a.md", body="original"), encoding="utf-8")
        idx = SearchIndex(tmp_path)
        # First upsert from the on-disk text.
        idx.upsert_page("concept/a.md", path.read_text(encoding="utf-8"))
        assert [h.rel for h in idx.search("original")] == ["concept/a.md"]
        # Now write a new body to disk (the file is what stat() reads) and
        # inline-upsert. The next scan sees matching mtime/size and skips.
        path.write_text(_page(rel="concept/a.md", body="replaced"), encoding="utf-8")
        idx.upsert_page("concept/a.md", path.read_text(encoding="utf-8"))
        assert [h.rel for h in idx.search("replaced")] == ["concept/a.md"]

    def test_remove_page_clears_index_row(self, tmp_path):
        path = tmp_path / "wiki" / "concept" / "a.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_page(rel="concept/a.md", body="alpha"), encoding="utf-8")
        idx = SearchIndex(tmp_path)
        idx.upsert_page("concept/a.md", path.read_text(encoding="utf-8"))
        idx.remove_page("concept/a.md")
        # The row is gone from the page table (the next scan will *not*
        # re-add it, because the file is still on disk; ``remove_page`` is
        # paired with the file delete — see the staleness test).
        rows = idx._conn.execute(
            "SELECT rel FROM page WHERE rel = ?", ("concept/a.md",)
        ).fetchall()
        assert rows == []


# --- git_date -----------------------------------------------------------


class TestGitDate:
    """``git_date`` is filled by one ``git log`` pass at scan/upsert time."""

    def test_git_date_is_none_for_uncommitted_page(self, tmp_path):
        # No git repo: git_date is None.
        idx = SearchIndex(_vault(tmp_path, {"concept/a.md": _page(rel="concept/a.md")}))
        (hit,) = idx.search()
        assert hit.git_date is None

    def test_git_date_populated_from_git_log(self, tmp_path):
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True
        )
        subprocess.run(
            ["git", "config", "user.name", "t"], cwd=tmp_path, check=True
        )
        _vault(tmp_path, {"concept/a.md": _page(rel="concept/a.md")})
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        env = {**os.environ, "GIT_AUTHOR_DATE": "2026-07-01T12:00:00",
               "GIT_COMMITTER_DATE": "2026-07-01T12:00:00"}
        subprocess.run(
            ["git", "commit", "-q", "-m", "init"],
            cwd=tmp_path, check=True, env=env,
        )
        idx = SearchIndex(tmp_path)
        (hit,) = idx.search()
        assert hit.git_date == "2026-07-01"

    def test_since_with_git_date(self, tmp_path):
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
        _vault(tmp_path, {"concept/a.md": _page(rel="concept/a.md", source_date="2026-01-01")})
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        env = {**os.environ, "GIT_AUTHOR_DATE": "2026-07-01T12:00:00",
               "GIT_COMMITTER_DATE": "2026-07-01T12:00:00"}
        subprocess.run(["git", "commit", "-q", "-m", "init"],
                       cwd=tmp_path, check=True, env=env)
        idx = SearchIndex(tmp_path)
        # source_date=2026-01-01 wouldn't match --since=2026-06-01
        assert idx.search(since="2026-06-01", date_field="source_date") == []
        # git_date=2026-07-01 does
        assert [h.rel for h in idx.search(since="2026-06-01", date_field="git_date")] == ["concept/a.md"]


# --- CLI -----------------------------------------------------------------


def test_cli_status_and_reindex(tmp_path, capsys, monkeypatch):
    pages = {"concept/a.md": _page(rel="concept/a.md", body="alpha content")}
    _vault(tmp_path, pages)
    monkeypatch.setenv("WIKI_ROOT", str(tmp_path))
    from search import _main
    _main(["--status", "--json"])
    out = capsys.readouterr().out.strip()
    payload = json.loads(out.splitlines()[0])
    assert payload["backend"] in ("fts5", "re")
    assert payload["pages"] == 0  # nothing indexed yet

    # Reindex brings the page in.
    _main(["--reindex", "--json"])
    out = capsys.readouterr().out.strip()
    payload = json.loads(out.splitlines()[0])
    assert payload["pages"] == 1
    assert payload["inserted"] == 1


def test_cli_text_query_default_output(tmp_path, capsys, monkeypatch):
    pages = {"concept/a.md": _page(rel="concept/a.md", body="alpha content")}
    _vault(tmp_path, pages)
    monkeypatch.setenv("WIKI_ROOT", str(tmp_path))
    from search import _main
    _main(["alpha"])
    out = capsys.readouterr().out
    assert "concept/a.md" in out
    # Compact format includes title and volatility.
    assert "alpha content" in out or "Page" in out  # title or summary text


def test_cli_text_query_json(tmp_path, capsys, monkeypatch):
    pages = {"concept/a.md": _page(rel="concept/a.md", body="alpha content")}
    _vault(tmp_path, pages)
    monkeypatch.setenv("WIKI_ROOT", str(tmp_path))
    from search import _main
    _main(["alpha", "--json"])
    out = capsys.readouterr().out.strip()
    record = json.loads(out.splitlines()[0])
    assert record["rel"] == "concept/a.md"
    assert record["title"] == "Page"


def test_cli_tag_filter(tmp_path, capsys, monkeypatch):
    pages = {
        "concept/a.md": _page(rel="concept/a.md", tags=["db"], body="alpha"),
        "concept/b.md": _page(rel="concept/b.md", tags=["http"], body="beta"),
    }
    _vault(tmp_path, pages)
    monkeypatch.setenv("WIKI_ROOT", str(tmp_path))
    from search import _main
    _main(["--tag", "db", "--json"])
    rels = [json.loads(line)["rel"] for line in capsys.readouterr().out.splitlines() if line]
    assert rels == ["concept/a.md"]
