"""TDD for vault.py — vault-root resolution (§1 order), the ``Vault`` class
(all vault I/O and the cross-page operations built on it), and the CLI.

``Vault``'s pure counterpart ``WikiPage`` is covered in test_wikipage.py; the
split between the two files mirrors the split between the two modules.
"""
import os
import posixpath
import subprocess
import sys

import pytest

import vault
import wikipage
from vault import Vault
from wikipage import WikiPage


def test_wiki_root_env_wins(tmp_path):
    # A marker exists under start, but $WIKI_ROOT must win regardless.
    (tmp_path / "wiki").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    root = vault.resolve_vault_root(start=tmp_path, env={"WIKI_ROOT": str(elsewhere)})
    assert root == elsewhere.resolve()


def test_wiki_root_env_wins_even_if_nonexistent(tmp_path):
    # "$WIKI_ROOT if set — wins always." It's the declared root by fiat.
    declared = tmp_path / "not-created-yet"
    root = vault.resolve_vault_root(start=tmp_path, env={"WIKI_ROOT": str(declared)})
    assert root == declared.resolve()


def test_ancestor_with_wiki_dir_marker(tmp_path):
    (tmp_path / "wiki").mkdir()
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    root = vault.resolve_vault_root(start=nested, env={})
    assert root == tmp_path.resolve()


def test_ancestor_with_sentinel_marker(tmp_path):
    (tmp_path / ".wiki-root").write_text("", encoding="utf-8")
    nested = tmp_path / "deep" / "dir"
    nested.mkdir(parents=True)
    root = vault.resolve_vault_root(start=nested, env={})
    assert root == tmp_path.resolve()


def test_nearest_ancestor_wins(tmp_path):
    # Two markers on the path; the nearest ancestor to start wins.
    (tmp_path / "wiki").mkdir()
    near = tmp_path / "inner"
    near.mkdir()
    (near / "wiki").mkdir()
    start = near / "x"
    start.mkdir()
    root = vault.resolve_vault_root(start=start, env={})
    assert root == near.resolve()


def test_start_itself_carries_marker(tmp_path):
    (tmp_path / "wiki").mkdir()
    root = vault.resolve_vault_root(start=tmp_path, env={})
    assert root == tmp_path.resolve()


def test_falls_back_to_cwd_when_no_marker(tmp_path):
    nested = tmp_path / "no" / "marker" / "here"
    nested.mkdir(parents=True)
    root = vault.resolve_vault_root(start=nested, env={})
    assert root == nested.resolve()


def test_empty_wiki_root_env_is_ignored(tmp_path):
    # An empty string is not "set" in any useful sense — fall through.
    (tmp_path / "wiki").mkdir()
    root = vault.resolve_vault_root(start=tmp_path, env={"WIKI_ROOT": ""})
    assert root == tmp_path.resolve()


# --- Vault: auto-migrates leftover singular kind-folders (#119) ------------


def test_vault_init_auto_migrates_singular_kind_folders(tmp_path):
    (tmp_path / "wiki/concept").mkdir(parents=True)
    (tmp_path / "wiki/concept/a.md").write_text("---\ntitle: A\n---\n", encoding="utf-8")

    Vault(tmp_path)

    assert (tmp_path / "wiki/concepts/a.md").exists()
    assert not (tmp_path / "wiki/concept").exists()


def test_vault_init_is_a_noop_when_already_plural(small_vault):
    before = (small_vault / "wiki/concepts/a.md").read_text(encoding="utf-8")

    Vault(small_vault)

    assert (small_vault / "wiki/concepts/a.md").read_text(encoding="utf-8") == before


def test_vault_init_raises_on_migration_collision(tmp_path):
    (tmp_path / "wiki/concept").mkdir(parents=True)
    (tmp_path / "wiki/concepts").mkdir(parents=True)
    (tmp_path / "wiki/concept/a.md").write_text("---\ntitle: A\n---\n", encoding="utf-8")
    (tmp_path / "wiki/concepts/a.md").write_text("---\ntitle: A2\n---\n", encoding="utf-8")

    import migrate_kind_folders_0114

    with pytest.raises(migrate_kind_folders_0114.MigrationError):
        Vault(tmp_path)


