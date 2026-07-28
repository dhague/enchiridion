"""TDD for page_record.py — the one module that reads the frontmatter schema.

Lifted out of build_index._page_record (#40) so a page's frontmatter is
decoded into a :class:`PageRecord` exactly once, with every other caller
(``build_index.py`` today; ``Vault.pages()`` in #41; ``Vault.search()`` in
#39) reading the record instead of re-parsing YAML keys itself.
"""
import page_record


def test_basic_fields():
    text = (
        "---\n"
        "title: Prepared statements\n"
        "summary: Avoid re-parsing repeated SQL text.\n"
        "tags: [sql, perf]\n"
        "source_date: 2026-01-05\n"
        "volatility: stable\n"
        "---\n"
        "# Prepared statements\n"
    )
    rec = page_record.page_record("concept/prepared-statements.md", text)
    assert rec.rel == "concept/prepared-statements.md"
    assert rec.title == "Prepared statements"
    assert rec.summary == "Avoid re-parsing repeated SQL text."
    assert rec.tags == ["sql", "perf"]
    assert rec.source_date == "2026-01-05"
    assert rec.volatility == "stable"
    assert rec.edges == []
    assert rec.superseded_by == []


def test_kind_derived_from_folder():
    text = "---\ntitle: A\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n"
    assert page_record.page_record("concept/a.md", text).kind == "concept"
    assert page_record.page_record("entity/b.md", text).kind == "entity"
    assert page_record.page_record("source/c.md", text).kind == "source"
    assert page_record.page_record("synthesis/d.md", text).kind == "synthesis"


def test_edges_resolved_and_rebased_to_wiki_root():
    text = (
        "---\n"
        "title: B\n"
        "summary: s\n"
        "tags: []\n"
        "source_date: 2026-01-01\n"
        "volatility: stable\n"
        "related:\n"
        '  - "[A](../concept/a.md)"\n'
        "---\n"
    )
    rec = page_record.page_record("entity/b.md", text)
    assert rec.edges == [("related", ["concept/a.md"])]


def test_edges_ordered_by_schema_not_authored_order():
    text = (
        "---\n"
        "title: A\n"
        "summary: s\n"
        "tags: []\n"
        "source_date: 2026-01-01\n"
        "volatility: stable\n"
        "related:\n"
        '  - "[B](../entity/b.md)"\n'
        "refines:\n"
        '  - "[C](../concept/c.md)"\n'
        "---\n"
    )
    rec = page_record.page_record("concept/a.md", text)
    assert [key for key, _ in rec.edges] == ["refines", "related"]


def test_superseded_by_derived_across_vault():
    pages = {
        "source/new.md": (
            "---\n"
            "title: New deploy\n"
            "summary: s\n"
            "tags: []\n"
            "source_date: 2026-01-01\n"
            "volatility: stable\n"
            "supersedes:\n"
            '  - "[Old deploy](old.md)"\n'
            "---\n"
        ),
        "source/old.md": (
            "---\ntitle: Old deploy\nsummary: s\ntags: []\n"
            "source_date: 2026-01-01\nvolatility: stable\n---\n"
        ),
    }
    records = page_record.load_records(pages)
    assert records["source/old.md"].superseded_by == ["source/new.md"]
    assert records["source/new.md"].superseded_by == []


def test_superseded_by_empty_when_no_page_supersedes_it():
    pages = {
        "concept/a.md": "---\ntitle: A\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
    }
    records = page_record.load_records(pages)
    assert records["concept/a.md"].superseded_by == []


def test_load_records_skips_index_md():
    pages = {
        "_index.md": "stale content\n",
        "concept/a.md": "---\ntitle: A\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
    }
    records = page_record.load_records(pages)
    assert "_index.md" not in records
    assert "concept/a.md" in records
