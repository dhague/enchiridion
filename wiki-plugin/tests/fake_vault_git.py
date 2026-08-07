"""In-memory stand-in for :class:`vault_git.VaultGit` — no subprocess.

The seam that makes VaultGit a real seam rather than relocation (issue #126):
tests script the fake's responses and assert each caller's absent-git policy
against it, without standing up a git work tree. The fake mirrors the real
interface's two surfaces:

* **strict** methods (``ensure_work_tree``/``init``/``add``/``commit``) raise
  :class:`vault_git.GitError` when ``available=False`` or ``work_tree=False``;
* **lenient** methods (``last_commit_date``/``porcelain_mentions``/
  ``commit_dates``/``is_work_tree``) return the documented defaults.

Scripted state: ``last_commit_dates`` answers :meth:`last_commit_date`,
``dirty`` answers :meth:`porcelain_mentions`, and ``commit_dates`` answers
:meth:`commit_dates`. ``add``/``commit`` record their calls so tests can assert
what was staged and what message was committed.
"""
from __future__ import annotations

from typing import Iterable

from vault_git import GitError, VaultGit


class FakeVaultGit(VaultGit):
    def __init__(
        self,
        *,
        available: bool = True,
        work_tree: bool = True,
        last_commit_dates: dict[str, str] | None = None,
        dirty: Iterable[str] = (),
        commit_dates: dict[str, str] | None = None,
        sha: str = "fakesha",
    ) -> None:
        super().__init__(root="")
        self._available = available
        self.work_tree = work_tree
        self.last_commit_dates = dict(last_commit_dates or {})
        self.dirty = set(dirty)
        self._commit_dates = dict(commit_dates or {})
        self.sha = sha
        self.added: list[str] = []
        self.messages: list[str] = []
        self.init_called = False

    # -- strict surface ----------------------------------------------------

    def available(self) -> bool:
        return self._available

    def run(self, *args: str) -> str:
        raise GitError("FakeVaultGit cannot run git")

    def ensure_work_tree(self) -> None:
        if not self._available or not self.work_tree:
            raise GitError("git unavailable or not a work tree")

    def init(self) -> None:
        if not self._available:
            raise GitError("git is required but was not found on PATH")
        self.init_called = True

    def add(self, *paths: str) -> None:
        if not self._available:
            raise GitError("git is required but was not found on PATH")
        self.added.extend(paths)

    def commit(self, message: str) -> str:
        if not self._available:
            raise GitError("git is required but was not found on PATH")
        self.messages.append(message)
        return self.sha

    # -- lenient surface ---------------------------------------------------

    def is_work_tree(self) -> bool:
        return self._available and self.work_tree

    def last_commit_date(self, rel: str) -> str | None:
        return self.last_commit_dates.get(rel)

    def porcelain_mentions(self, rel: str) -> bool:
        return rel in self.dirty

    def commit_dates(self) -> dict[str, str]:
        return dict(self._commit_dates)

    def set_commit_dates(self, dates: dict[str, str]) -> None:
        self._commit_dates = dict(dates)
