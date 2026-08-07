"""TDD for commit.py — one structured git commit per manifest (§4)."""
import subprocess

import pytest

import commit
from fake_vault_git import FakeVaultGit


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
        created=["wiki/concepts/prepared-statements.md"],
        updated=["wiki/concepts/db-connection-pooling.md"],
        superseded=[("wiki/sources/deploy-capistrano.md",
                     "wiki/sources/deploy-github-actions.md")],
        source_date="2026-03-01",
    )
    assert commit.build_message(m) == (
        "ingest: Postgres tuning\n"
        "\n"
        "created: wiki/concepts/prepared-statements.md\n"
        "updated: wiki/concepts/db-connection-pooling.md\n"
        "superseded: wiki/sources/deploy-capistrano.md -> "
        "wiki/sources/deploy-github-actions.md\n"
        "source-date: 2026-03-01\n"
    )


def test_build_message_is_deterministic():
    m = commit.Manifest(action="edit", title="Fix typo",
                        updated=["wiki/concepts/a.md"])
    assert commit.build_message(m) == commit.build_message(m)
    assert commit.build_message(m).startswith("edit: Fix typo\n\n")


def test_build_message_omits_empty_sections():
    m = commit.Manifest(action="ingest", title="Only created",
                        created=["wiki/concepts/a.md"])
    msg = commit.build_message(m)
    assert "updated:" not in msg
    assert "superseded:" not in msg
    assert "source-date:" not in msg


# --- committing --------------------------------------------------------------

def test_commit_writes_structured_message_and_stages_files(git_repo):
    (git_repo / "wiki").mkdir()
    (git_repo / "wiki" / "concepts").mkdir()
    a = git_repo / "wiki" / "concepts" / "a.md"
    a.write_text("# A\n", encoding="utf-8")

    m = commit.Manifest(
        action="ingest",
        title="Seed A",
        created=["wiki/concepts/a.md"],
    )
    sha = commit.commit(git_repo, m)

    assert sha
    body = _git(git_repo, "log", "-1", "--pretty=%B")
    assert body.strip() == commit.build_message(m).strip()
    # The page was committed; nothing left staged/dirty.
    tracked = _git(git_repo, "ls-files")
    assert "wiki/concepts/a.md" in tracked
    assert _git(git_repo, "status", "--porcelain") == ""


def test_commit_stages_raw_source(git_repo):
    (git_repo / "wiki" / "sources").mkdir(parents=True)
    (git_repo / "raw").mkdir()
    page = git_repo / "wiki" / "sources" / "a.md"
    # the stub carries the chain-of-evidence frontmatter so the #34 gate
    # recognises it; that's the test for that gate lives further down
    page.write_text(
        '---\n'
        'title: A\n'
        'raw_source: "[2026-03-01-0900-a.md](../../raw/2026-03-01-0900-a.md)"\n'
        '---\n'
        '# A\n',
        encoding="utf-8",
    )
    raw = git_repo / "raw" / "2026-03-01-0900-a.md"
    raw.write_text("raw\n", encoding="utf-8")

    m = commit.Manifest(
        action="ingest",
        title="Seed A",
        created=["wiki/sources/a.md"],
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


def test_commit_fails_loudly_when_git_absent(tmp_path):
    """#126: absent git is the strict surface's hard dependency — GitError,
    never a silent skip — read off the fake instead of PATH monkeypatching."""
    (tmp_path / "a.md").write_text("x\n", encoding="utf-8")
    m = commit.Manifest(action="ingest", title="x", created=["a.md"])
    with pytest.raises(commit.GitError):
        commit.commit(tmp_path, m, git=FakeVaultGit(available=False))


def test_commit_records_add_and_commit_against_fake(tmp_path):
    """#126: the whole commit verb — ensure work tree, stage, structured
    message, SHA — is assertable against the in-memory fake, no work tree."""
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "a.md").write_text("# A\n", encoding="utf-8")
    fake = FakeVaultGit(sha="abc123")
    m = commit.Manifest(action="ingest", title="Seed A", created=["wiki/a.md"])

    sha = commit.commit(tmp_path, m, git=fake)

    assert sha == "abc123"
    assert fake.added == ["wiki/a.md"]
    assert fake.messages == [commit.build_message(m)]


