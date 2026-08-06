"""TDD for build_index.py — regenerate wiki/_index.md from page frontmatter.

Per the amended frontmatter contract (19be866, reconciled in #9), typed edges,
``supersedes`` and ``raw_source`` are quoted markdown links, not a flat
``links:`` list — so the index builder must strip the markdown (reusing
``lib.md.iter_links``, which already parses these) to recover each target's
path, then re-base it from the *page's* directory to be relative to ``wiki/``
(the directory ``_index.md`` itself lives in). ``_index.md`` indexes
``wiki/**`` only — never ``raw/`` — though a ``sources/`` page's ``raw_source``
pointer still rides along as data on that page's own row.
"""
import build_index


def test_single_page_basic_fields():
    pages = {
        "concepts/prepared-statements.md": (
            "---\n"
            "title: Prepared statements\n"
            "summary: Avoid re-parsing repeated SQL text.\n"
            "tags: [sql, perf]\n"
            "source_date: 2026-01-05\n"
            "volatility: stable\n"
            "---\n"
            "# Prepared statements\n"
        ),
    }
    out = build_index.build_index(pages)
    assert (
        "| [concepts/prepared-statements.md](concepts/prepared-statements.md) "
        "| Prepared statements | Avoid re-parsing repeated SQL text. "
        "| [sql, perf] | 2026-01-05 | stable |  |"
    ) in out


def test_rows_sorted_by_path():
    pages = {
        "entities/b.md": "---\ntitle: B\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
        "concepts/a.md": "---\ntitle: A\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
    }
    out = build_index.build_index(pages)
    a_pos = out.index("concepts/a.md")
    b_pos = out.index("entities/b.md")
    assert a_pos < b_pos


def test_index_md_itself_excluded():
    pages = {
        "_index.md": "stale content\n",
        "concepts/a.md": "---\ntitle: A\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
    }
    out = build_index.build_index(pages)
    assert "stale content" not in out
    assert out.count("\n| [") == 1


def test_typed_edge_rebased_relative_to_wiki_root():
    # From entities/b.md, "related" points at "../concepts/a.md" (relative to
    # entities/). Relative to wiki/ (the index's own dir) that's "concepts/a.md".
    pages = {
        "entities/b.md": (
            "---\n"
            "title: B\n"
            "summary: s\n"
            "tags: []\n"
            "source_date: 2026-01-01\n"
            "volatility: stable\n"
            "related:\n"
            '  - "[A](../concepts/a.md)"\n'
            "---\n"
        ),
        "concepts/a.md": "---\ntitle: A\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
    }
    out = build_index.build_index(pages)
    row = [line for line in out.splitlines() if "entities/b.md" in line][0]
    assert "related:concepts/a.md" in row


def test_multiple_targets_under_one_edge_key():
    pages = {
        "synthesis/s.md": (
            "---\n"
            "title: S\n"
            "summary: s\n"
            "tags: []\n"
            "source_date: 2026-01-01\n"
            "volatility: stable\n"
            "source:\n"
            '  - "[A](../concepts/a.md)"\n'
            '  - "[B](../concepts/b.md)"\n'
            "---\n"
        ),
        "concepts/a.md": "---\ntitle: A\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
        "concepts/b.md": "---\ntitle: B\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
    }
    out = build_index.build_index(pages)
    row = [line for line in out.splitlines() if "synthesis/s.md" in line][0]
    assert "source:concepts/a.md,concepts/b.md" in row


def test_supersedes_rendered():
    pages = {
        "sources/new.md": (
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
        "sources/old.md": "---\ntitle: Old deploy\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
    }
    out = build_index.build_index(pages)
    row = [line for line in out.splitlines() if "sources/new.md" in line][0]
    assert "supersedes:sources/old.md" in row


def test_raw_source_rebased_across_wiki_boundary():
    # From wiki/sources/x.md, raw_source is "../../raw/notes/x.md" (relative to
    # sources/). Relative to wiki/ that's "../raw/notes/x.md".
    pages = {
        "sources/x.md": (
            "---\n"
            "title: X source\n"
            "summary: s\n"
            "tags: []\n"
            "source_date: 2026-01-01\n"
            "volatility: stable\n"
            'raw_source: "[x.md](../../raw/notes/x.md)"\n'
            "---\n"
        ),
    }
    out = build_index.build_index(pages)
    row = [line for line in out.splitlines() if "sources/x.md" in line][0]
    assert "raw_source:../raw/notes/x.md" in row


def test_edges_ordered_and_joined_with_semicolon():
    pages = {
        "concepts/a.md": (
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
        ),
        "entities/b.md": "---\ntitle: B\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
        "concepts/c.md": "---\ntitle: C\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
    }
    out = build_index.build_index(pages)
    row = [line for line in out.splitlines() if line.startswith("| [concepts/a.md]")][0]
    # Schema field order (refines before related), regardless of authored order.
    assert "refines:concepts/c.md; related:entities/b.md" in row


def test_no_edges_renders_empty_cell():
    pages = {
        "concepts/a.md": "---\ntitle: A\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
    }
    out = build_index.build_index(pages)
    row = [line for line in out.splitlines() if "concepts/a.md" in line][0]
    assert row.endswith("|  |")


def test_pipe_in_summary_is_escaped():
    pages = {
        "concepts/a.md": (
            "---\ntitle: A\nsummary: has a | pipe\ntags: []\n"
            "source_date: 2026-01-01\nvolatility: stable\n---\n"
        ),
    }
    out = build_index.build_index(pages)
    assert "has a \\| pipe" in out


def test_empty_vault_renders_header_only():
    out = build_index.build_index({})
    assert "Path" in out
    assert "\n| [" not in out


def test_deterministic_idempotent():
    pages = {
        "concepts/a.md": "---\ntitle: A\nsummary: s\ntags: [x]\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
        "entities/b.md": "---\ntitle: B\nsummary: s\ntags: [y]\nsource_date: 2026-01-01\nvolatility: evolving\n---\n",
    }
    assert build_index.build_index(pages) == build_index.build_index(pages)


# --- disk integration ---------------------------------------------------

def test_write_index_walks_wiki_only_and_writes_file(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / "a.md").write_text(
        "---\ntitle: A\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
        encoding="utf-8",
    )
    raw = tmp_path / "raw" / "notes"
    raw.mkdir(parents=True)
    (raw / "x.md").write_text("raw content\n", encoding="utf-8")

    index_path = build_index.write_index(tmp_path)

    assert index_path == wiki / "_index.md"
    content = index_path.read_text(encoding="utf-8")
    assert "concepts/a.md" in content
    assert "raw content" not in content
    assert "notes/x.md" not in content


def test_write_index_regenerates_stale_index(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir(parents=True)
    (wiki / "_index.md").write_text("stale\n", encoding="utf-8")
    (wiki / "concepts").mkdir()
    (wiki / "concepts" / "a.md").write_text(
        "---\ntitle: A\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
        encoding="utf-8",
    )

    build_index.write_index(tmp_path)

    content = (wiki / "_index.md").read_text(encoding="utf-8")
    assert "stale" not in content
    assert "concepts/a.md" in content
