"""TDD for ``ingest_scan.py`` — the ingestion sweep (#54, per #26).

The sweep walks ``raw/``, applies each folder's own ``.ingestignore``,
and reports a file as eligible for ingestion when (a) no page's
``raw_source`` points at it, or (b) one does but the raw file is
strictly newer than that page's ``git_date`` (or ``git status
--porcelain`` reports it dirty). Per #26 the two signals stay separate
from the policy file: ``.ingestignore`` governs *whether to offer*, the
scan governs *whether anything's changed*.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import ingest_scan
from fake_vault_git import FakeVaultGit
from ingest_scan import (
    IngestCandidate,
    ScanResult,
    append_ignore_entry,
    load_ingestignore,
    parse_ingestignore,
    scan,
    walk_raw,
)
from vault import Vault


# --- helpers --------------------------------------------------------------


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True, capture_output=True, text=True,
    ).stdout


def _seed_vault(root: Path) -> None:
    """A vault with wiki/{concept,entity,source,synthesis} and raw/."""
    (root / "wiki" / "concepts").mkdir(parents=True)
    (root / "wiki" / "entities").mkdir(parents=True)
    (root / "wiki" / "sources").mkdir(parents=True)
    (root / "wiki" / "synthesis").mkdir(parents=True)
    (root / "raw").mkdir()


# --- parse_ingestignore ---------------------------------------------------


class TestParseIngestignore:
    def test_blank_lines_are_skipped(self):
        assert parse_ingestignore("\n\n*.tmp\n\n") == ["*.tmp"]

    def test_hash_comments_are_stripped(self):
        assert parse_ingestignore("# header\n*.tmp\n# trailing\n") == ["*.tmp"]

    def test_inline_comment_is_stripped_to_end_of_line(self):
        # A literal `#` after a pattern is a trailing comment.
        assert parse_ingestignore("*.tmp  # all temp files\n") == ["*.tmp"]

    def test_order_preserved(self):
        text = "*.tmp\nliteral.md\n*.bak\n"
        assert parse_ingestignore(text) == ["*.tmp", "literal.md", "*.bak"]

    def test_empty_input(self):
        assert parse_ingestignore("") == []

    def test_only_comments(self):
        assert parse_ingestignore("# one\n# two\n") == []

    def test_rejects_slash(self):
        # `/` is a path separator — out of scope for a per-folder policy file.
        with pytest.raises(ValueError, match="/"):
            parse_ingestignore("sub/*.tmp\n")

    def test_rejects_negation(self):
        with pytest.raises(ValueError, match="!"):
            parse_ingestignore("!*.tmp\n")

    def test_rejects_doublestar(self):
        with pytest.raises(ValueError, match=r"\*\*"):
            parse_ingestignore("**/*.tmp\n")


# --- load_ingestignore ----------------------------------------------------


class TestLoadIngestignore:
    def test_absent_file_returns_empty(self, tmp_path):
        assert load_ingestignore(tmp_path) == []

    def test_reads_patterns(self, tmp_path):
        (tmp_path / ".ingestignore").write_text("*.tmp\nliteral.md\n", encoding="utf-8")
        assert load_ingestignore(tmp_path) == ["*.tmp", "literal.md"]


# --- walk_raw -------------------------------------------------------------


class TestWalkRaw:
    def test_yields_every_file_recursively(self, tmp_path):
        _seed_vault(tmp_path)
        (tmp_path / "raw" / "a.md").write_text("a", encoding="utf-8")
        (tmp_path / "raw" / "notes").mkdir()
        (tmp_path / "raw" / "notes" / "b.md").write_text("b", encoding="utf-8")
        files = sorted(p.relative_to(tmp_path).as_posix() for p in walk_raw(tmp_path))
        assert files == ["raw/a.md", "raw/notes/b.md"]

    def test_skips_ingestion_md(self, tmp_path):
        _seed_vault(tmp_path)
        (tmp_path / "raw" / "INGESTION.md").write_text("hints", encoding="utf-8")
        (tmp_path / "raw" / "real.md").write_text("r", encoding="utf-8")
        names = [p.name for p in walk_raw(tmp_path)]
        assert "INGESTION.md" not in names
        assert names == ["real.md"]

    def test_skips_ingestignore_itself(self, tmp_path):
        _seed_vault(tmp_path)
        (tmp_path / "raw" / ".ingestignore").write_text("*.tmp\n", encoding="utf-8")
        (tmp_path / "raw" / "real.md").write_text("r", encoding="utf-8")
        names = [p.name for p in walk_raw(tmp_path)]
        assert ".ingestignore" not in names
        assert names == ["real.md"]

    def test_scoped_to_one_folder(self, tmp_path):
        _seed_vault(tmp_path)
        (tmp_path / "raw" / "a.md").write_text("a", encoding="utf-8")
        (tmp_path / "raw" / "notes").mkdir()
        (tmp_path / "raw" / "notes" / "b.md").write_text("b", encoding="utf-8")
        files = sorted(p.relative_to(tmp_path).as_posix() for p in walk_raw(tmp_path, "notes"))
        assert files == ["raw/notes/b.md"]

    def test_missing_folder_yields_nothing(self, tmp_path):
        _seed_vault(tmp_path)
        assert list(walk_raw(tmp_path, "nope")) == []


# --- append_ignore_entry --------------------------------------------------


class TestAppendIgnoreEntry:
    def test_creates_file_if_absent(self, tmp_path):
        append_ignore_entry(tmp_path, "*.tmp")
        assert (tmp_path / ".ingestignore").read_text(encoding="utf-8") == "*.tmp\n"

    def test_appends_to_existing_file(self, tmp_path):
        (tmp_path / ".ingestignore").write_text("*.bak\n", encoding="utf-8")
        append_ignore_entry(tmp_path, "*.tmp")
        text = (tmp_path / ".ingestignore").read_text(encoding="utf-8")
        assert text == "*.bak\n*.tmp\n"

    def test_idempotent_when_pattern_already_present(self, tmp_path):
        (tmp_path / ".ingestignore").write_text("*.tmp\n", encoding="utf-8")
        append_ignore_entry(tmp_path, "*.tmp")
        text = (tmp_path / ".ingestignore").read_text(encoding="utf-8")
        # no double-write
        assert text == "*.tmp\n"

    def test_appends_with_a_trailing_comment(self, tmp_path):
        append_ignore_entry(tmp_path, "foo.md", comment="ingested before back-pointers were mandatory")
        text = (tmp_path / ".ingestignore").read_text(encoding="utf-8")
        assert text == "foo.md  # ingested before back-pointers were mandatory\n"

    def test_idempotent_with_comment_when_pattern_already_present(self, tmp_path):
        (tmp_path / ".ingestignore").write_text("foo.md  # old reason\n", encoding="utf-8")
        append_ignore_entry(tmp_path, "foo.md", comment="new reason")
        # The first call's comment stays — the second call is a no-op, by design.
        assert (tmp_path / ".ingestignore").read_text(encoding="utf-8") == "foo.md  # old reason\n"


# --- scan: ignored files --------------------------------------------------


class TestScanIgnored:
    def test_a_file_matching_its_own_folders_ingestignore_is_ignored(self, tmp_path):
        _seed_vault(tmp_path)
        (tmp_path / "raw" / ".ingestignore").write_text("*.tmp\n", encoding="utf-8")
        (tmp_path / "raw" / "foo.tmp").write_text("junk", encoding="utf-8")
        (tmp_path / "raw" / "real.md").write_text("real", encoding="utf-8")

        result = scan(tmp_path)
        assert [c.raw_rel for c in result.eligible] == ["raw/real.md"]
        assert result.ignored == ["raw/foo.tmp"]

    def test_parents_ingestignore_does_not_apply(self, tmp_path):
        """`raw/INGESTION.md` lives next to the inbox, but a file in `raw/notes/`
        must NOT be governed by it — the lookup is the file's own folder only."""
        _seed_vault(tmp_path)
        (tmp_path / "raw" / ".ingestignore").write_text("*.tmp\n", encoding="utf-8")
        (tmp_path / "raw" / "notes").mkdir()
        (tmp_path / "raw" / "notes" / "foo.tmp").write_text("junk", encoding="utf-8")

        result = scan(tmp_path)
        # The child folder has no .ingestignore, so the parent's pattern
        # does not apply — `foo.tmp` is offered.
        assert [c.raw_rel for c in result.eligible] == ["raw/notes/foo.tmp"]
        assert result.ignored == []

    def test_own_folders_ingestignore_overrides_back_pointers(self, tmp_path):
        """A .ingestignore match is *policy* — it trumps the eligibility
        signal. A file already linked from a page, but matched by its
        folder's policy, must not be offered."""
        _seed_vault(tmp_path)
        (tmp_path / "wiki" / "sources" / "foo.md").write_text(
            '---\ntitle: Foo\nraw_source: "[foo.md](../../raw/foo.md)"\n---\n# Foo\n',
            encoding="utf-8",
        )
        (tmp_path / "raw" / ".ingestignore").write_text("foo.md\n", encoding="utf-8")
        (tmp_path / "raw" / "foo.md").write_text("raw", encoding="utf-8")

        result = scan(tmp_path)
        assert [c.raw_rel for c in result.eligible] == []
        assert result.ignored == ["raw/foo.md"]


