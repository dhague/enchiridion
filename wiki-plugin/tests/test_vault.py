"""TDD for vault.py — vault-root resolution (§1 order) and the CLI.

The ``Vault`` class's own coverage lives in test_wikipage.py, where it was
written before ``Vault`` moved into this module.
"""
import os
import subprocess
import sys

import pytest

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


# --- CLI ------------------------------------------------------------------


@pytest.fixture
def small_vault(tmp_path):
    (tmp_path / "wiki/concept").mkdir(parents=True)
    (tmp_path / "wiki/entity").mkdir(parents=True)
    (tmp_path / "wiki/concept/a.md").write_text(
        "---\ntitle: A\n---\nsee [b](../entity/b.md)\n", encoding="utf-8"
    )
    (tmp_path / "wiki/entity/b.md").write_text("---\ntitle: B\n---\n# B\n", encoding="utf-8")
    return tmp_path


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
        "move", "wiki/entity/b.md", "wiki/concept/b.md",
        env={"WIKI_ROOT": str(small_vault)},
    )
    assert result.returncode == 0, result.stderr
    assert not (small_vault / "wiki/entity/b.md").exists()
    assert (small_vault / "wiki/concept/b.md").exists()
    # The inbound link in a.md follows the move, rebased to the new folder.
    assert "[b](b.md)" in (small_vault / "wiki/concept/a.md").read_text(encoding="utf-8")
    # The moved page is reported on stdout, one vault-relative path per line.
    assert "wiki/concept/b.md" in result.stdout.split()
