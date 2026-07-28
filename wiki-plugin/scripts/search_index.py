"""``SearchIndex`` — a SQLite FTS5 lexical index for the ``Vault``.

Per [ADR-0006](../docs/adr/0006-stdlib-fts5-not-embeddings.md): a single
gitignored ``.wiki-knowledge/index.db`` at the vault root holds a ``page``
metadata table (kind, tags, source_date, git_date, volatility, supersedes,
superseded_by, mtime_ns, size) plus an FTS5 virtual table over
title/summary/body. The composite query shape — *"pages updated in the last
week, tagged `foo`, containing `bar`"* — is one SQL statement with text as
``MATCH`` and metadata as ``WHERE`` predicates.

Correctness lives in an unconditional ``(mtime_ns, size)`` staleness scan on
every search call, not in ``Vault.write``'s inline update — the inline
update is a latency optimisation only. A capability probe at open time
falls back to a Python ``re`` backend when FTS5 isn't compiled into the
platform's SQLite.

This module knows about ``page_record`` (decoding frontmatter) and ``vault``
(``load_wiki_pages`` for the re-fallback's body read). It does not know about
``Vault`` itself — the index is a stand-alone object that ``Vault`` owns and
proxies ``.search()``/``.reindex()``/``.index_status()`` through.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import page_record
import wikipage


#: Bump when the on-disk schema changes. A mismatch on open triggers a
#: full rebuild — delete-and-rebuild is the migration strategy.
SCHEMA_VERSION = "1"

#: ``bm25()`` column weights for ``rel`` (UNINDEXED), title, summary, body —
#: encodes the retrieval skill's "frontmatter-first" instruction into the
#: ranking rather than leaving it as prose the agent must remember.
_FTS5_WEIGHTS = (0.0, 10.0, 5.0, 1.0)


# --- public types --------------------------------------------------------


@dataclass(frozen=True)
class SearchHit:
    """One search result. ``score`` is higher-is-better (we negate ``bm25()``,
    which returns negative values)."""

    rel: str
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
    last_scan_duration_ms: float
    pages_scanned: int
    pages_stale: int


# --- tokenize-and-phrase-quote (the FTS5 syntax footgun) -----------------


def tokenize_query(text: str) -> str:
    """Split ``text`` on whitespace and phrase-quote each term.

    FTS5's ``MATCH`` is a query language, not a string, and ordinary vault
    vocabulary is a syntax error in it: a hyphenated tag like
    ``wiki-knowledge`` raises ``no such column:
    knowledge``. The default path of :meth:`SearchIndex.search` must
    split-and-quote, with ``raw=True`` as the escape hatch for callers who
    really want ``NEAR()``, ``OR``, and prefix operators.
    """
    if not text:
        return ""
    out: list[str] = []
    for term in text.split():
        out.append('"' + term.replace('"', '\\"') + '"')
    return " ".join(out)


# --- capability probe ----------------------------------------------------


def _probe_fts5(conn: sqlite3.Connection) -> bool:
    """Return ``True`` iff this SQLite can ``CREATE VIRTUAL TABLE … USING
    fts5``. Anything else means a Python-substring fallback."""
    try:
        conn.execute("CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE _fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


# --- git_date extraction -------------------------------------------------


def _compute_git_dates(vault_root: Path) -> dict[str, str]:
    """One ``git log`` pass → ``{wiki-relative-rel: YYYY-MM-DD}``.

    The most recent commit date for each file wins. Returns an empty dict
    when git is unavailable or the directory isn't a git work tree — callers
    treat a missing date as "git_date is None", not as a failure.
    """
    if shutil.which("git") is None:
        return {}
    proc = subprocess.run(
        [
            "git", "-C", str(vault_root),
            "log", "--name-only", "--format=%H|%ad", "--date=short",
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return {}
    dates: dict[str, str] = {}
    current_date: str | None = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            _sha, _, current_date = line.partition("|")
        elif current_date and line.endswith(".md") and line.startswith("wiki/"):
            rel = line[len("wiki/"):]
            if rel not in dates:
                dates[rel] = current_date
    return dates


# --- SearchIndex ---------------------------------------------------------


class SearchIndex:
    """The lexical index for one vault. Opens the db lazily; one
    ``SearchIndex`` per vault, lifetime of the process.

    The unit of address is a wiki-relative rel (e.g. ``concept/foo.md``)
    throughout — the search results, the inline-update API, and the schema
    all use it. ``Vault.search`` re-labels to vault-relative on the way out
    so the agent layer doesn't have to think about either convention.
    """

    def __init__(self, root: Path | str):
        self.root = Path(root)
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
                rel           TEXT PRIMARY KEY,
                title         TEXT,
                summary       TEXT,
                kind          TEXT,
                source_date   TEXT,
                git_date      TEXT,
                volatility    TEXT,
                supersedes    TEXT,    -- JSON array of wiki-relative rels
                superseded_by TEXT,    -- single wiki-relative rel, or NULL
                mtime_ns      INTEGER,
                size          INTEGER
            );
            CREATE TABLE IF NOT EXISTS page_tag (
                rel TEXT,
                tag TEXT,
                PRIMARY KEY (rel, tag)
            );
            CREATE INDEX IF NOT EXISTS ix_page_tag_tag ON page_tag(tag);
            """
        )
        if self.backend == "fts5":
            c.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS page_fts USING fts5(
                    rel UNINDEXED, title, summary, body,
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
        """Wipe and re-index. Delete-and-rebuild *is* the migration strategy
        — no incremental migrations, ever."""
        c = self._conn
        for table in ("page_tag", "page"):
            c.execute(f"DELETE FROM {table}")
        if self.backend == "fts5":
            c.execute("DELETE FROM page_fts")
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
        """Staleness scan: walk ``wiki/**``; upsert diffs. This is the
        *correctness* path — every search call runs it before querying.

        Each upsert commits immediately (its own implicit transaction);
        a mid-scan crash leaves the index partially updated, and the next
        scan reconciles. This is fine because the file system is the
        source of truth — there is no "all-or-nothing" semantic the index
        needs to preserve against a crash, only a "the next scan will fix
        it" semantic, which is exactly what partial commits give us.
        """
        start = time.monotonic()
        rows = self._conn.execute(
            "SELECT rel, mtime_ns, size FROM page"
        ).fetchall()
        indexed: dict[str, tuple[int, int]] = {
            rel: (mtime_ns, size) for rel, mtime_ns, size in rows
        }
        seen: set[str] = set()
        stats = IndexStats()
        for path in (self.root / "wiki").rglob("*.md"):
            rel = path.relative_to(self.root / "wiki").as_posix()
            if rel == "_index.md":
                continue
            seen.add(rel)
            st = path.stat()
            mtime_ns, size = st.st_mtime_ns, st.st_size
            prev = indexed.get(rel)
            if prev is None:
                self.upsert_page(rel, path.read_text(encoding="utf-8"))
                stats.inserted += 1
            elif prev != (mtime_ns, size):
                self.upsert_page(rel, path.read_text(encoding="utf-8"))
                stats.updated += 1

        for rel in indexed.keys() - seen:
            self.remove_page(rel)
            stats.removed += 1

        self._recompute_superseded_by()
        stats.pages = self._count_pages()
        stats.duration_ms = (time.monotonic() - start) * 1000
        return stats

    def _reindex_walk(self) -> IndexStats:
        """Full walk, no diff — every file is upserted. Used by the schema-
        version-mismatch rebuild path. Per-page commits, same rationale as
        :meth:`_scan`."""
        start = time.monotonic()
        stats = IndexStats()
        for path in (self.root / "wiki").rglob("*.md"):
            rel = path.relative_to(self.root / "wiki").as_posix()
            if rel == "_index.md":
                continue
            self.upsert_page(rel, path.read_text(encoding="utf-8"))
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

    def upsert_page(self, rel: str, text: str) -> None:
        """Index/replace one page. ``rel`` is wiki-relative. The file at
        ``self.root / "wiki" / rel`` must exist — the inline update needs
        ``(mtime_ns, size)`` for the next scan to recognise this row as
        fresh, and the staleness check uses those stat values verbatim."""
        rec = page_record.page_record(rel, text)
        path = self.root / "wiki" / rel
        st = path.stat()
        git_dates = _compute_git_dates(self.root)
        body = _body_text(text)

        c = self._conn
        c.execute("DELETE FROM page WHERE rel = ?", (rel,))
        c.execute("DELETE FROM page_tag WHERE rel = ?", (rel,))
        if self.backend == "fts5":
            c.execute("DELETE FROM page_fts WHERE rel = ?", (rel,))

        c.execute(
            "INSERT INTO page(rel, title, summary, kind, source_date, "
            "git_date, volatility, supersedes, superseded_by, mtime_ns, size) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
            (
                rel,
                rec.title,
                rec.summary,
                rec.kind,
                rec.source_date,
                git_dates.get(rel),
                rec.volatility,
                json.dumps([t for key, targets in rec.edges
                            if key == "supersedes" for t in targets]),
                st.st_mtime_ns,
                st.st_size,
            ),
        )
        for tag in rec.tags:
            c.execute("INSERT OR IGNORE INTO page_tag(rel, tag) VALUES (?, ?)",
                      (rel, tag))
        if self.backend == "fts5":
            c.execute(
                "INSERT INTO page_fts(rel, title, summary, body) VALUES (?, ?, ?, ?)",
                (rel, rec.title, rec.summary, body),
            )
        self._conn.commit()

    def remove_page(self, rel: str) -> None:
        c = self._conn
        c.execute("DELETE FROM page WHERE rel = ?", (rel,))
        c.execute("DELETE FROM page_tag WHERE rel = ?", (rel,))
        if self.backend == "fts5":
            c.execute("DELETE FROM page_fts WHERE rel = ?", (rel,))
        self._conn.commit()

    def _recompute_superseded_by(self) -> None:
        """Invert every page's ``supersedes`` into the targets'
        ``superseded_by`` (one per target — the immediate superseder)."""
        c = self._conn
        c.execute("UPDATE page SET superseded_by = NULL")
        for rel, raw in c.execute("SELECT rel, supersedes FROM page WHERE supersedes IS NOT NULL").fetchall():
            try:
                targets = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            for t in targets:
                c.execute(
                    "UPDATE page SET superseded_by = ? WHERE rel = ? AND superseded_by IS NULL",
                    (rel, t),
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
        # Correctness: scan first so external edits / git pull / Obsidian
        # are caught. Inline updates (Vault.write et al.) are a latency
        # optimisation, never the path that keeps the index right.
        self._scan()

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
                "SELECT p.rel, 0.0, p.title, p.summary, p.kind, "
                "       p.source_date, p.git_date, p.volatility, p.superseded_by "
                "FROM page p "
                f"WHERE {where} "
                "ORDER BY p.rel LIMIT ?"
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
            "SELECT p.rel, bm25(page_fts, " + weights + ") AS raw_score, "
            "       p.title, p.summary, p.kind, "
            "       p.source_date, p.git_date, p.volatility, p.superseded_by, "
            "       snippet(page_fts, 3, '', '', '…', 12) AS snip "
            "FROM page_fts "
            "JOIN page p ON p.rel = page_fts.rel "
            "WHERE page_fts MATCH ? AND " + where + " "
            "ORDER BY raw_score LIMIT ?"
        )
        rows = self._conn.execute(
            sql, (match_expr, *params, limit)
        ).fetchall()
        return [
            SearchHit(
                rel=row[0],
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
            "SELECT p.rel, p.title, p.summary, p.kind, p.source_date, "
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
        for rel, row in candidates.items():
            text_blob = pages_text.get(rel, "")
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
                rel=rel,
                score=score,
                title=row[1],
                summary=row[2],
                tags=self._tags_for(rel),
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

    def _load_pages_text(self, rels) -> dict[str, str]:
        """Read each page's full text off disk for the re-fallback search.
        I/O is the cost; the trade is no FTS5 dependency."""
        out: dict[str, str] = {}
        for rel in rels:
            path = self.root / "wiki" / rel
            if path.exists():
                out[rel] = path.read_text(encoding="utf-8")
        return out

    @staticmethod
    def _compile_re_pattern(text: str | None, raw: bool) -> re.Pattern[str] | None:
        if not text:
            return None
        # In ``raw`` mode the caller is asking for the literal pattern; in
        # default mode we phrase-quote the same way FTS5 would, so a
        # hyphenated term doesn't compile to a regex character class.
        if raw:
            return re.compile(text, re.IGNORECASE | re.DOTALL)
        terms = text.split()
        if not terms:
            return None
        # Each quoted term is treated as a literal phrase. We compile one
        # alternation and score by hit count; close enough to the FTS5
        # ranking for the fallback to be useful rather than great.
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
                "WHERE t.rel = p.rel AND t.tag = ?)"
            )
            params.append(tag)

        if tags_any:
            placeholders = ",".join("?" for _ in tags_any)
            clauses.append(
                "EXISTS (SELECT 1 FROM page_tag t "
                f"WHERE t.rel = p.rel AND t.tag IN ({placeholders}))"
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

    def _tags_for(self, rel: str) -> list[str]:
        return [
            row[0] for row in
            self._conn.execute("SELECT tag FROM page_tag WHERE rel = ? ORDER BY tag", (rel,))
        ]

    @staticmethod
    def _row_to_hit(row) -> SearchHit:
        # Pure-metadata query path returns 9 columns, no snippet.
        return SearchHit(
            rel=row[0], score=row[1], title=row[2], summary=row[3],
            tags=[], kind=row[4], source_date=row[5], git_date=row[6],
            volatility=row[7], superseded_by=row[8], snippet=None,
        )

    def close(self) -> None:
        self._conn.close()


# --- body extraction -----------------------------------------------------


def _body_text(text: str) -> str:
    """The body of a page — everything after the frontmatter block, verbatim.

    The FTS5 body column wants the prose, not the YAML. ``wikipage.split_frontmatter``
    already gives us (frontmatter, body, offset); we just take the body.
    """
    _fm, body, _offset = wikipage.split_frontmatter(text)
    return body


def _line_around(text: str, offset: int, span: int = 80) -> str:
    """Return a one-line snippet around ``offset`` in ``text``."""
    line_start = text.rfind("\n", 0, offset) + 1
    line_end = text.find("\n", offset)
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end].strip()[:span]
