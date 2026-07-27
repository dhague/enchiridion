# Text search for the `Vault` class

Research for [#36](https://github.com/dhague/enchiridion/issues/36). The ask: stop grepping the vault with regexes that have to work around frontmatter, and give `Vault` a real search API that composes structured metadata predicates with full-text matching — *"pages updated in the last week, tagged `foo`, containing `bar`"*.

Sources are primary: SQLite's own FTS5 documentation, PyPI release metadata, project READMEs, and Debian's packaging rules. Claims are labelled **[verified]** where read from a primary source, **[measured]** where produced by a throwaway prototype run on this machine against the real dogfooding vault, and **[inferred]** where reasoned from those.

---

## Verdict

**Use SQLite FTS5 through the stdlib `sqlite3` module. Add no dependency.**

A single `.wiki-knowledge/index.db` at the vault root holds two things: an ordinary `page` metadata table (one row per page — kind, tags, `source_date`, `volatility`, `superseded_by`, plus `mtime_ns`/`size` for staleness) and an FTS5 virtual table over title/summary/body. The ticket's query shape is then one ordinary SQL statement with a `MATCH` in the `WHERE` clause — **[measured]** 7 ms at 2000 realistic pages.

**Runner-up: `tantivy` (the Rust engine's Python bindings).** It is genuinely better software with genuinely better ranking, and its wheel coverage is excellent — but it is a compiled dependency bought to solve a problem that a stdlib module already solves at this corpus size. Revisit only if FTS5's ranking is measured to be the thing failing.

**The index is gitignored and rebuilt, never committed.** **Complement `_index.md`, do not replace it.** **Staleness is detected by an `(mtime_ns, size)` scan on every search** — **[measured]** 53 ms over 2000 files, cheap enough to run unconditionally and never think about cache invalidation again.

**ADR-0002 is respected, not stretched.** BM25 over an inverted index is lexical retrieval. There is no embedding model, no vector store, no similarity threshold. What this proposal actually does is *buy ADR-0002 more runway*: that ADR's own stated revisit trigger is "`_index.md` no longer fits a single read", and a metadata+FTS filter is precisely the non-embedding way to survive that. It is worth recording as its own ADR (`0006`) rather than amending `0002`.

---

## 1. The corpus we are actually sizing for

**[measured]** The live dogfooding vault today: **25 pages, 76 KiB** of markdown under `wiki/`, plus 16 raw artifacts. The golden vault is specced at 15–30 pages. The ticket asks about "a few hundred to a few thousand".

This matters more than any library comparison, because it means **the honest answer at today's scale is "you don't need an index yet"** — 25 pages fit in `_index.md` and `grep` returns in single-digit milliseconds. Everything below is sizing for the corpus this vault will have in a year of `/save-conversation`, not the one it has now. That is a legitimate thing to build for, but the recommendation should be the one with the lowest carrying cost while it is over-engineered, and that argues hard for "no new dependency".

---

## 2. SQLite FTS5 — the recommendation

### Availability: the thing to verify, verified

**[verified, measured]** On this machine — Windows 11, CPython 3.14.6 from python.org, `sqlite3` module linked against SQLite 3.50.4 — `PRAGMA compile_options` reports:

```
ENABLE_FTS3
ENABLE_FTS4
ENABLE_FTS5
ENABLE_MATH_FUNCTIONS
ENABLE_RTREE
```

and `CREATE VIRTUAL TABLE t USING fts5(body)` succeeds, as do the `porter unicode61` and `trigram` tokenizers. **This is the platform the plugin's author is on, and it is the platform most likely to be missing an optional module.** It isn't.

**[verified]** SQLite's [FTS5 documentation](https://www.sqlite.org/fts5.html) states the compilation rule exactly:

> As of [version 3.9.0](https://www.sqlite.org/releaselog/3_9_0.html) (2015-10-14), FTS5 is included as part of the SQLite amalgamation. If using the canonical source tree, FTS5 is enabled by specifying the "--enable-fts5" option when running the configure script. (FTS5 is currently disabled by default for the source-tree configure script and **enabled by default for the amalgamation configure script**, but these defaults might change in the future.)

**[verified]** Debian's [`debian/rules` for the `sqlite3` source package](https://sources.debian.org/src/sqlite3/latest/debian/rules/) passes `--enable-fts4 \` and `--enable-fts5 \` to configure — so every Debian/Ubuntu-derived `libsqlite3`, which is what a distro CPython links against, has it.

**[inferred]** The realistic gap is narrow: a hand-rolled CPython built against a hand-rolled SQLite with FTS5 off, or an unusual embedded/minimal distro. That is not zero, so the mitigation is a **three-line capability probe** at index-open time — `try: CREATE VIRTUAL TABLE …fts5; except OperationalError:` — which falls back to the metadata table plus a Python substring scan of bodies. Degraded ranking, identical API, no crash. **Not verified**: FTS5 presence in the python.org macOS installer's bundled SQLite and in Homebrew's — I could not test either from here, and would not assert it. The probe makes it not matter.

### Schema shape for the ticket's query

The composite query works because FTS5 tables are ordinary SQL tables as far as joins are concerned. **[verified]** from the FTS5 docs, and **[measured]** by running it:

```sql
CREATE TABLE page (
  rel           TEXT PRIMARY KEY,       -- vault-relative path, e.g. wiki/concept/foo.md
  title         TEXT,
  summary       TEXT,
  kind          TEXT,                   -- concept | entity | source | synthesis
  source_date   TEXT,                   -- ISO-8601, sorts lexically
  git_date      TEXT,                   -- transaction time, batch-filled from git log
  volatility    TEXT,
  superseded_by TEXT,                   -- rel of the page whose `supersedes` names this one
  mtime_ns      INTEGER,                -- staleness detection
  size          INTEGER,
  UNIQUE(rel)
);
CREATE TABLE page_tag (rel TEXT, tag TEXT, PRIMARY KEY (rel, tag));
CREATE TABLE page_edge (src TEXT, kind TEXT, dst TEXT);   -- the five typed edges + supersedes
CREATE INDEX ix_tag ON page_tag(tag);

CREATE VIRTUAL TABLE page_fts USING fts5(
  rel UNINDEXED, title, summary, body,
  tokenize = 'porter unicode61'
);
```

and the ticket's exact query shape:

```sql
SELECT p.rel, bm25(page_fts, 0.0, 10.0, 5.0, 1.0) AS score
FROM page_fts
JOIN page p ON p.rel = page_fts.rel
WHERE page_fts MATCH :query               -- "bar"
  AND p.git_date >= :since                -- "updated in the last week"
  AND p.superseded_by IS NULL             -- never cite a superseded page
  AND EXISTS (SELECT 1 FROM page_tag t
              WHERE t.rel = p.rel AND t.tag = :tag)   -- "tagged foo"
ORDER BY score
LIMIT 20;
```

Three details worth pinning:

- **The `bm25()` weights are the design lever, and they're free.** **[verified]** `bm25()` takes one weight per column; `rel` is `UNINDEXED` so it takes 0.0. Weighting `title` 10× and `summary` 5× over `body` encodes the retrieval skill's existing "frontmatter-first" instruction *into the ranking* rather than leaving it as prose the agent must remember. **[verified]** Lower scores are better matches (`bm25()` returns negated values), hence `ORDER BY score` ascending — a classic off-by-sign bug worth writing a test for.
- **[verified]** `ORDER BY rank` (the special `rank` column, defaulting to unweighted `bm25()`) is the faster form when no custom weights are needed; the docs recommend it over `ORDER BY bm25(ft)`.
- **`porter unicode61`** gives stemming ("ingest" matches "ingestion", "ingested") plus diacritic folding. **[verified]** `unicode61` is the default tokenizer and removes diacritics by default; `porter` wraps another tokenizer's output. **[measured]** `MATCH 'ingest'` against the real vault ranked `ingestion-md-hints-for-raw-folders-design-decision.md` first — the stemmer is doing real work.

### Index freshness on a single page write

Because we want the body text available for `snippet()`/`highlight()`, use a **regular (content-storing) FTS5 table**, not external-content or contentless. That doubles storage and buys away all the trigger machinery: a page write is

```sql
DELETE FROM page_fts WHERE rel = ?;   INSERT INTO page_fts(rel,title,summary,body) VALUES (?,?,?,?);
```

**[measured]** 2.8 ms at 500 pages, 7.5 ms at 2000, 17 ms at 5000, including the `COMMIT`. That is well inside the noise of the `git commit` that follows every ingestion, so hooking it into `Vault.write()` costs nothing perceptible.

**[verified]** The alternatives and why they lose here: **external content tables** (`content=`) require hand-written triggers on a source table we don't have (the source of truth is the filesystem, not a SQL table) and the docs warn that inconsistency "causes unpredictable results". **Contentless tables** (`content=''`) can't return column values at all, killing `snippet()`; **contentless-delete** (SQLite ≥ 3.43.0) restores `DELETE` but still not column reads. The storage saving isn't worth losing snippets at this scale.

**[verified]** Housekeeping commands worth knowing: `INSERT INTO page_fts(page_fts) VALUES('rebuild')` (reindex from content), `'optimize'` (merge b-trees to minimum size / fastest form — worth running after a full rebuild), and `'integrity-check'` (verify internal consistency; a good `--verify` flag for the CLI).

### Size, build time, latency

**[measured]** Prototype on this machine. "Realistic" replicates the dogfooding vault's actual prose and frontmatter to N pages; "synthetic" is a worst case of 600 unique-ish random tokens per page (a much larger vocabulary than real prose, so a larger index).

| corpus | markdown | `index.db` | ratio | full build | composite query | 1-page update |
| --- | --- | --- | --- | --- | --- | --- |
| real vault (25 pages) | 76 KiB | 216 KiB | 2.8× | 30 ms | 0.06 ms | — |
| realistic 500 | 1.48 MiB | 3.10 MiB | 2.10× | 0.50 s | 1.6 ms | — |
| realistic 2000 | 5.91 MiB | 14.85 MiB | 2.51× | 1.93 s | 7.1 ms | — |
| synthetic 500 | 2.65 MiB | 6.61 MiB | 2.49× | 0.34 s | 0.39 ms | 2.8 ms |
| synthetic 2000 | 10.62 MiB | 23.46 MiB | 2.21× | 1.42 s | 2.8 ms | 7.5 ms |
| synthetic 5000 | 26.54 MiB | 54.55 MiB | 2.06× | 3.56 s | 7.0 ms | 17.0 ms |

Read off the numbers that matter:

- **Index is ~2–2.5× the markdown it indexes.** A 2000-page vault costs ~15 MB. That is the number the git question turns on (§6).
- **A full cold rebuild of a 2000-page vault is ~2 seconds.** This is the single most important number in the document: it means the "index is missing" case is *not a crisis*, which is what makes gitignoring it obviously correct.
- **Query latency is 1–7 ms.** Not a consideration.
- **[verified, measured]** `bm25()` costs are why the `columnsize` option exists — leaving it at its default (1) is right here, since bm25 needs it.

### The FTS5 query-syntax footgun — do not skip this

**[measured]** FTS5's `MATCH` argument is a query *language*, not a string, and ordinary vault vocabulary is a syntax error in it:

```
'wiki-knowledge'   -> OperationalError: no such column: knowledge
'foo:'             -> OperationalError: no such column: foo
'AND'              -> OperationalError: fts5: syntax error near "AND"
'x AND'            -> OperationalError: fts5: syntax error near ""
'"wiki-knowledge"' -> 1 hit          (phrase-quoted: fine)
'a*'               -> 1 hit          (prefix: fine)
```

A hyphenated tag — and this vault's tags are *routinely* hyphenated (`wiki-knowledge-plugin`, `example-of`) — **crashes a naive `MATCH`**. A Haiku-model agent shelling out with an arbitrary user phrase will hit this on day one.

**Therefore the API must tokenize-and-quote by default**: split the caller's terms and wrap each in double quotes before building the `MATCH` expression, with an explicit `--raw`/`raw=True` escape hatch for callers who really want `NEAR()`, `OR`, and prefix operators. This is a correctness requirement on the interface, not a nicety, and it is the sort of thing that gets discovered in production if it isn't written down now.

---

## 3. Tantivy via `tantivy-py` — the runner-up

**[verified]** PyPI package name is **`tantivy`** (the repo is [`quickwit-oss/tantivy-py`](https://github.com/quickwit-oss/tantivy-py); `tantivy-py` is a different, stale PyPI name). MIT licensed. Latest release **0.26.0, uploaded 2026-04-29**, `requires_python >= 3.10`.

**[verified]** Wheel coverage for 0.26.0 is genuinely excellent — 30 wheels plus an sdist, covering **cp310, cp311, cp312, cp313, cp313t (free-threaded), and cp314** across **win_amd64**, macOS universal2 + x86_64, and manylinux_2_17 x86_64 + aarch64. Windows and Python 3.14 are both covered. This is not the weak point people assume it is.

The weak points are elsewhere:

- **[verified]** The README states: *"If no binary wheel is present for your operating system the bindings will be built from source, this means that Rust needs to be installed before building can succeed."* The wheel matrix is broad but not total — notably **no `win_arm64`, no musllinux, no aarch64 macOS-only tag beyond universal2**. A user on Windows ARM or Alpine gets "install Rust" as the failure mode for a Claude Code plugin. **[verified]** There is prior art for exactly this pain: [tantivy-py#371](https://github.com/quickwit-oss/tantivy-py/issues/371) is a user unable to fetch a wheel for CPython 3.13 during the gap before 3.13 wheels shipped. Every new CPython release re-opens that gap.
- **Metadata filtering composes worse.** Tantivy has a schema with typed fields and supports boolean/range queries over them, so `tag:foo AND date:[…]` is expressible — but the tag/date filters live *inside* the search engine's own query language, not in SQL. Every filter the vault ever wants (tags, kind, volatility, `superseded_by`, edges) has to be modelled as a tantivy field and re-expressed in tantivy query syntax, and anything relational — "pages that `refines` a page tagged X" — is not expressible at all. In the SQLite design that is a two-table join.
- **Ranking is better, and it doesn't matter yet.** Tantivy is a Lucene-class engine: better tokenization, faceting, proper phrase/proximity scoring. At 2000 pages of a single author's prose, FTS5 BM25 is not the bottleneck; the bottleneck is whether the right page was written down at all.
- **[inferred]** Two index formats to maintain, versioned by a Rust crate we don't control, in a directory that must also be gitignored — same operational burden as SQLite, plus a dependency.

**Verdict: keep on the shelf.** The trigger to revisit is a *measured* ranking failure — a set of real questions where FTS5 returns the right page outside the top ~10 — not a size threshold. If that day comes, the metadata table stays in SQLite and only the text half moves.

---

## 4. Whoosh / whoosh-reloaded — ruled out

**[verified]** The original [Whoosh](https://pypi.org/project/Whoosh/) is unmaintained. The fork, [`whoosh-reloaded`](https://pypi.org/project/whoosh-reloaded/) by Sygil-Dev, is at **2.7.5 (2024-02-02)** and declares Python 2.7 and 3.4–3.12.

**[verified]** The fork's own [README](https://github.com/Sygil-Dev/whoosh-reloaded) now carries the notice: **"This repository (whoosh-reloaded) is NO LONGER MAINTAINED."** Three open issues; no release in over two years.

That closes it. Pure-Python and zero-compilation was the entire case for Whoosh, and an unmaintained fork of an unmaintained library that has never declared support for Python 3.13 or 3.14 is a worse bet than the stdlib. **[inferred]** It will probably keep working for years — it is pure Python with no C extensions — but shipping a plugin on it means owning its Python-version compatibility ourselves.

---

## 5. `ripgrep`-plus-structure — the serious baseline

Evaluated properly, because it is the option with zero new machinery and it very nearly wins.

The shape: `Vault.search()` shells out to `rg --json` (or uses Python's `re` over `load_wiki_pages()`), then post-filters hits in Python using frontmatter already parsed by `WikiPage`. Metadata filtering happens in Python, text matching happens in the grep.

**[measured]** `grep -ril` over 2000 replicated pages on this machine: **162 ms**, returning 1680 matching files. Fast enough. But that result is also the argument against it: **1680 unranked paths is not an answer**, it is the whole vault back again. That is exactly the failure the retrieval skill's *"never grep a schema word"* hand-run rule exists to work around by hand.

Concrete problems, in order of severity:

1. **No ranking.** `grep` gives a set, not an ordering. The retrieval skill's budget ("~12 pages read in full") presupposes something ranked the candidates. Today the Haiku agent does that ranking itself by reading `_index.md` summaries — which works at 25 pages and does not survive a 500-hit result set. BM25 is the missing piece, and grep structurally cannot provide it.
2. **[verified, measured] `rg` is not guaranteed to be on PATH.** `shutil.which("rg")` returns `None` on this machine — Claude Code bundles ripgrep for its own `Grep` tool, but that binary is not exposed to subprocesses the plugin spawns. So `Vault.search()` shelling to `rg` either adds an undeclared external-binary dependency (worse than a wheel: no `pip` to install it) or falls back to `grep`, which is absent on stock Windows. Python `re` over `load_wiki_pages()` avoids that but re-reads and re-parses every page per query.
3. **No stemming, no term weighting.** "ingest" doesn't find "ingestion" without the author writing the regex `ingest\w*`, and a title hit ranks identically to a body hit.
4. **It does solve the frontmatter problem.** Credit where due: `WikiPage.split_frontmatter` already exists, so "grep the body only, filter on parsed frontmatter" is achievable *today* in about 40 lines with no dependency and no index, and it fixes the ticket's literal opening complaint.

**Verdict: this is the right *interim* shape and the wrong *destination*.** If #36 needs to ship small, ship `Vault.search()` with a Python-`re` backend behind the exact API in §7, then swap the backend to FTS5 without touching a caller. That is a real, defensible plan. But specifying the API against the FTS5 design from the start is what makes the swap free, so **the API must be designed for FTS5 even if the first implementation isn't**.

---

## 6. Other options, briefly

- **`bm25s`** ([xhluca/bm25s](https://github.com/xhluca/bm25s)) — fast BM25 over scipy sparse matrices. Real, actively developed, and genuinely good at what it does, but it is an **in-memory ranker with a numpy/scipy dependency**, not a persistent index with metadata filtering. Pulling scipy into a Claude Code plugin to rank 2000 short documents is a poor trade. **Not recommended.**
- **DuckDB's `fts` extension** — capable, but DuckDB is a much larger dependency than the problem, and its FTS extension is loaded at runtime (network fetch on first use in some configurations). **Not recommended.**
- **`sqlite-vec` / `sqlite-vss` / any embedding store** — **out of scope by [ADR-0002](../adr/0002-no-embeddings-agent-read-retrieval.md)**, noted here only so the next reader doesn't re-derive it. Nothing in this research changes ADR-0002's reasoning; if anything it strengthens it, because a lexical index is the cheaper answer to the scale problem embeddings were being held in reserve for.
- **`sqlite-utils`** (Simon Willison) — a convenience wrapper over exactly the FTS5 pattern recommended here. Worth reading as a reference implementation; not worth adding as a dependency when the underlying calls are a dozen lines of `sqlite3`.

---

## 7. Decision answers

### Is the index committed to the vault git repo, or gitignored?

**Gitignored, at `.wiki-knowledge/index.db`, alongside the already-gitignored `.claude/wiki-knowledge/sessions/`.** Not close.

The argument for committing is "no cold rebuild on a fresh clone". **[measured]** that cold rebuild is **2 seconds at 2000 pages**. Weigh that against what committing costs:

- **Binary churn in history, forever.** A ~15 MB binary file that changes on *every ingestion* — and SQLite b-tree pages reshuffle, so git's delta compression does poorly. A year of daily ingestion is gigabytes of history for a vault whose markdown is 6 MB. The vault is synced across machines; that history is cloned every time.
- **Guaranteed merge conflicts with no resolution.** Two machines ingesting independently produce two divergent binary blobs. There is no merge driver for an FTS5 index. Every pull becomes "take one side and rebuild" — i.e. the rebuild happens anyway, plus a conflict.
- **It would be committed *stale*.** `commit.py` stages an explicit path list; the index would be written before the commit and immediately invalidated by anything the commit itself changed.
- **It violates the repo's own derived-data convention.** `build_index.py`'s docstring: *"Derived data — always regenerated, never hand-edited or trusted as a cache."* `_index.md` is committed because it is small, text, diffable, and readable. An index.db is none of those four.

**Two operational riders**, both specific to this vault:

- **[inferred]** The vault sits in a **Resilio Sync** folder. Resilio syncs whole files and knows nothing about SQLite. A live `.db` plus WAL sidecars in a synced folder is the textbook database-corruption scenario. So: **do not enable WAL** (leave `journal_mode` at the default rollback journal, so no long-lived `-wal`/`-shm` sidecars exist between transactions), and **document adding `.wiki-knowledge/` to Resilio's ignore list**. If sync churn still proves troublesome, the fallback — not the default — is to move the index out of the vault entirely into a per-machine cache keyed by a hash of the vault path.
- **Obsidian and other editors** must never see it: `.wiki-knowledge/` is dot-prefixed, which Obsidian ignores by default, same as the existing `.obsidian/` entry.

### How does the index stay correct when the vault changes outside `Vault`?

**A `(mtime_ns, size)` scan of `wiki/**/*.md` on every search call.** No git hooks, no filesystem watchers, no daemon.

**[measured]** over 2000 files on this machine: **`stat` scan = 53 ms; read-and-sha256 = 995 ms.** A 19× difference, and 53 ms is inside the latency budget of a search an agent is about to spend seconds reading the results of. So run it unconditionally and stop reasoning about invalidation.

The mechanism:

1. `SELECT rel, mtime_ns, size FROM page` — one query, the whole index's view of the world.
2. Walk `wiki/**/*.md`, `os.stat` each.
3. Reindex the symmetric difference: files whose `(mtime_ns, size)` differs, files present on disk but not in `page`, and `page` rows with no file (delete those rows).
4. Recompute the derived `superseded_by` column for any page touched (it is a function of *other* pages' `supersedes`).

Why this catches every case the ticket asks about:

- **`git pull` / `git checkout` / branch switch** — git rewrites file content and therefore sets a fresh mtime on every file it touches. Detected.
- **Manual edit / Obsidian / external editor** — mtime changes. Detected.
- **`Vault.write()`** — updates the index inline *and* stamps the new `(mtime_ns, size)`, so the next scan sees no drift and does nothing. The inline update is a latency optimisation, **not a correctness requirement** — which is the important property: the index can never be wrong because someone forgot to call the right method.
- **A file restored with byte-identical content and byte-identical `st_mtime_ns`** — undetected, and correct to ignore, since the content is identical.

**[inferred]** `mtime_ns` is nanosecond-resolution on NTFS, APFS and ext4 alike, so the classic "same-second modification" mtime race doesn't apply; adding `size` closes it further. A `--rehash` flag doing full sha256 verification is the escape hatch for the paranoid, and `INSERT INTO page_fts(page_fts) VALUES('integrity-check')` is the escape hatch for a suspected-corrupt index.

**Schema versioning:** a `meta` table holding a schema version and the tokenizer string. A mismatch (plugin upgrade changed the schema or the tokenizer) triggers a full rebuild — 2 seconds, so no migration machinery is ever needed. Delete-and-rebuild is the migration strategy, deliberately.

**A rejected alternative worth recording:** keying staleness on `git rev-parse HEAD` plus `git status --porcelain`. It's cheaper in principle, but it is wrong in the case that matters most — an uncommitted Obsidian edit shows in `git status` but gives you no way to know *which* index rows to refresh without the file scan you were trying to avoid. And 53 ms didn't need optimising.

### Proposed `Vault` API surface

Sketch only. The mechanics live in a new `scripts/search_index.py`; `Vault` is the facade, so callers never see SQL and the backend can start as `re`-over-`load_wiki_pages()` (§5) and become FTS5 without a caller changing.

```python
@dataclass(frozen=True)
class SearchHit:
    rel: str                 # vault-relative path
    score: float             # higher is better — sign-normalised from bm25()
    title: str
    summary: str
    tags: list[str]
    kind: str
    source_date: str         # valid time, from frontmatter
    git_date: str | None     # transaction time, from git log
    volatility: str
    superseded_by: str | None
    snippet: str | None      # fts5 snippet(), when the backend can produce one

class Vault:
    def search(
        self,
        text: str | None = None,        # free text; tokenized + phrase-quoted unless raw=True
        *,
        tags_all: Sequence[str] = (),   # AND over tags
        tags_any: Sequence[str] = (),   # OR over tags
        kind: str | Sequence[str] | None = None,
        since: str | None = None,       # ISO date
        until: str | None = None,
        date_field: Literal["source_date", "git_date"] = "source_date",
        volatility: Sequence[str] = (),
        fields: Sequence[str] = ("title", "summary", "body"),
        include_superseded: bool = False,   # default False — mechanizes the hand-run rule
        raw: bool = False,              # pass `text` through as a literal FTS5 expression
        limit: int = 20,
    ) -> list[SearchHit]: ...

    def reindex(self, *, full: bool = False) -> IndexStats: ...
    def index_status(self) -> IndexStatus: ...   # page count, staleness, db size, backend in use
```

Points the sketch is making on purpose:

- **`text` is optional.** `search(tags_all=["foo"], since="2026-07-20")` is a pure-metadata query and must not require a text term.
- **`include_superseded=False` is the default**, turning `wiki-retrieval`'s third hand-run rule ("never cite a superseded page") from prose the agent must remember into a filter it must opt out of. This is the single highest-value thing the index buys, and it is directly relevant to [#17](https://github.com/dhague/enchiridion/issues/17).
- **`date_field` is explicit**, because this vault has a bitemporal model and "updated in the last week" is ambiguous between `source_date` (valid time) and git (transaction time). Forcing the caller to name it is the correct interface, and it also mechanizes the second hand-run rule — `git_date` batch-filled from one `git log --name-only --format=%H|%ad` pass means the agent stops shelling `git log -1` per page.
- **`raw=False` is the default** because of the §2 syntax footgun. Hyphenated tags must not crash a search.
- **`search()` internally calls the staleness scan.** No `refresh()` in the caller's face.
- **`Vault.write()` / `set()` / `merge()` / `move_page()` update the touched rows inline** — the ticket's "when Vault adds/updates a page it should also trigger the index update" — as an optimisation over the scan, not as the correctness path.

**CLI**, since agents and skills shell out (`scripts/search.py`, following the existing one-script-one-CLI convention):

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/search.py" "connection pooling" \
    --tag database --since 2026-07-20 --date-field git_date \
    --kind concept --limit 10 --json
python "${CLAUDE_PLUGIN_ROOT}/scripts/search.py" --reindex [--full]
python "${CLAUDE_PLUGIN_ROOT}/scripts/search.py" --status
```

Default output is a compact one-line-per-hit table (path, score, title, tags, dates, volatility) that a Haiku agent can read directly; `--json` emits `SearchHit` records for programmatic use. The vault root resolves through the existing `vault.resolve_vault_root()`, so both deployment modes work unchanged ([ADR-0004](../adr/0004-deployment-modes-and-vault-root-resolution.md)).

### How does this relate to `build_index.py` / `_index.md`?

**Complement. `_index.md` stays, unchanged, committed.** Decisively — they are different artifacts for different readers:

| | `_index.md` | `index.db` |
| --- | --- | --- |
| reader | human + agent, read whole | `Vault.search()` only |
| form | markdown table, git-diffable | binary, gitignored |
| purpose | *the map* — the cheap whole-vault view | *the filter* — narrow a large vault to candidates |
| ADR-0002 role | is the mechanism ADR-0002 names | is what keeps that mechanism viable at scale |

[ADR-0002](../adr/0002-no-embeddings-agent-read-retrieval.md) says retrieval is "an agent reading the map", and names its own revisit trigger: *"once `_index.md` no longer fits a single read and folder tiering stops discriminating"*. A search index is the answer to **the first half of that trigger without invoking the second half's conclusion**: when the map gets too big to read whole, you don't need embeddings, you need a way to read the *relevant slice* of the map. `search()` returns exactly that slice — and the agent then proceeds with the existing summary-first, edge-following procedure over it, unchanged. **The search index feeds the map-read; it does not replace it.**

Two consequences to build in:

- **Do not make `build_index.py` read from `index.db`.** It would be faster and it inverts the trust direction: `_index.md` is committed, human-trusted, derived-from-source data, and deriving it from a rebuildable cache means a corrupt cache silently corrupts a committed artifact. `build_index.py` keeps walking the tree.
- **Do share the extraction.** `build_index._page_record()` already computes precisely the metadata the `page` table needs (title, summary, tags, source_date, volatility, edges). Factor it into a shared frontmatter→record function used by both, so `_index.md` and the search index can never disagree about a page's tags. This is the one code change that makes the whole thing coherent rather than parallel.

---

## 8. Migration impact

Rough sizing, most to least work.

| File | Change | Size |
| --- | --- | --- |
| `scripts/search_index.py` | **New.** Schema, staleness scan, incremental upsert, query builder, FTS5 capability probe + fallback. The whole mechanism. | ~250–350 lines |
| `tests/test_search_index.py` | **New.** Per [ADR-0005](../adr/0005-tdd-for-scripts-evals-for-agents.md) this is script-layer code and is written test-first. Must cover: hyphenated-term quoting, bm25 sign, staleness after simulated `git checkout` (touch mtime), deleted-page row removal, `superseded_by` derivation, FTS5-absent fallback. | ~200 lines |
| `scripts/search.py` | **New.** Thin argparse CLI over `Vault.search` / `reindex` / `status`, text + `--json` output. | ~80 lines |
| `scripts/wikipage.py` | `Vault.search()` / `reindex()` / `index_status()` facade; inline index update in `write`/`set`/`merge`/`move_page`. `WikiPage` untouched. | ~40 lines |
| `skills/wiki-retrieval/SKILL.md` | **The biggest doc change.** Step 2's three grep/tag/title seed strategies collapse into one `search.py` call. The *"never grep a schema word"* rule is deleted — it exists only because grep can't see structure, and the index can. The commit-date sanity-check rule becomes "use `--date-field git_date`". The supersession rule becomes "the default filter already excludes them; pass `--include-superseded` only to discuss history". Steps 3–6 (summary-first judgment, edge-following, budget, honest temporal framing) are unchanged. | ~40 lines rewritten of ~70 |
| `agents/wiki-researcher.md` | Essentially unchanged — it delegates to the skill. Possibly one line noting the search CLI. | ~2 lines |
| `skills/wiki-ingest/SKILL.md` | Step 3's overlap check ("find existing pages this document overlaps") is currently grep-shaped and becomes a `search.py` call. Genuine quality win: BM25 over title+summary is a much better overlap detector than substring grep, and this is the step where a miss causes a *duplicate page*. | ~15 lines |
| `scripts/build_index.py` | Extract `_page_record`'s frontmatter reading into the shared record function. Output byte-identical. | ~20 lines moved |
| `scripts/commit.py` | **No change.** The index is gitignored, so it is never staged. Worth an explicit test asserting that. | 0 |
| `pyproject.toml` | **No change.** `sqlite3` is stdlib. This is the point. | 0 |
| vault `.gitignore` + `scripts/init_wiki.py` | Add `.wiki-knowledge/` to the scaffolded `.gitignore`; existing vaults need the line added by hand or by a migration step in `wiki-init`. | ~2 lines |
| `docs/adr/0006-*.md` | **New ADR** recording "lexical index in stdlib SQLite, not embeddings and not a dependency", cross-referencing ADR-0002 rather than amending it. | ~15 lines |
| `CONTEXT.md` | Possibly one glossary entry for **search index** vs **index** (`_index.md`), since "index" is now overloaded and the repo's terminology discipline is strict. | ~4 lines |

**Sequencing note:** the API (§7) and its tests are the part worth landing first. The backend can ship as the §5 Python-`re` baseline and become FTS5 in a follow-up without a caller changing, which is a reasonable way to split #36 into two tickets if it looks too big.

---

## 9. What I could not verify

- **FTS5 presence in the python.org macOS installer's bundled SQLite, and in Homebrew's `python@3.x`.** Verified on Windows/python.org (empirically) and Debian/Ubuntu (packaging rules). macOS is very likely fine — the amalgamation enables FTS5 by default — but I had no macOS host to run `PRAGMA compile_options` on. The capability probe in §2 makes this a non-blocker rather than an assumption.
- **Tantivy's real-world ranking advantage on *this* corpus.** Asserted from its Lucene-class design, not measured. Measuring it properly needs a labelled question set — which is what the **golden vault** exists for, and would be the right way to settle #36's runner-up question if it is ever reopened.
- **Resilio Sync's behaviour with a live SQLite file.** The WAL-corruption hazard is well-established for Dropbox/OneDrive-class syncers and I am extending it to Resilio by analogy, not from Resilio documentation. The mitigation (no WAL, ignore the directory) costs nothing, so I recommend it without needing to be certain.
- **Index size for a vault of long-form pages.** All measurements replicate this vault's ~3 KB pages. A vault of 50 KB pages would have a different index-to-source ratio (probably *better*, since vocabulary saturates).
- **`whoosh-reloaded` on Python 3.13/3.14.** Its metadata claims 3.4–3.12 and it is unmaintained; I did not install it to check whether it happens to work anyway. Moot given the maintenance status.

---

## Appendix: sources

**SQLite** (primary):
[FTS5 documentation](https://www.sqlite.org/fts5.html) — compilation, external-content and contentless tables, `contentless_delete` (3.43.0), `bm25()` and the `rank` column, `unicode61`/`porter`/`ascii`/`trigram` tokenizers, `columnsize`/`detail` options, and the `rebuild`/`delete-all`/`integrity-check`/`optimize` commands ·
[SQLite 3.9.0 release log](https://www.sqlite.org/releaselog/3_9_0.html) ·
[Debian `sqlite3` `debian/rules`](https://sources.debian.org/src/sqlite3/latest/debian/rules/)

**Python packages** (primary — PyPI JSON API and project repos):
[`tantivy` on PyPI](https://pypi.org/project/tantivy/) (0.26.0, 2026-04-29; full wheel matrix read from `https://pypi.org/pypi/tantivy/0.26.0/json`) ·
[`quickwit-oss/tantivy-py`](https://github.com/quickwit-oss/tantivy-py) ·
[tantivy-py issue #371](https://github.com/quickwit-oss/tantivy-py/issues/371) ·
[`quickwit-oss/tantivy`](https://github.com/quickwit-oss/tantivy) ·
[`whoosh-reloaded` on PyPI](https://pypi.org/project/whoosh-reloaded/) (2.7.5, 2024-02-02) ·
[`Sygil-Dev/whoosh-reloaded`](https://github.com/Sygil-Dev/whoosh-reloaded) ("NO LONGER MAINTAINED") ·
[`xhluca/bm25s`](https://github.com/xhluca/bm25s)

**Measurements**: a throwaway prototype run on this machine (Windows 11, CPython 3.14.6, SQLite 3.50.4) against the live dogfooding vault at `enchiridion-vault` and against replicated/synthetic corpora at 500/2000/5000 pages. Not committed — the numbers are in §2 and §7 and are reproducible from the schema and queries quoted there.

**This repo**: `CLAUDE.md`, `CONTEXT.md`, `docs/adr/0002`, `docs/adr/0004`, `docs/adr/0005`, `wiki-plugin/scripts/wikipage.py`, `wiki-plugin/scripts/build_index.py`, `wiki-plugin/scripts/commit.py`, `wiki-plugin/pyproject.toml`, `wiki-plugin/skills/wiki-retrieval/SKILL.md`, `wiki-plugin/agents/wiki-researcher.md`.