# --- chain-of-evidence gate (#34 point 4) -------------------------------------
#
# The agent-time half of this check lives in `ingest._chain_of_evidence_errors`
# (see tests in test_ingest.py). The tests here are the commit-time half:
# `commit.commit()` itself re-checks every manifest that names a raw_source
# before any file is staged, so a hand-built manifest or a future caller
# can't slip a violation past `ingest.validate()` and into history.


@pytest.fixture
def vault(git_repo):
    """A git repo with the wiki/ + raw/ folder skeleton but no pages."""
    (git_repo / "wiki" / "concepts").mkdir(parents=True)
    (git_repo / "wiki" / "entities").mkdir(parents=True)
    (git_repo / "wiki" / "sources").mkdir(parents=True)
    (git_repo / "wiki" / "synthesis").mkdir(parents=True)
    (git_repo / "raw").mkdir()
    return git_repo


def _seed_stub(vault, raw_name="notes.md", stub_rel="wiki/sources/notes.md"):
    """Seed a raw file plus a stub page whose raw_source points at it."""
    (vault / "raw" / raw_name).write_text("raw\n", encoding="utf-8")
    (vault / stub_rel).write_text(
        '---\n'
        f'title: Notes\nraw_source: "[{raw_name}](../../raw/{raw_name})"\n'
        '---\n'
        '# Notes\n',
        encoding="utf-8",
    )
    return stub_rel


def _seed_distilled(vault, rel, stub_rel="wiki/sources/notes.md"):
    """Seed a distilled page whose `source` edge points back at the stub."""
    stub_label = stub_rel.rsplit("/", 1)[-1].removesuffix(".md")
    parent = vault / rel
    parent.parent.mkdir(parents=True, exist_ok=True)
    parent.write_text(
        '---\n'
        f'title: {rel.rsplit("/", 1)[-1].removesuffix(".md").title()}\n'
        f'source:\n  - "[{stub_label}](../sources/{stub_label}.md)"\n'
        '---\n'
        f'# {rel.rsplit("/", 1)[-1].removesuffix(".md").title()}\n',
        encoding="utf-8",
    )
    return rel


def test_commit_passes_when_chain_of_evidence_is_satisfied(vault):
    """A raw ingestion with its stub + a distilled page that links to it lands."""
    stub_rel = _seed_stub(vault)
    distilled_rel = _seed_distilled(vault, "wiki/concepts/prepared-statements.md")

    m = commit.Manifest(
        action="ingest",
        title="Postgres tuning",
        created=[stub_rel, distilled_rel],
        raw_source="raw/notes.md",
    )
    sha = commit.commit(vault, m)
    assert sha
    assert _git(vault, "status", "--porcelain") == ""


