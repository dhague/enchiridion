"""TDD for the one-off #114 migration: singular kind-folders -> plural."""
import pytest

import migrate_kind_folders_0114 as migrate_mod


def _write(path, text="---\ntitle: X\n---\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_plan_moves_every_page_out_of_old_singular_folders(tmp_path):
    _write(tmp_path / "wiki/concept/a.md")
    _write(tmp_path / "wiki/entity/b.md")
    _write(tmp_path / "wiki/source/c.md")

    moves = migrate_mod.plan(tmp_path)

    assert sorted(moves) == [
        ("wiki/concept/a.md", "wiki/concepts/a.md"),
        ("wiki/entity/b.md", "wiki/entities/b.md"),
        ("wiki/source/c.md", "wiki/sources/c.md"),
    ]


def test_plan_skips_a_kind_with_no_old_folder(tmp_path):
    _write(tmp_path / "wiki/concepts/a.md")

    assert migrate_mod.plan(tmp_path) == []


def test_plan_leaves_synthesis_untouched(tmp_path):
    _write(tmp_path / "wiki/synthesis/a.md")

    assert migrate_mod.plan(tmp_path) == []


def test_plan_raises_on_same_slug_collision(tmp_path):
    _write(tmp_path / "wiki/concept/a.md")
    _write(tmp_path / "wiki/concepts/a.md")

    with pytest.raises(migrate_mod.MigrationError, match="wiki/concept/a.md"):
        migrate_mod.plan(tmp_path)


def test_migrate_moves_files_and_removes_drained_old_folder(tmp_path):
    _write(tmp_path / "wiki/concept/a.md", "---\ntitle: A\n---\nsee [b](../entity/b.md)\n")
    _write(tmp_path / "wiki/entity/b.md", "---\ntitle: B\n---\n")

    moved = migrate_mod.migrate(tmp_path)

    assert sorted(moved) == [
        ("wiki/concept/a.md", "wiki/concepts/a.md"),
        ("wiki/entity/b.md", "wiki/entities/b.md"),
    ]
    assert (tmp_path / "wiki/concepts/a.md").exists()
    assert (tmp_path / "wiki/entities/b.md").exists()
    assert not (tmp_path / "wiki/concept").exists()
    assert not (tmp_path / "wiki/entity").exists()
    # move_page fixed the inbound link across the rename too.
    assert "../entities/b.md" in (tmp_path / "wiki/concepts/a.md").read_text()


def test_migrate_merges_into_an_existing_plural_folder(tmp_path):
    _write(tmp_path / "wiki/concept/a.md")
    _write(tmp_path / "wiki/concepts/existing.md")

    moved = migrate_mod.migrate(tmp_path)

    assert moved == [("wiki/concept/a.md", "wiki/concepts/a.md")]
    assert (tmp_path / "wiki/concepts/a.md").exists()
    assert (tmp_path / "wiki/concepts/existing.md").exists()
    assert not (tmp_path / "wiki/concept").exists()


def test_migrate_removes_a_lone_gitkeep_before_dropping_the_old_folder(tmp_path):
    _write(tmp_path / "wiki/concept/a.md")
    (tmp_path / "wiki/concept/.gitkeep").touch()

    migrate_mod.migrate(tmp_path)

    assert not (tmp_path / "wiki/concept").exists()


def test_migrate_dry_run_moves_nothing(tmp_path):
    _write(tmp_path / "wiki/concept/a.md")

    moved = migrate_mod.migrate(tmp_path, dry_run=True)

    assert moved == []
    assert (tmp_path / "wiki/concept/a.md").exists()
    assert not (tmp_path / "wiki/concepts").exists()
