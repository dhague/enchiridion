"""TDD for ``vault_git.py`` — the one module for git facts about the vault
(#126).

Every ``shutil.which("git")`` probe and ``subprocess.run(["git", ...])`` in
the plugin lives here, so the absent-git policy is pinned here: the strict
surface (:meth:`VaultGit.run` / ``ensure_work_tree`` / ``init`` / ``add`` /
``commit``) raises :class:`GitError`; the lenient surface
(``is_work_tree`` / ``last_commit_date`` / ``porcelain_mentions`` /
``commit_dates``) returns the documented default. These tests use real git
work trees — the *fake* stands in for this module in the callers' tests, so
what's pinned here is the real behaviour against real git.
"""
import os
import subprocess

import pytest

from vault_git import GitError, VaultGit


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "commit.gpgsign", "false"], check=True)
    return VaultGit(tmp_path)


def _git(git: VaultGit, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(git.root), *args], check=True, capture_output=True, text=True
    ).stdout


# --- availability ---------------------------------------------------------


class TestAvailable:
    def test_true_when_git_on_path(self, git_repo):
        assert git_repo.available() is True

    def test_false_when_git_absent(self, git_repo, monkeypatch):
        monkeypatch.setattr("vault_git.shutil.which", lambda _name: None)
        assert git_repo.available() is False


# --- strict surface -------------------------------------------------------


class TestStrictSurface:
    def test_run_returns_stdout(self, git_repo):
        assert git_repo.run("rev-parse", "--is-inside-work-tree").strip() == "true"

    def test_run_raises_on_absent_git(self, git_repo, monkeypatch):
        monkeypatch.setattr("vault_git.shutil.which", lambda _name: None)
        with pytest.raises(GitError, match="not found on PATH"):
            git_repo.run("rev-parse", "--is-inside-work-tree")

    def test_run_raises_on_non_zero_exit(self, git_repo):
        with pytest.raises(GitError, match="failed"):
            git_repo.run("log", "--nonexistent-flag")

    def test_ensure_work_tree_passes_in_a_repo(self, git_repo):
        git_repo.ensure_work_tree()  # no raise

    def test_ensure_work_tree_raises_when_not_a_work_tree(self, tmp_path):
        git = VaultGit(tmp_path)
        with pytest.raises(GitError, match="not a git work tree"):
            git.ensure_work_tree()

    def test_ensure_work_tree_raises_when_git_absent(self, git_repo, monkeypatch):
        monkeypatch.setattr("vault_git.shutil.which", lambda _name: None)
        with pytest.raises(GitError, match="not found on PATH"):
            git_repo.ensure_work_tree()

    def test_init_makes_a_repo(self, tmp_path):
        git = VaultGit(tmp_path)
        git.init()
        assert git.is_work_tree()

    def test_add_and_commit_return_sha(self, git_repo):
        (git_repo.root / "a.md").write_text("# A\n", encoding="utf-8")
        git_repo.add("a.md")
        sha = git_repo.commit("seed")
        assert sha
        assert _git(git_repo, "log", "-1", "--pretty=%B").strip() == "seed"
        assert _git(git_repo, "status", "--porcelain") == ""


# --- lenient surface ------------------------------------------------------


class TestLenientSurface:
    def test_is_work_tree_true_in_a_repo(self, git_repo):
        assert git_repo.is_work_tree() is True

    def test_is_work_tree_false_outside_a_repo(self, tmp_path):
        assert VaultGit(tmp_path).is_work_tree() is False

    def test_is_work_tree_false_when_git_absent(self, git_repo, monkeypatch):
        monkeypatch.setattr("vault_git.shutil.which", lambda _name: None)
        assert git_repo.is_work_tree() is False

    def test_last_commit_date_is_none_for_never_committed(self, git_repo):
        (git_repo.root / "a.md").write_text("# A\n", encoding="utf-8")
        assert git_repo.last_commit_date("a.md") is None

    def test_last_commit_date_after_commit(self, git_repo):
        (git_repo.root / "a.md").write_text("# A\n", encoding="utf-8")
        git_repo.add("a.md")
        env = {**os.environ, "GIT_AUTHOR_DATE": "2026-03-01T12:00:00",
               "GIT_COMMITTER_DATE": "2026-03-01T12:00:00"}
        subprocess.run(
            ["git", "-C", str(git_repo.root), "commit", "-q", "-m", "seed"],
            check=True, env=env,
        )
        assert git_repo.last_commit_date("a.md") == "2026-03-01"

    def test_last_commit_date_none_when_git_absent(self, git_repo, monkeypatch):
        monkeypatch.setattr("vault_git.shutil.which", lambda _name: None)
        assert git_repo.last_commit_date("a.md") is None

    def test_porcelain_mentions_clean_file(self, git_repo):
        (git_repo.root / "a.md").write_text("# A\n", encoding="utf-8")
        git_repo.add("a.md")
        git_repo.commit("seed")
        assert git_repo.porcelain_mentions("a.md") is False

    def test_porcelain_mentions_dirty_file(self, git_repo):
        (git_repo.root / "a.md").write_text("# A\n", encoding="utf-8")
        git_repo.add("a.md")
        git_repo.commit("seed")
        (git_repo.root / "a.md").write_text("# A changed\n", encoding="utf-8")
        assert git_repo.porcelain_mentions("a.md") is True

    def test_porcelain_mentions_untracked_file(self, git_repo):
        (git_repo.root / "new.md").write_text("# New\n", encoding="utf-8")
        assert git_repo.porcelain_mentions("new.md") is True

    def test_porcelain_mentions_false_when_git_absent(self, git_repo, monkeypatch):
        monkeypatch.setattr("vault_git.shutil.which", lambda _name: None)
        assert git_repo.porcelain_mentions("a.md") is False

    def test_commit_dates_empty_without_a_repo(self, tmp_path):
        assert VaultGit(tmp_path).commit_dates() == {}

    def test_commit_dates_empty_when_git_absent(self, git_repo, monkeypatch):
        monkeypatch.setattr("vault_git.shutil.which", lambda _name: None)
        assert git_repo.commit_dates() == {}

    def test_commit_dates_one_pass_per_file(self, git_repo):
        for rel in ("wiki/concepts/a.md", "wiki/concepts/b.md"):
            p = git_repo.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# A\n", encoding="utf-8")
        env = {**os.environ, "GIT_AUTHOR_DATE": "2026-03-01T12:00:00",
               "GIT_COMMITTER_DATE": "2026-03-01T12:00:00"}
        subprocess.run(
            ["git", "-C", str(git_repo.root), "add", "-A"], check=True,
        )
        subprocess.run(
            ["git", "-C", str(git_repo.root), "commit", "-q", "-m", "seed"],
            check=True, env=env,
        )
        dates = git_repo.commit_dates()
        assert dates["wiki/concepts/a.md"] == "2026-03-01"
        assert dates["wiki/concepts/b.md"] == "2026-03-01"

    def test_commit_dates_skips_non_wiki_files(self, git_repo):
        (git_repo.root / "raw" / "notes.md").parent.mkdir(parents=True, exist_ok=True)
        (git_repo.root / "raw" / "notes.md").write_text("raw\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(git_repo.root), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(git_repo.root), "commit", "-q", "-m", "seed"],
            check=True,
        )
        assert git_repo.commit_dates() == {}