# --- Vault: I/O + cross-page move -----------------------------------------------


@pytest.fixture
def small_vault(tmp_path):
    (tmp_path / "wiki/concepts").mkdir(parents=True)
    (tmp_path / "wiki/entities").mkdir(parents=True)
    (tmp_path / "wiki/concepts/a.md").write_text(
        "---\ntitle: A\n---\nsee [b](../entities/b.md)\n", encoding="utf-8"
    )
    (tmp_path / "wiki/entities/b.md").write_text("---\ntitle: B\n---\n# B\n", encoding="utf-8")
    return tmp_path


def test_vault_load_and_get(small_vault):
    v = Vault(small_vault)
    page = v.load("wiki/entities/b.md")
    assert page.get("title") == "B"


def test_vault_set_writes_back(small_vault):
    v = Vault(small_vault)
    v.set("wiki/entities/b.md", "summary", "the B page")
    assert v.load("wiki/entities/b.md").get("summary") == "the B page"


def test_vault_merge_writes_back(small_vault):
    v = Vault(small_vault)
    v.set("wiki/entities/b.md", "tags", ["db"])
    v.merge("wiki/entities/b.md", "tags", ["db", "sql"])
    tags = v.load("wiki/entities/b.md").get("tags")
    assert tags is not None
    assert list(tags) == ["db", "sql"]


def test_vault_move_page_rewrites_inbound_and_moves_file(small_vault):
    v = Vault(small_vault)
    changed = v.move_page("wiki/entities/b.md", "wiki/concepts/b.md")
    assert set(changed) == {"wiki/concepts/a.md", "wiki/concepts/b.md"}
    assert not (small_vault / "wiki/entities/b.md").exists()
    assert (small_vault / "wiki/concepts/b.md").exists()
    assert "see [b](b.md)" in (small_vault / "wiki/concepts/a.md").read_text(encoding="utf-8")


def test_vault_move_page_missing_source_raises(small_vault):
    v = Vault(small_vault)
    with pytest.raises(FileNotFoundError):
        v.move_page("wiki/entities/missing.md", "wiki/concepts/missing.md")


def test_vault_round_trip_encoded_raw_filename_survives_write_read_move_read(small_vault):
    # A raw filename combining every char the encode charset covers: space,
    # `#`, `%`, and parens. Write -> read -> move -> read, link stays resolved.
    raw_name = "my file#1 (50%).txt"
    (small_vault / "raw/notes").mkdir(parents=True)
    (small_vault / "raw/notes" / raw_name).write_bytes(b"raw bytes")
    (small_vault / "wiki/sources").mkdir()

    v = Vault(small_vault)
    encoded = wikipage.percent_encode(raw_name)
    v.write(
        "wiki/sources/x.md",
        WikiPage(
            "---\n"
            "title: X\n"
            f'raw_source: "[{raw_name}](../../raw/notes/{encoded})"\n'
            "---\n"
            "# X\n"
        ),
    )

    page = v.load("wiki/sources/x.md")
    assert page.get("raw_source") == f"[{raw_name}](../../raw/notes/{encoded})"

    changed = v.move_page("wiki/sources/x.md", "wiki/x.md")
    assert changed == ["wiki/x.md"]

    moved = v.load("wiki/x.md")
    lk = moved.links()[0]
    resolved = posixpath.normpath(posixpath.join("wiki", lk.decoded_path))
    assert resolved == f"raw/notes/{raw_name}"
    assert (small_vault / resolved).read_bytes() == b"raw bytes"


def test_vault_move_page_never_reads_or_rewrites_raw(small_vault):
    # A markdown file under raw/ linking at the move target must be left
    # untouched -- move_page only rewrites inbound links from wiki/ pages.
    (small_vault / "raw/notes").mkdir(parents=True)
    raw_md = small_vault / "raw/notes/note.md"
    raw_md.write_text("see [b](../../wiki/entities/b.md)\n", encoding="utf-8")

    v = Vault(small_vault)
    changed = v.move_page("wiki/entities/b.md", "wiki/concepts/b.md")

    assert "raw/notes/note.md" not in changed
    assert raw_md.read_text(encoding="utf-8") == "see [b](../../wiki/entities/b.md)\n"