# --- scan: eligibility ----------------------------------------------------


class TestScanEligibility:
    def test_a_file_with_no_back_pointer_is_never_ingested(self, tmp_path):
        _seed_vault(tmp_path)
        (tmp_path / "raw" / "foo.md").write_text("raw", encoding="utf-8")

        result = scan(tmp_path)
        assert len(result.eligible) == 1
        cand = result.eligible[0]
        assert cand.raw_rel == "raw/foo.md"
        assert cand.reason == "never-ingested"
        assert cand.back_pointers == []

    def test_a_file_with_a_back_pointer_but_no_git_is_still_offered(self, tmp_path):
        """No git available, or no date for a path ⇒ treat as eligible
        (fail toward offering, never toward silently skipping)."""
        _seed_vault(tmp_path)
        (tmp_path / "wiki" / "sources" / "foo.md").write_text(
            '---\ntitle: Foo\nraw_source: "[foo.md](../../raw/foo.md)"\n---\n# Foo\n',
            encoding="utf-8",
        )
        (tmp_path / "raw" / "foo.md").write_text("raw", encoding="utf-8")

        # No `git init` here, so the real VaultGit's lenient surface returns
        # None / False — the file is offered under the
        # "changed-since-ingestion" reason with a back-pointer hint.
        result = scan(tmp_path)
        assert len(result.eligible) == 1
        cand = result.eligible[0]
        assert cand.raw_rel == "raw/foo.md"
        assert cand.reason == "changed-since-ingestion"
        assert cand.back_pointers == ["wiki/sources/foo.md"]

    def test_a_percent_encoded_raw_source_still_matches(self, tmp_path):
        """A raw filename with spaces/parens is percent-encoded in the
        link destination; the scan must decode before comparing to the
        file on disk."""
        _seed_vault(tmp_path)
        # The source page carries an encoded destination.
        (tmp_path / "wiki" / "sources" / "my-notes.md").write_text(
            '---\ntitle: My Notes\n'
            'raw_source: "[My Notes (draft).md](../../raw/My%20Notes%20%28draft%29.md)"\n'
            '---\n# My Notes\n',
            encoding="utf-8",
        )
        (tmp_path / "raw" / "My Notes (draft).md").write_text("raw", encoding="utf-8")

        # Even with the encoding, the scan sees no back-pointer from disk
        # (the decodes-to-vault-relative path matches the on-disk file) and
        # no git — so the file is eligible under "never-ingested" reason.
        # (Wait: the source page IS a back-pointer, so the file is under
        # "changed-since-ingestion". The test pins the back-pointer path
        # so the encoding path is what's actually exercised here.)
        result = scan(tmp_path)
        assert len(result.eligible) == 1
        cand = result.eligible[0]
        assert cand.raw_rel == "raw/My Notes (draft).md"
        assert cand.reason == "changed-since-ingestion"
        assert cand.back_pointers == ["wiki/sources/my-notes.md"]


