"""TDD for page_record.py — the one module that reads the frontmatter schema.

Decoded once into a :class:`PageRecord` so every other caller
(``Vault.pages()``, ``search_index.upsert_page``) reads the record instead
of re-parsing YAML keys itself. All rels are vault-relative page references
(ADR-0009).
"""
import warnings

import pytest

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
    rec = page_record.page_record("wiki/concepts/prepared-statements.md", text)
    assert rec.page_ref == "wiki/concepts/prepared-statements.md"
    assert rec.title == "Prepared statements"
    assert rec.summary == "Avoid re-parsing repeated SQL text."
    assert rec.tags == ["sql", "perf"]
    assert rec.source_date == "2026-01-05"
    assert rec.volatility == "stable"
    assert rec.edges == []
    assert rec.superseded_by == []


def test_kind_derived_from_folder():
    text = "---\ntitle: A\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n"
    assert page_record.page_record("wiki/concepts/a.md", text).kind == "concept"
    assert page_record.page_record("wiki/entities/b.md", text).kind == "entity"
    assert page_record.page_record("wiki/sources/c.md", text).kind == "source"
    assert page_record.page_record("wiki/synthesis/d.md", text).kind == "synthesis"


def test_kind_derived_from_custom_folder():
    """A page in a custom kind-folder gets its kind via ADR-0008 singularization."""
    text = "---\ntitle: A\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n"
    assert page_record.page_record("wiki/decisions/d.md", text).kind == "decision"
    assert page_record.page_record("wiki/meetings/m.md", text).kind == "meeting"
    assert page_record.page_record("wiki/people/p.md", text).kind == "people"


def test_kind_accepts_old_singular_folder():
    """An unmigrated singular folder (wiki/concept/) is now accepted: folder_to_kind
    returns 'concept' verbatim (no trailing s), same kind as wiki/concepts/."""
    text = "---\ntitle: A\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n"
    rec = page_record.page_record("wiki/concept/a.md", text)
    assert rec.kind == "concept"


def test_kind_rejects_a_page_not_under_a_kind_folder():
    """The kind-folder is derived from the page's directory (ADR-0009's
    vault-relative spelling): a page at the wiki root or in a nested subfolder
    is outside the schema and is a hard error, not a silently-guessed kind."""
    text = "---\ntitle: A\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n"
    with pytest.raises(ValueError):
        page_record.page_record("wiki/foo.md", text)
    with pytest.raises(ValueError):
        page_record.page_record("wiki/concepts/nested/deep.md", text)


def test_edges_resolved_to_vault_relative():
    text = (
        "---\n"
        "title: B\n"
        "summary: s\n"
        "tags: []\n"
        "source_date: 2026-01-01\n"
        "volatility: stable\n"
        "related:\n"
        '  - "[A](../concepts/a.md)"\n'
        "---\n"
    )
    rec = page_record.page_record("wiki/entities/b.md", text)
    assert rec.edges == [("related", ["wiki/concepts/a.md"])]


def test_edges_ordered_by_schema_not_authored_order():
    text = (
        "---\n"
        "title: A\n"
        "summary: s\n"
        "tags: []\n"
        "source_date: 2026-01-01\n"
        "volatility: stable\n"
        "related:\n"
        '  - "[B](../entities/b.md)"\n'
        "refines:\n"
        '  - "[C](../concepts/c.md)"\n'
        "---\n"
    )
    rec = page_record.page_record("wiki/concepts/a.md", text)
    assert [key for key, _ in rec.edges] == ["refines", "related"]


def test_superseded_by_derived_across_vault():
    pages = {
        "wiki/sources/new.md": (
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
        "wiki/sources/old.md": (
            "---\ntitle: Old deploy\nsummary: s\ntags: []\n"
            "source_date: 2026-01-01\nvolatility: stable\n---\n"
        ),
    }
    records = page_record.load_records(pages)
    assert records["wiki/sources/old.md"].superseded_by == ["wiki/sources/new.md"]
    assert records["wiki/sources/new.md"].superseded_by == []


def test_superseded_by_empty_when_no_page_supersedes_it():
    pages = {
        "wiki/concepts/a.md": "---\ntitle: A\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
    }
    records = page_record.load_records(pages)
    assert records["wiki/concepts/a.md"].superseded_by == []


def test_load_records_includes_custom_folder_pages():
    """Pages in custom kind-folders are fully included in the result."""
    pages = {
        "wiki/concepts/a.md": "---\ntitle: A\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
        "wiki/decisions/my-decision.md": "---\ntitle: My Decision\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
    }
    records = page_record.load_records(pages)
    assert "wiki/concepts/a.md" in records
    assert "wiki/decisions/my-decision.md" in records
    assert records["wiki/decisions/my-decision.md"].kind == "decision"


def test_load_records_includes_multiple_custom_folders():
    """Pages across multiple custom folders all appear in the result."""
    pages = {
        "wiki/concepts/a.md": "---\ntitle: A\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
        "wiki/decisions/d.md": "---\ntitle: D\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
        "wiki/meetings/m.md": "---\ntitle: M\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
    }
    records = page_record.load_records(pages)
    assert set(records) == {"wiki/concepts/a.md", "wiki/decisions/d.md", "wiki/meetings/m.md"}


def test_load_records_no_warning_for_custom_folders():
    """Custom kind-folder pages are no longer a warning condition."""
    import warnings
    pages = {
        "wiki/decisions/my-decision.md": "---\ntitle: My Decision\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        page_record.load_records(pages)
    assert len(caught) == 0