def test_commit_gates_a_raw_ingestion_with_no_source_stub(vault):
    """A raw_source without a wiki/sources/ page to stand in for it is a hard block."""
    (vault / "raw" / "notes.md").write_text("raw\n", encoding="utf-8")
    distilled_rel = "wiki/concepts/prepared-statements.md"
    (vault / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
    _seed_distilled(vault, distilled_rel)
    # no stub written — the case the old 'distil straight into concepts/'
    # workflow would have left behind

    m = commit.Manifest(
        action="ingest",
        title="Forgot the stub",
        created=[distilled_rel],
        raw_source="raw/notes.md",
    )
    with pytest.raises(commit.CommitGateError, match="sources/ page"):
        commit.commit(vault, m)
    # nothing landed: the gate runs before git add, so a violation never
    # leaves a partial commit on the branch
    assert "prepared-statements.md" not in _git(vault, "ls-files")


def test_commit_gates_a_stub_whose_raw_source_points_elsewhere(vault):
    """The stub's own raw_source must resolve to manifest.raw_source."""
    (vault / "raw" / "notes.md").write_text("raw\n", encoding="utf-8")
    (vault / "raw" / "other.md").write_text("other\n", encoding="utf-8")
    (vault / "wiki" / "sources" / "notes.md").write_text(
        '---\n'
        'title: Notes\n'
        'raw_source: "[other.md](../../raw/other.md)"\n'
        '---\n'
        '# Notes\n',
        encoding="utf-8",
    )
    distilled_rel = "wiki/concepts/prepared-statements.md"
    _seed_distilled(vault, distilled_rel)

    m = commit.Manifest(
        action="ingest",
        title="Wrong pointer",
        created=[distilled_rel, "wiki/sources/notes.md"],
        raw_source="raw/notes.md",
    )
    with pytest.raises(commit.CommitGateError, match="sources/ page"):
        commit.commit(vault, m)


def test_commit_gates_a_distilled_page_missing_the_source_edge(vault):
    """A page the commit stages must carry a `source` edge to the stub."""
    stub_rel = _seed_stub(vault)
    distilled_rel = "wiki/concepts/prepared-statements.md"
    (vault / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
    (vault / distilled_rel).write_text(
        '---\n'
        'title: Prepared Statements\n'
        '---\n'
        '# Prepared Statements\n',
        encoding="utf-8",
    )

    m = commit.Manifest(
        action="ingest",
        title="Forgot the edge",
        created=[stub_rel, distilled_rel],
        raw_source="raw/notes.md",
    )
    with pytest.raises(commit.CommitGateError, match="source edge"):
        commit.commit(vault, m)


def test_commit_gate_is_a_noop_when_raw_source_is_unset(vault):
    """A synthesis save has no raw artifact — the gate is silent."""
    synthesized_rel = "wiki/synthesis/pooling-answer.md"
    parent = vault / synthesized_rel
    parent.parent.mkdir(parents=True, exist_ok=True)
    parent.write_text(
        '---\n'
        'title: Pooling answer\n'
        '---\n'
        '# Pooling answer\n',
        encoding="utf-8",
    )

    m = commit.Manifest(
        action="synthesize",
        title="How pooling is configured",
        created=[synthesized_rel],
    )
    sha = commit.commit(vault, m)
    assert sha
    assert _git(vault, "status", "--porcelain") == ""


def test_commit_gate_recognises_an_existing_stub_updated_in_place(vault):
    """#34: the stub may be an update whose raw_source already lives on disk."""
    # First pass: write the stub and commit it (no raw_source in this
    # manifest — the stub is being seeded on its own).
    stub_rel = "wiki/sources/notes.md"
    (vault / "raw" / "notes.md").write_text("raw\n", encoding="utf-8")
    (vault / stub_rel).write_text(
        '---\n'
        'title: Notes\n'
        'raw_source: "[notes.md](../../raw/notes.md)"\n'
        '---\n'
        '# Notes\n',
        encoding="utf-8",
    )
    commit.commit(
        vault,
        commit.Manifest(
            action="ingest",
            title="Seed the stub",
            created=[stub_rel],
        ),
    )

    # Second pass: update the stub in place (no frontmatter changes) and
    # add a distilled page that links to it. The gate must accept the
    # updated-in-place stub because its raw_source is already on disk.
    distilled_rel = _seed_distilled(vault, "wiki/concepts/prepared-statements.md")
    m = commit.Manifest(
        action="ingest",
        title="Re-ingest notes",
        updated=[stub_rel],
        created=[distilled_rel],
        raw_source="raw/notes.md",
    )
    sha = commit.commit(vault, m)
    assert sha
    assert _git(vault, "status", "--porcelain") == ""


def test_commit_gate_fires_before_git_add_so_no_partial_commit_lands(vault):
    """A violation must not leave a half-staged set of files behind."""
    (vault / "raw" / "notes.md").write_text("raw\n", encoding="utf-8")
    distilled_rel = "wiki/concepts/prepared-statements.md"
    (vault / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
    _seed_distilled(vault, distilled_rel)

    m = commit.Manifest(
        action="ingest",
        title="No stub",
        created=[distilled_rel],
        raw_source="raw/notes.md",
    )
    with pytest.raises(commit.CommitGateError):
        commit.commit(vault, m)
    # the distilled page was written to disk by the caller (ingest or a
    # hand-rolled script), but the gate ran before git add, so nothing
    # was staged and no commit was created
    staged = _git(vault, "diff", "--cached", "--name-only")
    assert staged == ""
    assert "prepared-statements.md" not in _git(vault, "ls-files")
