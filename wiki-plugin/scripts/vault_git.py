"""One module for git facts about the vault (#126).

Every ``shutil.which("git")`` probe and ``subprocess.run(["git", "-C", ...])``
call in the plugin lives here — the single place to read what the plugin does
when git is missing, and the single place to fake it. Before this module
existed, four callers each invented a different answer to "git wasn't there":
``commit.py`` raised, ``ingest_scan.py`` failed toward offering,
``search_index.py`` treated a missing date as ``None``, and ``init_wiki.py``
had its own handling again.

Callers keep their *own* absent-git policy, expressed against this interface
rather than against ``subprocess``. The two surfaces offered here are:

* **Strict** — :meth:`VaultGit.run`, :meth:`VaultGit.ensure_work_tree`,
  :meth:`VaultGit.init`, :meth:`VaultGit.add`, :meth:`VaultGit.commit`:
  raise :class:`GitError` when git is absent or a command fails. This is
  ``commit.py``'s "git is a hard dependency" reading.
* **Lenient** — :meth:`VaultGit.last_commit_date`,
  :meth:`VaultGit.porcelain_mentions`, :meth:`VaultGit.commit_dates`,
  :meth:`VaultGit.is_work_tree`: absent git or a failed command yields the
  documented default (``None`` / ``False`` / ``{}``) rather than raising.
  ``ingest_scan.py`` ("fail toward offering") and ``search_index.py`` ("a
  missing date means ``git_date is None``, never a failure") read theirs here.

This touches ADR-0003 (attribution from ingested content, not git identity)
and ``commit.py``'s hard-dependency statement only in *where* git is invoked
from — neither is reopened. ``init_wiki.py`` translates :class:`GitError`
into its own :class:`InitError`; that translation is its policy, kept here
deliberately out of this module.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    """Raised when git is unavailable or a git command fails.

    The strict-surface error: callers whose policy is "git is a hard
    dependency" (``commit.py``) propagate it; callers that translate it
    (``init_wiki.py`` into ``InitError``) catch it here.
    """


class VaultGit:
    """Git facts and verbs over one vault root.

    Constructing one never touches the filesystem or git — it just pins the
    root. All probing is lazy, so a caller can build one, ask an availability
    question, and never pay a subprocess if git isn't needed.
    """

    def __init__(self, root: Path | str):
        self.root = Path(root)

    # -- strict surface ----------------------------------------------------

    def available(self) -> bool:
        """``True`` iff ``git`` is on PATH. The one availability probe —
        strict callers gate on it before demanding anything of git."""
        return shutil.which("git") is not None

    def run(self, *args: str) -> str:
        """Run ``git -C <root> <args>`` and return stdout verbatim.

        **Strict:** raises :class:`GitError` when git is absent from PATH or
        the command exits non-zero. The one place the plugin shells out to
        git — every other method is a reading of this one.
        """
        if not self.available():
            raise GitError("git is required but was not found on PATH")
        proc = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
            )
        return proc.stdout

    def ensure_work_tree(self) -> None:
        """**Strict:** raise :class:`GitError` unless git is present and
        ``root`` is inside a git work tree.

        ``commit.py``'s hard-dependency gate: the time model depends on the
        history being complete, so a missing git or non-repo root is a
        failure, never a silent skip. A bare repo reports ``false`` here too —
        also a failure.
        """
        if not self.available():
            raise GitError("git is required but was not found on PATH")
        try:
            inside = self.run("rev-parse", "--is-inside-work-tree").strip()
        except GitError as exc:
            raise GitError(f"{self.root} is not a git work tree") from exc
        if inside != "true":
            raise GitError(f"{self.root} is not a git work tree")

    def init(self) -> None:
        """**Strict:** ``git init`` the root. Raises :class:`GitError` on
        failure."""
        self.run("init")

    def add(self, *paths: str) -> None:
        """**Strict:** stage ``paths``. Raises :class:`GitError` on failure."""
        self.run("add", "--", *paths)

    def commit(self, message: str) -> str:
        """**Strict:** write one commit with ``message`` and return its SHA.
        Raises :class:`GitError` on failure."""
        self.run("commit", "-m", message)
        return self.run("rev-parse", "HEAD").strip()

    # -- lenient surface ---------------------------------------------------

    def is_work_tree(self) -> bool:
        """**Lenient:** ``True`` iff ``root`` is inside a git work tree.
        ``False`` when git is absent or the probe fails — a question, not a
        demand."""
        try:
            return self.run("rev-parse", "--is-inside-work-tree").strip() == "true"
        except GitError:
            return False

    def last_commit_date(self, rel: str) -> str | None:
        """The last commit date of ``rel`` (``YYYY-MM-DD``), or ``None`` when
        git is unavailable, ``rel`` was never committed, or git exits non-zero.

        **Lenient:** the default is ``None``, never a raise. ``ingest_scan.py``
        reads "fail toward offering" off this — a missing date must err toward
        re-offering the file, the only safe direction for a signal that must
        not lose data.
        """
        try:
            out = self.run("log", "-1", "--format=%ad", "--date=short", "--", rel)
        except GitError:
            return None
        if not out.strip():
            return None
        return out.strip().splitlines()[-1]

    def porcelain_mentions(self, rel: str) -> bool:
        """``True`` iff ``git status --porcelain`` reports ``rel`` modified or
        untracked. Untracked (``??``) counts: a brand-new file isn't in git's
        index at all, and finding it is the point.

        **Lenient:** ``False`` when git is absent or the command fails —
        ``ingest_scan.py`` treats "can't tell if dirty" as "clean".
        """
        try:
            out = self.run("status", "--porcelain", "--", rel)
        except GitError:
            return False
        return bool(out.strip())

    def commit_dates(self) -> dict[str, str]:
        """One ``git log`` pass → ``{vault-relative page_ref: YYYY-MM-DD}``,
        most recent commit per file. ``.md`` files under ``wiki/`` only.

        **Lenient:** ``{}`` when git is unavailable or this isn't a work
        tree — ``search_index.py`` reads "a missing date means ``git_date is
        None``, never a failure" off this. Compute this once per scan, not
        once per page (#124).
        """
        try:
            out = self.run("log", "--name-only", "--format=%H|%ad", "--date=short")
        except GitError:
            return {}
        dates: dict[str, str] = {}
        current_date: str | None = None
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            if "|" in line:
                _sha, _, current_date = line.partition("|")
            elif current_date and line.endswith(".md") and line.startswith("wiki/"):
                # git log paths are already vault-relative — no strip, unlike
                # the old wiki-relative convention (ADR-0009).
                if line not in dates:
                    dates[line] = current_date
        return dates
