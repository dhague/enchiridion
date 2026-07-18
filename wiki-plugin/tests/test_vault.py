"""TDD for vault.py — vault-root resolution (§1 order)."""
from pathlib import Path

import vault


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
