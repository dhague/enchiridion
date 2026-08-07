"""``SearchIndex`` — a SQLite FTS5 lexical index for the ``Vault``.

Per [ADR-0006](../docs/adr/0006-stdlib-fts5-not-embeddings.md): a single
gitignored ``.wiki-knowledge/index.db`` at the vault root holds a ``page``
metadata table (kind, tags, source_date, git_date, volatility, supersedes,
superseded_by, mtime_ns, size) plus an FTS5 virtual table over
title/summary/body. The composite query shape — *"pages updated in the last
week, tagged `foo`, containing `bar`"* — is one SQL statement with text as
``MATCH`` and metadata as ``WHERE`` predicates.

**Where correctness lives** (the design decision the rest of the code assumes):
an unconditional ``(mtime_ns, size)`` staleness scan runs on every search
call. ``Vault.write``'s inline update is a latency optimisation only, so the
index cannot go wrong because a caller forgot to update it — including for
edits made outside the plugin entirely (git pull, Obsidian). A capability
probe at open time falls back to a Python ``re`` backend when FTS5 isn't
compiled into the platform's SQLite.

Depends only on ``page_record`` (frontmatter decoding), ``wikipage`` (body
split) and ``vault_git`` (the ``git_date`` map); the ``re`` fallback reads
page text off disk itself. Nothing here imports ``vault`` — the index is a
stand-alone object ``Vault`` owns and proxies through, and the reverse
dependency would be a cycle.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import page_record
import wikipage
from vault_git import VaultGit


#: Bump when the on-disk schema changes. A mismatch on open triggers a
#: full rebuild — delete-and-rebuild is the migration strategy.
SCHEMA_VERSION = "2"

#: ``bm25()`` column weights for ``page_ref`` (UNINDEXED), title, summary,
#: body — encodes the retrieval skill's "frontmatter-first" instruction into
#: the ranking rather than leaving it as prose the agent must remember.
_FTS5_WEIGHTS = (0.0, 10.0, 5.0, 1.0)


# --- public types --------------------------------------------------------


@dataclass(frozen=True)
class SearchHit:
    """One search result. ``score`` is higher-is-better — ``bm25()`` returns
    negative values, so it is negated on the way out. ``page_ref`` is
    vault-relative, so it is directly usable as a plan edge/``update`` target
    (ADR-0009)."""

    page_ref: str
    score: float
    title: str
    summary: str
    tags: list[str]
    kind: str
    source_date: str
    git_date: str | None
    volatility: str
    superseded_by: str | None
    snippet: str | None


@dataclass
class IndexStats:
    pages: int = 0
    inserted: int = 0
    updated: int = 0
    removed: int = 0
    duration_ms: float = 0.0


@dataclass
class IndexStatus:
    pages: int
    db_size_bytes: int
    backend: str
    schema_version: str
    #: Unpopulated placeholders — :meth:`SearchIndex.status` always passes 0.
    #: ``status()`` doesn't scan, so it has no scan figures to report.
    last_scan_duration_ms: float
    pages_scanned: int
    pages_stale: int


# --- tokenize-and-phrase-quote (the FTS5 syntax footgun) -----------------


def tokenize_query(text: str) -> str:
    """Split ``text`` on whitespace and phrase-quote each term.

    FTS5's ``MATCH`` is a query language, not a string, and ordinary vault
    vocabulary is a syntax error in it — a hyphenated tag like
    ``wiki-knowledge`` raises ``no such column: knowledge``. Hence quoting by
    default, with ``raw=True`` the escape hatch for callers who actually want
    ``NEAR()``, ``OR``, and prefix operators.
    """
    if not text:
        return ""
    out: list[str] = []
    for term in text.split():
        out.append('"' + term.replace('"', '\\"') + '"')
    return " ".join(out)


# --- capability probe ----------------------------------------------------


def _probe_fts5(conn: sqlite3.Connection) -> bool:
    """``True`` iff this SQLite can ``CREATE VIRTUAL TABLE … USING fts5``.
    Otherwise the ``re`` fallback backend is used."""
    try:
        conn.execute("CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE _fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


# --- SearchIndex ---------------------------------------------------------


class SearchIndex:
    """The lexical index for one vault. One ``SearchIndex`` per vault,
    lifetime of the process.

    The unit of address is a vault-relative page reference
    (``wiki/concepts/foo.md``) throughout — schema, inline-update API, and
    search results alike. It is *not* re-labelled on the way out:
    ``Vault.search`` proxies these page_refs unchanged, so a hit's
    ``page_ref`` is directly usable as a plan edge/``update`` target
    (ADR-0009).
    """

    def __init__(self, root: Path | str, git: VaultGit | None = None):
        self.root = Path(root)
        # ``git`` is injectable for tests (an in-memory :class:`VaultGit`
        # fake). Its lenient absent-git policy — ``{}``, so ``git_date`` is
        # ``None`` — is the "never a failure" reading search relies on.
        self._git = git or VaultGit(self.root)
        self._index_dir = self.root / ".wiki-knowledge"
        self._index_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._index_dir / "index.db"
        self._conn = sqlite3.connect(self._db_path)
        self.backend: str = "fts5" if _probe_fts5(self._conn) else "re"
        self._create_schema()
        if not self._schema_ok():
            self._full_rebuild()

    # -- schema lifecycle ------------------------------------------------

    def _create_schema(self) -> None:
        c = self._conn
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS page (
                page_ref      TEXT PRIMARY KEY,
                title         TEXT,
                summary       TEXT,
                kind          TEXT,
                source_date   TEXT,
                git_date      TEXT,
                volatility    TEXT,
                supersedes    TEXT,    -- JSON array of vault-relative page_refs
                superseded_by TEXT,    -- single vault-relative page_ref, or NULL
                mtime_ns      INTEGER,
                size          INTEGER
            );
            CREATE TABLE IF NOT EXISTS page_tag (
                page_ref TEXT,
                tag TEXT,
                PRIMARY KEY (page_ref, tag)
            );
            CREATE INDEX IF NOT EXISTS ix_page_tag_tag ON page_tag(tag);
            """
        )
        if self.backend == "fts5":
            c.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS page_fts USING fts5(
                    page_ref UNINDEXED, title, summary, body,
                    tokenize = 'porter unicode61'
                );
                """
            )
        # Seed the schema version the first time; check it on every open.
        c.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        c.commit()

    def _schema_ok(self) -> bool:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        return bool(row and row[0] == SCHEMA_VERSION)

    def _full_rebuild(self) -> IndexStats:
        """Wipe and re-index. Delete-and-rebuild *is* the migration strategy —
        no incremental migrations, ever. Tables are dropped, not just emptied,
        so a schema-spelling change (ADR-0009's ``rel`` → ``page_ref``) is
        absorbed by the same path as a data wipe."""
        c = self._conn
        for table in ("page_tag", "page"):
            c.execute(f"DROP TABLE IF EXISTS {table}")
        if self.backend == "fts5":
            c.execute("DROP TABLE IF EXISTS page_fts")
        self._create_schema()
        c.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        c.commit()
        return self._reindex_walk()

    # -- core index walk -------------------------------------------------

    def reindex(self, *, full: bool = False) -> IndexStats:
        """Re-index the vault. ``full=True`` wipes first; ``full=False``
        runs the delta scan. Both call the same per-page path."""
        start = time.monotonic()
        if full:
            stats = self._full_rebuild()
        else:
            stats = self._scan()
        stats.duration_ms = (time.monotonic() - start) * 1000
        return stats

    def _scan(self) -> IndexStats:
        """Staleness scan: walk ``wiki/**``, upsert diffs. The correctness
        path — every search runs it before querying.

        Each upsert commits immediately, so a mid-scan crash leaves the index
        partially updated. That's acceptable: the filesystem is the source of
        truth and the next scan reconciles, so the index needs no
        all-or-nothing semantic to protect.
        """
        start = time.monotonic()
        rows = self._conn.execute(
            "SELECT page_ref, mtime_ns, size FROM page"
        ).fetchall()
        indexed: dict[str, tuple[int, int]] = {
            page_ref: (mtime_ns, size) for page_ref, mtime_ns, size in rows
        }
        seen: set[str] = set()
        stats = IndexStats()
        git_dates = self._git.commit_dates()
        for path in (self.root / "wiki").rglob("*.md"):
            page_ref = path.relative_to(self.root).as_posix()
            seen.add(page_ref)
            st = path.stat()
            mtime_ns, size = st.st_mtime_ns, st.st_size
            prev = indexed.get(page_ref)
            if prev is None:
                self.upsert_page(page_ref, path.read_text(encoding="utf-8"),
                                 git_dates=git_dates)
                stats.inserted += 1
            elif prev != (mtime_ns, size):
                self.upsert_page(page_ref, path.read_text(encoding="utf-8"),
                                 git_dates=git_dates)
                stats.updated += 1

        for page_ref in indexed.keys() - seen:
            self.remove_page(page_ref)
            stats.removed += 1

        self._recompute_superseded_by()
        stats.pages = self._count_pages()
        stats.duration_ms = (time.monotonic() - start) * 1000
        return stats

    def _reindex_walk(self) -> IndexStats:
        """Full walk, no diff — every file upserted. The schema-mismatch
        rebuild path."""
        start = time.monotonic()
        stats = IndexStats()
        git_dates = self._git.commit_dates()
        for path in (self.root / "wiki").rglob("*.md"):
            page_ref = path.relative_to(self.root).as_posix()
            self.upsert_page(page_ref, path.read_text(encoding="utf-8"),
                             git_dates=git_dates)
            stats.inserted += 1
        self._recompute_superseded_by()
        stats.pages = self._count_pages()
        stats.duration_ms = (time.monotonic() - start) * 1000
        return stats

    def _stats_snapshot(self) -> IndexStats:
        return IndexStats(pages=self._count_pages())

    def _count_pages(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM page").fetchone()[0]

    # -- per-page upsert/remove (used inline by Vault) -------------------

    def upsert_page(
        self, page_ref: str, text: str,
        git_dates: dict[str, str] | None = None,
    ) -> None:
        """Index/replace one page. ``page_ref`` is vault-relative (ADR-0009).
        The file at ``self.root / page_ref`` must already exist — its
        ``(mtime_ns, size)`` is stored verbatim, and is what the next
        staleness scan compares against to call this row fresh.

        ``git_dates`` is the ``{page_ref: date}`` map from one
        :meth:`VaultGit.commit_dates` pass; scan callers derive it once per
        walk and hand it down. When it's ``None`` (the single-page path,
        ``Vault.write``'s inline update) it is computed here — one full-history
        ``git log`` pass per write, the honest cost of that already-documented
        latency optimisation.
        """
        rec = page_record.page_record(page_ref, text)
        path = self.root / page_ref
        st = path.stat()
        if git_dates is None:
            git_dates = self._git.commit_dates()
        body = _body_text(text)

        c = self._conn
        c.execute("DELETE FROM page WHERE page_ref = ?", (page_ref,))
        c.execute("DELETE FROM page_tag WHERE page_ref = ?", (page_ref,))
        if self.backend == "fts5":
            c.execute("DELETE FROM page_fts WHERE page_ref = ?", (page_ref,))

        c.execute(
            "INSERT INTO page(page_ref, title, summary, kind, source_date, "
            "git_date, volatility, supersedes, superseded_by, mtime_ns, size) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
            (
                page_ref,
                rec.title,
                rec.summary,
                rec.kind,
                rec.source_date,
                git_dates.get(page_ref),
                rec.volatility,
                json.dumps([t for key, targets in rec.edges
                            if key == "supersedes" for t in targets]),
                st.st_mtime_ns,
                st.st_size,
            ),
        )
        for tag in rec.tags:
            c.execute("INSERT OR IGNORE INTO page_tag(page_ref, tag) VALUES (?, ?)",
                      (page_ref, tag))
        if self.backend == "fts5":
            c.execute(
                "INSERT INTO page_fts(page_ref, title, summary, body) VALUES (?, ?, ?, ?)",
                (page_ref, rec.title, rec.summary, body),
            )
        self._conn.commit()

    def remove_page(self, page_ref: str) -> None:
        c = self._conn
        c.execute("DELETE FROM page WHERE page_ref = ?", (page_ref,))
        c.execute("DELETE FROM page_tag WHERE page_ref = ?", (page_ref,))
        if self.backend == "fts5":
            c.execute("DELETE FROM page_fts WHERE page_ref = ?", (page_ref,))
        self._conn.commit()

    def _recompute_superseded_by(self) -> None:
        """Invert every ``supersedes`` into the targets' ``superseded_by``
        (one per target — the immediate superseder)."""
        c = self._conn
        c.execute("UPDATE page SET superseded_by = NULL")
        for page_ref, raw in c.execute("SELECT page_ref, supersedes FROM page WHERE supersedes IS NOT NULL").fetchall():
            try:
                targets = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            for t in targets:
                c.execute(
                    "UPDATE page SET superseded_by = ? WHERE page_ref = ? AND superseded_by IS NULL",
                    (page_ref, t),
                )

    # -- public read API -------------------------------------------------

    def status(self) -> IndexStatus:
        pages = self._count_pages()
        db_size = self._db_path.stat().st_size if self._db_path.exists() else 0
        schema_row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        return IndexStatus(
            pages=pages,
            db_size_bytes=db_size,
            backend=self.backend,
            schema_version=schema_row[0] if schema_row else "",
            last_scan_duration_ms=0.0,
            pages_scanned=0,
            pages_stale=0,
        )

    def tag_counts(self) -> list[tuple[str, int]]:
        """Every tag with its usage count, most-used first, ties alphabetical.
        Staleness-scans first, like ``search``, so a tag minted by an external
        edit is visible immediately."""
        self._scan()
        return [
            (row[0], row[1])
            for row in self._conn.execute(
                "SELECT tag, COUNT(*) AS n FROM page_tag "
                "GROUP BY tag ORDER BY n DESC, tag ASC"
            )
        ]

    def search(
        self,
        text: str | None = None,
        *,
        tags_all: Sequence[str] = (),
        tags_any: Sequence[str] = (),
        kind: str | Sequence[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        date_field: str = "source_date",
        volatility: Sequence[str] = (),
        fields: Sequence[str] = ("title", "summary", "body"),  # noqa: ARG002
        include_superseded: bool = False,
        raw: bool = False,
        limit: int = 20,
    ) -> list[SearchHit]:
        """The headline API. Staleness-scans, then queries. ``text`` is
        tokenized-and-quoted by default — pass ``raw=True`` to use FTS5's
        own operators (``NEAR``, ``OR``, ``"…"`` etc.)."""
        self._scan()  # correctness path; see module docstring

        if self.backend == "fts5":
            return self._search_fts5(
                text, tags_all, tags_any, kind, since, until,
                date_field, volatility, include_superseded, raw, limit,
            )
        return self._search_re(
            text, tags_all, tags_any, kind, since, until,
            date_field, volatility, include_superseded, raw, limit,
        )

    # -- FTS5 query path -------------------------------------------------

    def _search_fts5(
        self, text, tags_all, tags_any, kind, since, until,
        date_field, volatility, include_superseded, raw, limit,
    ) -> list[SearchHit]:
        match_expr = text if raw else tokenize_query(text or "")
        if not match_expr:
            # Pure-metadata query: no MATCH. The WHERE-only path.
            where, params = self._metadata_where(
                tags_all, tags_any, kind, since, until,
                date_field, volatility, include_superseded,
            )
            sql = (
                "SELECT p.page_ref, 0.0, p.title, p.summary, p.kind, "
                "       p.source_date, p.git_date, p.volatility, p.superseded_by "
                "FROM page p "
                f"WHERE {where} "
                "ORDER BY p.page_ref LIMIT ?"
            )
            rows = self._conn.execute(sql, (*params, limit)).fetchall()
            return [self._row_to_hit(row) for row in rows]

        # Composite (text + metadata) query — the headline case.
        where, params = self._metadata_where(
            tags_all, tags_any, kind, since, until,
            date_field, volatility, include_superseded,
        )
        weights = ",".join(str(w) for w in _FTS5_WEIGHTS)
        sql = (
            "SELECT p.page_ref, bm25(page_fts, " + weights + ") AS raw_score, "
            "       p.title, p.summary, p.kind, "
            "       p.source_date, p.git_date, p.volatility, p.superseded_by, "
            "       snippet(page_fts, 3, '', '', '…', 12) AS snip "
            "FROM page_fts "
            "JOIN page p ON p.page_ref = page_fts.page_ref "
            "WHERE page_fts MATCH ? AND " + where + " "
            "ORDER BY raw_score LIMIT ?"
        )
        rows = self._conn.execute(
            sql, (match_expr, *params, limit)
        ).fetchall()
        return [
            SearchHit(
                page_ref=row[0],
                score=-row[1],  # bm25() returns negative; negate for higher-is-better
                title=row[2],
                summary=row[3],
                tags=self._tags_for(row[0]),
                kind=row[4],
                source_date=row[5],
                git_date=row[6],
                volatility=row[7],
                superseded_by=row[8],
                snippet=row[9],
            )
            for row in rows
        ]

    # -- re-fallback query path ------------------------------------------

    def _search_re(
        self, text, tags_all, tags_any, kind, since, until,
        date_field, volatility, include_superseded, raw, limit,
    ) -> list[SearchHit]:
        # Use the metadata WHERE to cut candidates, then re over body.
        where, params = self._metadata_where(
            tags_all, tags_any, kind, since, until,
            date_field, volatility, include_superseded,
        )
        sql = (
            "SELECT p.page_ref, p.title, p.summary, p.kind, p.source_date, "
            "       p.git_date, p.volatility, p.superseded_by "
            "FROM page p "
            f"WHERE {where}"
        )
        rows = self._conn.execute(sql, params).fetchall()
        if not rows:
            return []

        candidates = {row[0]: row for row in rows}
        pattern = self._compile_re_pattern(text, raw)

        # For the re backend, snippet = the matched line, if any.
        hits: list[SearchHit] = []
        pages_text = self._load_pages_text(candidates.keys())
        for page_ref, row in candidates.items():
            text_blob = pages_text.get(page_ref, "")
            if pattern is not None:
                m = pattern.search(text_blob)
                if not m:
                    continue
                snippet = _line_around(text_blob, m.start())
                score = float(len(pattern.findall(text_blob)))
            else:
                snippet = None
                score = 0.0
            hits.append(SearchHit(
                page_ref=page_ref,
                score=score,
                title=row[1],
                summary=row[2],
                tags=self._tags_for(page_ref),
                kind=row[3],
                source_date=row[4],
                git_date=row[5],
                volatility=row[6],
                superseded_by=row[7],
                snippet=snippet,
            ))

        # Higher-is-better, but limit on the *sorted* slice.
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    def _load_pages_text(self, page_refs) -> dict[str, str]:
        """Read each candidate page's full text off disk, for the ``re``
        fallback. The I/O is the price of not requiring FTS5."""
        out: dict[str, str] = {}
        for page_ref in page_refs:
            path = self.root / page_ref
            if path.exists():
                out[page_ref] = path.read_text(encoding="utf-8")
        return out

    @staticmethod
    def _compile_re_pattern(text: str | None, raw: bool) -> re.Pattern[str] | None:
        if not text:
            return None
        # ``raw`` means the caller wants the literal pattern.
        if raw:
            return re.compile(text, re.IGNORECASE | re.DOTALL)
        terms = text.split()
        if not terms:
            return None
        # Otherwise escape each term to a literal, alternate them, and score
        # by hit count — a rough stand-in for BM25, adequate for a fallback.
        alts = [re.escape(t).replace(r"\"", '"') for t in terms]
        return re.compile("|".join(alts), re.IGNORECASE | re.DOTALL)

    # -- shared metadata WHERE builder -----------------------------------

    def _metadata_where(
        self, tags_all, tags_any, kind, since, until,
        date_field, volatility, include_superseded,
    ):
        clauses: list[str] = []
        params: list = []

        if not include_superseded:
            clauses.append("p.superseded_by IS NULL")

        for tag in tags_all:
            clauses.append(
                "EXISTS (SELECT 1 FROM page_tag t "
                "WHERE t.page_ref = p.page_ref AND t.tag = ?)"
            )
            params.append(tag)

        if tags_any:
            placeholders = ",".join("?" for _ in tags_any)
            clauses.append(
                "EXISTS (SELECT 1 FROM page_tag t "
                f"WHERE t.page_ref = p.page_ref AND t.tag IN ({placeholders}))"
            )
            params.extend(tags_any)

        if kind is not None:
            kinds = [kind] if isinstance(kind, str) else list(kind)
            placeholders = ",".join("?" for _ in kinds)
            clauses.append(f"p.kind IN ({placeholders})")
            params.extend(kinds)

        if date_field not in ("source_date", "git_date"):
            raise ValueError(f"date_field must be 'source_date' or 'git_date', got {date_field!r}")
        if since is not None:
            clauses.append(f"p.{date_field} >= ?")
            params.append(since)
        if until is not None:
            clauses.append(f"p.{date_field} <= ?")
            params.append(until)

        if volatility:
            placeholders = ",".join("?" for _ in volatility)
            clauses.append(f"p.volatility IN ({placeholders})")
            params.extend(volatility)

        where = " AND ".join(clauses) if clauses else "1=1"
        return where, params

    def _tags_for(self, page_ref: str) -> list[str]:
        return [
            row[0] for row in
            self._conn.execute("SELECT tag FROM page_tag WHERE page_ref = ? ORDER BY tag", (page_ref,))
        ]

    @staticmethod
    def _row_to_hit(row) -> SearchHit:
        # Pure-metadata query path returns 9 columns, no snippet.
        return SearchHit(
            page_ref=row[0], score=row[1], title=row[2], summary=row[3],
            tags=[], kind=row[4], source_date=row[5], git_date=row[6],
            volatility=row[7], superseded_by=row[8], snippet=None,
        )

    def close(self) -> None:
        self._conn.close()


# --- per-root cache (ADR-0010) --------------------------------------------

#: Process-lifetime, keyed by resolved root path, no eviction — every
#: entrypoint is a one-shot CLI process (ADR-0001), so this is bounded in
#: practice to a handful of roots for the life of one invocation.
_cache: dict[Path, "SearchIndex"] = {}


def for_root(root: Path | str) -> SearchIndex:
    """The one cached entrypoint: one ``SearchIndex`` per resolved root,
    lifetime of the process. Takes root only — no ``git`` parameter — so a
    test's ``SearchIndex(root, git=fake)`` injection path can never silently
    collide with a cached production instance; it keeps calling
    ``SearchIndex(...)`` directly, uncached, as before."""
    resolved = Path(root).resolve()
    index = _cache.get(resolved)
    if index is None:
        index = SearchIndex(resolved)
        _cache[resolved] = index
    return index


# --- body extraction -----------------------------------------------------


def _body_text(text: str) -> str:
    """Everything after the frontmatter block, verbatim — the FTS5 body column
    indexes the prose, not the YAML."""
    _fm, body, _offset = wikipage.split_frontmatter(text)
    return body


def _line_around(text: str, offset: int, span: int = 80) -> str:
    """Return a one-line snippet around ``offset`` in ``text``."""
    line_start = text.rfind("\n", 0, offset) + 1
    line_end = text.find("\n", offset)
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end].strip()[:span]