# --- scan: git-backed eligibility ----------------------------------------


@pytest.fixture
def git_vault(tmp_path):
    """A vault initialised as a git repo with the kind-folders + raw/."""
    _seed_vault(tmp_path)
    (tmp_path / "wiki" / "concepts" / "seed.md").write_text(
        "---\ntitle: Seed\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n---\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    return tmp_path


class TestScanGitBacked:
    def test_strictly_newer_fake_git_facts_offer_the_file(self, tmp_path):
        """#126: the sweep reads its git facts off VaultGit, so the strictly-
        newer rule is assertable against the in-memory fake — no work tree."""
        _seed_vault(tmp_path)
        (tmp_path / "wiki" / "sources" / "foo.md").write_text(
            '---\ntitle: Foo\nraw_source: "[foo.md](../../raw/foo.md)"\n---\n# Foo\n',
            encoding="utf-8",
        )
        (tmp_path / "raw" / "foo.md").write_text("raw", encoding="utf-8")
        fake = FakeVaultGit(
            last_commit_dates={
                "raw/foo.md": "2026-02-01",
                "wiki/sources/foo.md": "2026-01-01",
            },
        )

        result = scan(tmp_path, git=fake)

        assert len(result.eligible) == 1
        cand = result.eligible[0]
        assert cand.raw_rel == "raw/foo.md"
        assert cand.reason == "changed-since-ingestion"
        assert cand.back_pointers == ["wiki/sources/foo.md"]

    def test_dirty_fake_overrides_equal_dates(self, tmp_path):
        """#126: the fake's dirty set flips the offer even when the dates are
        equal — the porcelain check runs before the date comparison."""
        _seed_vault(tmp_path)
        (tmp_path / "wiki" / "sources" / "foo.md").write_text(
            '---\ntitle: Foo\nraw_source: "[foo.md](../../raw/foo.md)"\n---\n# Foo\n',
            encoding="utf-8",
        )
        (tmp_path / "raw" / "foo.md").write_text("raw", encoding="utf-8")
        fake = FakeVaultGit(
            dirty=["raw/foo.md"],
            last_commit_dates={
                "raw/foo.md": "2026-01-01",
                "wiki/sources/foo.md": "2026-01-01",
            },
        )

        result = scan(tmp_path, git=fake)

        assert len(result.eligible) == 1
        assert result.eligible[0].raw_rel == "raw/foo.md"
        assert result.eligible[0].reason == "changed-since-ingestion"

    def test_same_commit_means_not_offered(self, git_vault):
        """A raw file and the page it produces normally land in the same
        commit (`e548c78` in the vault does) — strictly newer, not `>=`,
        means we don't re-offer it forever."""
        (git_vault / "raw" / "notes.md").write_text("raw notes", encoding="utf-8")
        (git_vault / "wiki" / "sources" / "notes.md").write_text(
            '---\ntitle: Notes\nraw_source: "[notes.md](../../raw/notes.md)"\n---\n# Notes\n',
            encoding="utf-8",
        )
        _git(git_vault, "add", "-A")
        _git(git_vault, "commit", "-q", "-m", "ingest notes")

        result = scan(git_vault)
        # Both raw and page in one commit → not offered.
        assert result.eligible == []
        assert result.ignored == []

    def test_raw_strictly_newer_than_page_is_offered(self, git_vault):
        """Edit the raw file in a new commit; the page is now strictly
        older than the raw — the file is offered with its back-pointer.

        The two commits are pinned to different dates so the day-level
        ``git log --date=short`` comparison has something to distinguish
        (two same-day commits are equal and the test would not exercise
        the strictly-newer code path at all)."""
        import os

        def _commit(args, date: str) -> None:
            env = {**os.environ, "GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date}
            subprocess.run(
                ["git", "-C", str(git_vault), *args],
                check=True, env=env, capture_output=True,
            )

        (git_vault / "raw" / "notes.md").write_text("raw notes v1", encoding="utf-8")
        (git_vault / "wiki" / "sources" / "notes.md").write_text(
            '---\ntitle: Notes\nraw_source: "[notes.md](../../raw/notes.md)"\n---\n# Notes\n',
            encoding="utf-8",
        )
        _git(git_vault, "add", "-A")
        _commit(["commit", "-q", "-m", "ingest notes"], "2026-01-01T12:00:00")

        # New commit touches the raw file only, at a later date.
        (git_vault / "raw" / "notes.md").write_text("raw notes v2", encoding="utf-8")
        _git(git_vault, "add", "raw/notes.md")
        _commit(["commit", "-q", "-m", "edit raw"], "2026-02-01T12:00:00")

        result = scan(git_vault)
        assert len(result.eligible) == 1
        cand = result.eligible[0]
        assert cand.raw_rel == "raw/notes.md"
        assert cand.reason == "changed-since-ingestion"
        assert cand.back_pointers == ["wiki/sources/notes.md"]

    def test_dirty_working_tree_overrides_date_equality(self, git_vault):
        """An uncommitted edit is the cheapest re-offer signal — the
        file is dirty per `git status --porcelain`, so it's offered
        even though the committed dates are equal."""
        (git_vault / "raw" / "notes.md").write_text("raw notes", encoding="utf-8")
        (git_vault / "wiki" / "sources" / "notes.md").write_text(
            '---\ntitle: Notes\nraw_source: "[notes.md](../../raw/notes.md)"\n---\n# Notes\n',
            encoding="utf-8",
        )
        _git(git_vault, "add", "-A")
        _git(git_vault, "commit", "-q", "-m", "ingest notes")

        # Edit the raw file but DON'T commit. The committed date is
        # equal to the page's; the dirty status flips the offer.
        (git_vault / "raw" / "notes.md").write_text("raw notes v2 (uncommitted)", encoding="utf-8")

        result = scan(git_vault)
        assert len(result.eligible) == 1
        assert result.eligible[0].raw_rel == "raw/notes.md"
        assert result.eligible[0].reason == "changed-since-ingestion"


# --- scan: shape ----------------------------------------------------------


class TestScanResult:
    def test_empty_vault_yields_empty_results(self, tmp_path):
        _seed_vault(tmp_path)
        result = scan(tmp_path)
        assert result == ScanResult(eligible=[], ignored=[])

    def test_scoped_to_one_folder(self, tmp_path):
        _seed_vault(tmp_path)
        (tmp_path / "raw" / "a.md").write_text("a", encoding="utf-8")
        (tmp_path / "raw" / "notes").mkdir()
        (tmp_path / "raw" / "notes" / "b.md").write_text("b", encoding="utf-8")

        result = scan(tmp_path, folder="notes")
        assert [c.raw_rel for c in result.eligible] == ["raw/notes/b.md"]


# --- Sweep coordinator -----------------------------------------------------


class TestSweep:
    def test_sweep_scan_delegates(self, tmp_path):
        _seed_vault(tmp_path)
        (tmp_path / "raw" / "foo.md").write_text("raw", encoding="utf-8")
        sweep = ingest_scan.Sweep(Vault(tmp_path))
        result = sweep.scan()
        assert [c.raw_rel for c in result.eligible] == ["raw/foo.md"]

    def test_sweep_append_ignore_entry_writes_under_raw(self, tmp_path):
        _seed_vault(tmp_path)
        (tmp_path / "raw" / "notes").mkdir()
        sweep = ingest_scan.Sweep(Vault(tmp_path))
        sweep.append_ignore_entry("notes", "*.tmp")
        assert (tmp_path / "raw" / "notes" / ".ingestignore").read_text(encoding="utf-8") == "*.tmp\n"


# --- CLI -----------------------------------------------------------------


class TestCLI:
    def test_cli_prints_eligible_files(self, tmp_path, capsys, monkeypatch):
        _seed_vault(tmp_path)
        (tmp_path / "raw" / "a.md").write_text("a", encoding="utf-8")
        (tmp_path / "raw" / "b.md").write_text("b", encoding="utf-8")

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("WIKI_ROOT", raising=False)
        rc = ingest_scan._main([])
        assert rc == 0
        out = capsys.readouterr().out
        assert "raw/a.md" in out
        assert "raw/b.md" in out
        # the reason column appears
        assert "never-ingested" in out

    def test_cli_json_emits_structured_records(self, tmp_path, capsys, monkeypatch):
        _seed_vault(tmp_path)
        (tmp_path / "raw" / "a.md").write_text("a", encoding="utf-8")
        (tmp_path / "raw" / ".ingestignore").write_text("ignored.md\n", encoding="utf-8")
        (tmp_path / "raw" / "ignored.md").write_text("x", encoding="utf-8")

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("WIKI_ROOT", raising=False)
        rc = ingest_scan._main(["--json"])
        assert rc == 0
        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
        eligible = [r for r in lines if r["kind"] == "eligible"]
        ignored = [r for r in lines if r["kind"] == "ignored"]
        assert [r["raw_rel"] for r in eligible] == ["raw/a.md"]
        assert [r["raw_rel"] for r in ignored] == ["raw/ignored.md"]

    def test_cli_scoped_to_one_folder(self, tmp_path, capsys, monkeypatch):
        _seed_vault(tmp_path)
        (tmp_path / "raw" / "a.md").write_text("a", encoding="utf-8")
        (tmp_path / "raw" / "notes").mkdir()
        (tmp_path / "raw" / "notes" / "b.md").write_text("b", encoding="utf-8")

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("WIKI_ROOT", raising=False)
        rc = ingest_scan._main(["notes"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "raw/notes/b.md" in out
        assert "raw/a.md" not in out