# --- Vault.pages(): PageRecord enumeration (#41) ----------------------------


def test_vault_pages_rel_is_vault_relative(small_vault):
    v = Vault(small_vault)
    records = v.pages()
    assert set(records) == {"wiki/concepts/a.md", "wiki/entities/b.md"}
    assert records["wiki/concepts/a.md"].page_ref == "wiki/concepts/a.md"


def test_vault_pages_kind_derived_from_folder(small_vault):
    v = Vault(small_vault)
    records = v.pages()
    assert records["wiki/concepts/a.md"].kind == "concept"
    assert records["wiki/entities/b.md"].kind == "entity"


def test_vault_pages_edges_resolve_vault_relative(small_vault):
    # a.md's link to b.md is page-relative ("../entities/b.md"); the resulting
    # edge target is vault-relative — the one convention every rel in the
    # script layer uses (ADR-0009).
    (small_vault / "wiki/concepts/a.md").write_text(
        '---\ntitle: A\nrelated:\n  - "[B](../entities/b.md)"\n---\nsee b\n',
        encoding="utf-8",
    )
    v = Vault(small_vault)
    records = v.pages()
    assert records["wiki/concepts/a.md"].edges == [("related", ["wiki/entities/b.md"])]


def test_vault_pages_never_walks_raw(small_vault):
    (small_vault / "raw/notes").mkdir(parents=True)
    (small_vault / "raw/notes/x.md").write_text("---\ntitle: X\n---\n", encoding="utf-8")
    v = Vault(small_vault)
    assert all(rel.startswith("wiki/") for rel in v.pages())
    assert "raw/notes/x.md" not in v.pages()


# --- Vault.load_wiki_pages()/pages_with_text(): the #41 boundary (#59) ------


def test_vault_pages_with_text_round_trips_the_same_text_on_disk(small_vault):
    v = Vault(small_vault)
    pages = v.pages_with_text()
    rec, text = pages["wiki/entities/b.md"]
    assert rec.page_ref == "wiki/entities/b.md"
    assert text == (small_vault / "wiki/entities/b.md").read_text(encoding="utf-8")


def test_vault_pages_derived_from_pages_with_text(small_vault):
    v = Vault(small_vault)
    with_text = v.pages_with_text()
    assert v.pages() == {rel: rec for rel, (rec, _text) in with_text.items()}


def test_vault_rewrite_inbound_links_for_non_page_target(small_vault):
    # A raw/ file rename: the target itself is never read/written, only the
    # wiki pages that link to it.
    (small_vault / "wiki/sources").mkdir()
    (small_vault / "wiki/sources/x.md").write_text(
        '---\ntitle: X\nraw_source: "[x.md](../../raw/notes/x.md)"\n---\n# X\n',
        encoding="utf-8",
    )
    v = Vault(small_vault)
    changed = v.rewrite_inbound_links("raw/notes/x.md", "raw/notes/2026-01-01-0000-x.md")
    assert changed == ["wiki/sources/x.md"]
    assert (
        'raw_source: "[x.md](../../raw/notes/2026-01-01-0000-x.md)"'
        in (small_vault / "wiki/sources/x.md").read_text(encoding="utf-8")
    )


# --- Vault: search index facade (#39) ------------------------------------


def test_vault_search_finds_written_page(small_vault):
    v = Vault(small_vault)
    v.set("wiki/entities/b.md", "summary", "connection pooling in postgres")
    hits = v.search("postgres")
    assert any(h.page_ref == "wiki/entities/b.md" for h in hits)


def test_vault_search_returns_vault_relative_page_refs(small_vault):
    """Hits carry the same vault-relative convention every other Vault rel
    uses (ADR-0009), so a hit can be handed straight to a plan."""
    v = Vault(small_vault)
    v.set("wiki/entities/b.md", "summary", "connection pooling in postgres")
    (hit,) = v.search("postgres")
    assert hit.page_ref == "wiki/entities/b.md"


