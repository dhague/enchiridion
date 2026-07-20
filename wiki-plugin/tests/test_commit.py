"""TDD for commit.py — one structured git commit per manifest (§4)."""
import subprocess

import pytest

import commit


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    return tmp_path


# --- message shape -----------------------------------------------------------

def test_build_message_full_trailer():
    m = commit.Manifest(
        action="ingest",
        title="Postgres tuning",
        created=["wiki/concept/prepared-statements.md"],
        updated=["wiki/concept/db-connection-pooling.md"],
        superseded=[("wiki/source/deploy-capistrano.md",
                     "wiki/source/deploy-github-actions.md")],
        source_date="2026-03-01",
    )
    assert commit.build_message(m) == (
        "ingest: Postgres tuning\n"
        "\n"
        "created: wiki/concept/prepared-statements.md\n"
        "updated: wiki/concept/db-connection-pooling.md\n"
        "superseded: wiki/source/deploy-capistrano.md -> "
        "wiki/source/deploy-github-actions.md\n"
        "source-date: 2026-03-01\n"
    )


def test_build_message_is_deterministic():
    m = commit.Manifest(action="edit", title="Fix typo",
                        updated=["wiki/concept/a.md"])
    assert commit.build_message(m) == commit.build_message(m)
    assert commit.build_message(m).startswith("edit: Fix typo\n\n")


def test_build_message_omits_empty_sections():
    m = commit.Manifest(action="ingest", title="Only created",
                        created=["wiki/concept/a.md"])
    msg = commit.build_message(m)
    assert "updated:" not in msg
    assert "superseded:" not in msg
    assert "source-date:" not in msg


# --- committing --------------------------------------------------------------

def test_commit_writes_structured_message_and_stages_files(git_repo):
    (git_repo / "wiki").mkdir()
    (git_repo / "wiki" / "concept").mkdir()
    a = git_repo / "wiki" / "concept" / "a.md"
    a.write_text("# A\n", encoding="utf-8")
    idx = git_repo / "wiki" / "_index.md"
    idx.write_text("index\n", encoding="utf-8")

    m = commit.Manifest(
        action="ingest",
        title="Seed A",
        created=["wiki/concept/a.md"],
        extra_paths=["wiki/_index.md"],
    )
    sha = commit.commit(git_repo, m)

    assert sha
    body = _git(git_repo, "log", "-1", "--pretty=%B")
    assert body.strip() == commit.build_message(m).strip()
    # Both the page and the index were committed; nothing left staged/dirty.
    tracked = _git(git_repo, "ls-files")
    assert "wiki/concept/a.md" in tracked
    assert "wiki/_index.md" in tracked
    assert _git(git_repo, "status", "--porcelain") == ""


def test_commit_stages_raw_source(git_repo):
    (git_repo / "wiki" / "source").mkdir(parents=True)
    (git_repo / "raw").mkdir()
    page = git_repo / "wiki" / "source" / "a.md"
    page.write_text("# A\n", encoding="utf-8")
    raw = git_repo / "raw" / "2026-03-01-0900-a.md"
    raw.write_text("raw\n", encoding="utf-8")

    m = commit.Manifest(
        action="ingest",
        title="Seed A",
        created=["wiki/source/a.md"],
        raw_source="raw/2026-03-01-0900-a.md",
    )
    commit.commit(git_repo, m)
    tracked = _git(git_repo, "ls-files")
    assert "raw/2026-03-01-0900-a.md" in tracked


def test_commit_stages_both_sides_of_supersede(git_repo):
    (git_repo / "wiki").mkdir()
    old = git_repo / "wiki" / "old.md"
    new = git_repo / "wiki" / "new.md"
    old.write_text("# Old\n", encoding="utf-8")
    new.write_text("# New\n", encoding="utf-8")
    m = commit.Manifest(
        action="ingest",
        title="Supersede",
        superseded=[("wiki/old.md", "wiki/new.md")],
    )
    commit.commit(git_repo, m)
    tracked = _git(git_repo, "ls-files")
    assert "wiki/old.md" in tracked and "wiki/new.md" in tracked


def test_commit_fails_loudly_when_not_a_repo(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("x\n", encoding="utf-8")
    m = commit.Manifest(action="ingest", title="x", created=["a.md"])
    with pytest.raises(commit.GitError):
        commit.commit(tmp_path, m)


def test_commit_fails_loudly_when_git_absent(git_repo, monkeypatch):
    (git_repo / "a.md").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(commit.shutil, "which", lambda _name: None)
    m = commit.Manifest(action="ingest", title="x", created=["a.md"])
    with pytest.raises(commit.GitError):
        commit.commit(git_repo, m)