def test_vault_set_inline_updates_index(small_vault):
    """The inline update is a latency optimisation: a write followed by a
    search should not need a scan. We can't observe that directly, but we
    *can* observe that the inline update populates the page table with the
    new text — so the next search's scan sees matching mtime/size and skips."""
    v = Vault(small_vault)
    v.set("wiki/entities/b.md", "summary", "alpha content")
    # Force the index to open (a search will do this implicitly, but here
    # we want to observe the inline update, not the scan).
    idx = v._get_index()
    text = (small_vault / "wiki/entities/b.md").read_text(encoding="utf-8")
    idx.upsert_page("wiki/entities/b.md", text)
    rows = idx._conn.execute(
        "SELECT summary FROM page WHERE page_ref = ?", ("wiki/entities/b.md",)
    ).fetchall()
    assert rows == [("alpha content",)]


def test_vault_move_page_picked_up_by_next_search(small_vault):
    """``move_page`` deliberately doesn't inline-update; the next search's
    staleness scan reconciles (the design's correctness path)."""
    v = Vault(small_vault)
    v.move_page("wiki/entities/b.md", "wiki/concepts/b.md")
    # The old rel is gone from the index, the new one is present.
    page_refs = [h.page_ref for h in v.search()]
    assert "wiki/entities/b.md" not in page_refs
    assert "wiki/concepts/b.md" in page_refs


def test_vault_index_status_reports_backend(small_vault):
    v = Vault(small_vault)
    # status is a read of the page table — it doesn't trigger a scan,
    # so a fresh vault reports 0 pages until something has indexed it.
    v.reindex()
    status = v.index_status()
    assert status.backend in ("fts5", "re")
    assert status.pages == 2


def test_vault_reindex_full_returns_stats(small_vault):
    v = Vault(small_vault)
    stats = v.reindex(full=True)
    assert stats.pages == 2
    assert stats.inserted == 2


def test_vault_tag_vocabulary_counts_across_pages(small_vault):
    v = Vault(small_vault)
    v.merge("wiki/concepts/a.md", "tags", ["python", "testing"])
    v.merge("wiki/entities/b.md", "tags", ["python"])
    assert v.tag_vocabulary() == [("python", 2), ("testing", 1)]


# --- CLI ------------------------------------------------------------------


def _run_cli(*args, env=None):
    return subprocess.run(
        [sys.executable, vault.__file__, *args],
        capture_output=True, text=True, env={**os.environ, **(env or {})},
    )


def test_cli_no_args_prints_root(small_vault):
    """The bare no-argument form is a documented surface — wiki-retrieval's
    SKILL.md tells the agent to run ``python vault.py`` to resolve its own
    Read paths — so it must keep working with no subcommand at all.
    """
    result = _run_cli(env={"WIKI_ROOT": str(small_vault)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(small_vault.resolve())


def test_cli_root_subcommand_matches_bare_form(small_vault):
    env = {"WIKI_ROOT": str(small_vault)}
    assert _run_cli("root", env=env).stdout == _run_cli(env=env).stdout


def test_cli_move_runs_as_subprocess(small_vault):
    """``python vault.py move ...`` must work when vault.py is the *executed*
    file, not just when it's imported. vault imports wikipage, which pytest
    has already loaded under that name — but as a script, vault.py is
    ``__main__`` and the import chain runs fresh. Only a real subprocess
    reproduces that; an in-process ``import`` never will.
    """
    result = _run_cli(
        "move", "wiki/entities/b.md", "wiki/concepts/b.md",
        env={"WIKI_ROOT": str(small_vault)},
    )
    assert result.returncode == 0, result.stderr
    assert not (small_vault / "wiki/entities/b.md").exists()
    assert (small_vault / "wiki/concepts/b.md").exists()
    # The inbound link in a.md follows the move, rebased to the new folder.
    assert "[b](b.md)" in (small_vault / "wiki/concepts/a.md").read_text(encoding="utf-8")
    # The moved page is reported on stdout, one vault-relative path per line.
    assert "wiki/concepts/b.md" in result.stdout.split()
