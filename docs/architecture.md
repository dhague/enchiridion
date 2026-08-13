# Architecture

Point-in-time snapshot of the `wiki-knowledge` plugin's Python codebase, agents, and skills, as of plugin version `0.7.1`. This is not maintained on every change — treat it as a map from roughly now, not a live contract. If it disagrees with the code, the code wins.

**The Python script layer is being replaced by a Go binary**, one subcommand at a time ([ADR-0011](adr/0011-go-rewrite-scope-sequencing-toolchain.md)). `search` and `init` have migrated: what these diagrams show as the Search cluster and `init_wiki.py` now runs as `enchiridion search` / `enchiridion init` out of `enchiridion-go/`, invoked through `wiki-plugin/bin/enchiridion`. The shapes are unchanged — the Go packages keep the Python modules' seams — but the diagrams below are not redrawn per migrated subcommand. `wiki-plugin/skills/wiki-conventions/SKILL.md`'s script catalogue is the live answer to which capability runs on which implementation.

Diagrams:

1. **Module dependency graph** — how `wiki-plugin/scripts/*.py` (and `hooks/`) depend on each other, clustered by responsibility.
2. **Skill → agent → script-cluster flow** — how each of the plugin's five slash-command entrypoints reaches the code in diagram 1.
3. **Class diagrams by cluster** — one diagram per cluster from (1), showing its actual classes/dataclasses, fields, methods, and relationships.

## Module dependency graph

Scripts are grouped into eight responsibility clusters, plus a `hooks/` cluster shown separately. An arrow between clusters means at least one module in the source cluster imports at least one module in the target cluster; individual file-level imports are collapsed for readability (see each cluster's file list for exact contents).

```mermaid
flowchart TB
    subgraph core["Core library"]
        wikipage["wikipage.py"]
        place["place.py"]
        page_record["page_record.py"]
    end

    subgraph vaultops["Vault ops"]
        vault["vault.py"]
        vault_git["vault_git.py"]
        init_wiki["init_wiki.py"]
    end

    subgraph search["Search"]
        search_py["search.py"]
        search_index["search_index.py"]
    end

    subgraph ingestion["Ingestion pipeline"]
        ingest["ingest.py"]
        ingest_scan["ingest_scan.py"]
        discover["discover.py"]
        chain_of_evidence["chain_of_evidence.py"]
        commit["commit.py"]
        superseded_by["superseded_by.py"]
    end

    subgraph sessioncap["Session capture"]
        session_state["session_state.py"]
        transcript_capture["transcript_capture.py"]
        save_session["save-session-to-vault.py"]
    end

    subgraph watch["Watch"]
        watch_raw["watch_raw.py"]
    end

    subgraph migration["One-off / migration"]
        migrate["migrate_kind_folders_0114.py"]
    end

    subgraph stats["Stats"]
        tool_call_stats["tool_call_stats.py"]
    end

    subgraph hooks["hooks/"]
        log_tool_calls["log_tool_calls.py"]
        store_transcript_path["store_transcript_path.py"]
    end

    ingestion --> core
    ingestion --> vaultops
    vaultops --> core
    vaultops --> search
    search --> core
    sessioncap --> vaultops
    watch --> ingestion
    watch --> vaultops
    migration --> vaultops
    stats --> sessioncap

    hooks -.->|writes files sessioncap/stats read| sessioncap
    hooks -.->|writes files sessioncap/stats read| stats
```

Cluster contents:

- **Core library** — `wikipage.py` (`WikiPage` get/set/merge/retarget, `plan_move`; no I/O), `place.py` (kebab-slug + kind-folder path computation), `page_record.py` (frontmatter schema reader, derives `kind` and `superseded_by`).
- **Vault ops** — `vault.py` (`Vault`: all I/O, `resolve_vault_root()`), `vault_git.py` (sole git-shell-out module), `init_wiki.py` (vault scaffolding for `/wiki-init`).
- **Search** — `search.py` (query CLI), `search_index.py` (SQLite FTS5 index, `for_root()` cache).
- **Ingestion pipeline** — `ingest.py` (`IngestPlan` executor), `ingest_scan.py` (`raw/` sweep eligibility), `discover.py` (overlap-candidate + tag-vocabulary lookup), `chain_of_evidence.py` (page→stub→raw-file rule), `commit.py` (structured git commit per manifest), `superseded_by.py` (supersession queries).
- **Session capture** — `session_state.py` (session→transcript-path lookup), `transcript_capture.py` (JSONL→page rendering), `save-session-to-vault.py` (CLI adapter for `/save-conversation`).
- **Watch** — `watch_raw.py` (event-driven `raw/` watcher: debounce, lock file, queue file).
- **One-off / migration** — `migrate_kind_folders_0114.py` (singular→plural kind-folder migration, self-healing via `Vault.__init__`).
- **Stats** — `tool_call_stats.py` (summarizes a session's hook-logged tool calls).
- **hooks/** — `log_tool_calls.py`, `store_transcript_path.py`. These aren't imported by anything; they run as Claude Code hook events and write JSON files that Session capture and Stats later read. The dashed edges mark that producer→consumer relationship, not a Python import.

## Skill → agent → script-cluster flow

Each of the plugin's five skills, traced through its agent (if any) to the script cluster(s) it drives. `/wiki-watch` and `/save-conversation` are dispatchers: both hand off into the `/wiki-ingest` flow rather than duplicating it.

```mermaid
flowchart LR
    skIngest["/wiki-ingest"] --> agIngest["wiki-ingest agent (sonnet)"]
    agIngest --> cIngestion["Ingestion pipeline"]

    skRetrieval["/wiki-retrieval"] --> agResearcher["wiki-researcher agent (haiku)"]
    agResearcher --> cSearch["Search"]

    skInit["/wiki-init"] --> cVaultOps["Vault ops"]

    skWatch["/wiki-watch"] --> cWatch["Watch"]
    cWatch -->|dispatches per queued file| agIngest

    skSave["/save-conversation"] --> cSessionCap["Session capture"]
    cSessionCap -->|hands off written file| agIngest
```

Notes:

- `/wiki-init` calls `enchiridion init` (ported from `init_wiki.py`) directly — no agent involved, it's pure scaffolding.
- `/wiki-watch` runs `watch_raw.py` and `ingest_scan.py` (Watch cluster) itself, then dispatches one `wiki-ingest` subagent per eligible/queued file — the same agent `/wiki-ingest` uses, not a separate copy.
- `/save-conversation` runs `save-session-to-vault.py` (Session capture cluster) to write the raw transcript file, then delegates to the `wiki-ingest` agent to file it into the vault — again reusing the same agent and pipeline, not a parallel one.
- Script clusters here are the same eight named in the module dependency graph above.

## Class diagrams by cluster

One diagram per cluster from the module dependency graph, showing its actual classes/dataclasses and their relationships. Several clusters (session capture, migration, stats) are mostly free functions rather than classes — those modules are shown as `<<module>>` boxes listing their public functions, for the same collapsed-but-accurate treatment as the classed modules. Cross-cluster references are dashed and named after the target cluster.

### Core library

```mermaid
classDiagram
    class WikiPage {
        <<wikipage.py>>
        +frontmatter dict|None
        +body str
        +get(key)
        +set(key, value) WikiPage
        +merge(key, values) WikiPage
        +links() list~LinkMatch~
        +retarget(file_rel, old_rel, new_rel) WikiPage
    }
    class LinkMatch {
        <<frozen dataclass · wikipage.py>>
        +start int
        +end int
        +dest str
        +decoded_path str
        +decoded_anchor str
        +is_image bool
        +line int
    }
    class PageRecord {
        <<frozen dataclass · page_record.py>>
        +page_ref str
        +kind str
        +title str
        +summary str
        +tags list~str~
        +source_date str
        +volatility str
        +edges list~tuple~
        +superseded_by list~str~
    }
    class place {
        <<module · place.py>>
        +slugify(title) str
        +path(kind, title) str
    }

    WikiPage "1" --> "*" LinkMatch : links()
    PageRecord ..> WikiPage : page_record() reads .frontmatter
    PageRecord ..> place : folder→kind via place.FOLDER_KINDS
```

### Vault ops

```mermaid
classDiagram
    class Vault {
        <<vault.py>>
        +root Path
        +load(page_ref) WikiPage
        +write(page_ref, page)
        +pages() dict~str,PageRecord~
        +pages_with_text() dict
        +set(page_ref, key, value) WikiPage
        +merge(page_ref, key, values) WikiPage
        +move_page(old_ref, new_ref) list~str~
        +rewrite_inbound_links(old_rel, new_rel) list~str~
        +search(...) list~SearchHit~
        +reindex(full) IndexStats
        +index_status() IndexStatus
        +tag_vocabulary() list
    }
    class VaultGit {
        <<vault_git.py>>
        +available() bool
        +run(...args) str
        +ensure_work_tree()
        +init()
        +add(...paths)
        +commit(message) str
        +is_work_tree() bool
        +last_commit_date(rel) str|None
        +porcelain_mentions(rel) bool
        +commit_dates() dict
    }
    class GitError
    class InitError
    class init_wiki {
        <<module · init_wiki.py>>
        +init_wiki(root, mode, plugin_root)
        +is_vault(root) bool
    }
    class SearchIndex {
        <<Search cluster>>
    }

    GitError --|> RuntimeError
    InitError --|> RuntimeError
    init_wiki ..> VaultGit : scaffold commit
    Vault "1" --> "1" SearchIndex : _get_index() via search_index.for_root()
```

### Search

```mermaid
classDiagram
    class SearchIndex {
        <<search_index.py>>
        +__init__(root, git)
        +reindex(full) IndexStats
        +upsert_page(page_ref, ...)
        +remove_page(page_ref)
        +search(...) list~SearchHit~
        +status() IndexStatus
        +tag_counts() list
        +close()
    }
    class SearchHit {
        <<frozen dataclass>>
        +page_ref str
        +score float
        +title str
        +summary str
        +tags list~str~
        +kind str
        +source_date str
        +git_date str|None
        +volatility str
        +superseded_by str|None
        +snippet str|None
    }
    class IndexStats {
        <<dataclass>>
        +pages int
        +inserted int
        +updated int
        +removed int
        +duration_ms float
    }
    class IndexStatus {
        <<dataclass>>
        +pages int
        +db_size_bytes int
        +backend str
        +schema_version str
        +pages_stale int
    }
    class VaultGit {
        <<Vault ops cluster>>
    }
    class search_cli {
        <<module · search.py>>
        +_main(argv) int
    }

    SearchIndex "1" --> "1" VaultGit : git dates for hits
    SearchIndex ..> SearchHit : search() returns
    SearchIndex ..> IndexStats : reindex() returns
    SearchIndex ..> IndexStatus : status() returns
    search_cli ..> SearchIndex : via vault.Vault.search()
```

### Ingestion pipeline

```mermaid
classDiagram
    class IngestPlan {
        <<dataclass · ingest.py>>
        +title str
        +action str
        +source_date str|None
        +raw str|None
        +pages list~PagePlan~
        +from_dict(d)$ IngestPlan
    }
    class PagePlan {
        <<dataclass · ingest.py>>
        +op str
        +title str
        +body str|None
        +kind str|None
        +page_ref str|None
        +frontmatter dict
        +edges dict
        +from_dict(d)$ PagePlan
    }
    class ResolvedPlan {
        <<dataclass · ingest.py>>
        +plan IngestPlan
        +pages list~ResolvedPage~
        +root Path|None
        +validate()
        +execute() str
        +describe() str
    }
    class ResolvedPage {
        <<dataclass · ingest.py>>
        +plan_page PagePlan
        +page_ref str|None
        +page WikiPage|None
        +exists bool
        +loaded bool
        +op str
    }
    class PlanError
    class IngestCandidate {
        <<frozen dataclass · ingest_scan.py>>
        +raw_rel str
        +reason str
        +back_pointers list~str~
    }
    class ScanResult {
        <<frozen dataclass · ingest_scan.py>>
        +eligible list~IngestCandidate~
        +ignored list~str~
    }
    class Sweep {
        <<ingest_scan.py>>
        +vault Vault
        +scan(folder) ScanResult
        +append_ignore_entry(folder, pattern, comment)
    }
    class DiscoveryCandidate {
        <<frozen dataclass · discover.py>>
        +page_ref str
        +title str
        +score float
        +hint Hint
        +summary str
        +tags list~str~
        +volatility str
        +superseded_by str|None
    }
    class Manifest {
        <<dataclass · commit.py>>
        +title str
        +action str
        +created list~str~
        +updated list~str~
        +superseded list~tuple~
        +source_date str|None
        +raw_source str|None
        +staged_paths() list~str~
    }
    class CommitGateError
    class Resolution {
        <<frozen dataclass · superseded_by.py>>
        +seed str
        +active str
        +chain list~str~
    }
    class chain_of_evidence {
        <<module>>
        +check(staged, raw) list~str~
    }
    class Vault {
        <<Vault ops cluster>>
    }
    class WikiPage {
        <<Core library cluster>>
    }

    IngestPlan "1" --> "*" PagePlan : pages
    ResolvedPlan --> IngestPlan : plan
    ResolvedPlan "1" --> "*" ResolvedPage : pages
    ResolvedPage --> PagePlan : plan_page
    ResolvedPage ..> WikiPage : page
    ResolvedPlan ..> Manifest : execute() builds
    ResolvedPlan ..> Vault : execute() writes via
    PlanError --|> ValueError
    CommitGateError --|> RuntimeError
    Manifest ..> chain_of_evidence : commit() gated by check()
    ScanResult "1" --> "*" IngestCandidate : eligible
    Sweep --> Vault : wraps
    Sweep ..> ScanResult : scan() returns
    DiscoveryCandidate ..> Vault : discover.check() queries via Vault.search()
    Resolution ..> Vault : resolve() walks supersedes edges via Vault.pages()
```

### Session capture

```mermaid
classDiagram
    class transcript_capture {
        <<module · transcript_capture.py>>
        +sanitize_slug(phrase) str
        +transcript_to_page(...) str
        +find_transcript_path(session_id) str|None
        +write_capture(wiki_root, filename, markdown, short_id) str
        +capture_session(...) str
    }
    class CaptureError
    class session_state {
        <<module · session_state.py>>
        +sessions_dir(root, env) Path
        +write_transcript_path(session_id, path)
        +read_transcript_path(session_id) str|None
    }
    class save_session_cli {
        <<module · save-session-to-vault.py>>
        +main(argv) int
    }
    class Vault {
        <<Vault ops cluster>>
    }

    CaptureError --|> Exception
    transcript_capture ..> CaptureError : raises
    transcript_capture ..> session_state : find_transcript_path() reads
    save_session_cli ..> transcript_capture : capture_session()
    save_session_cli ..> Vault : resolve_vault_root()
```

### Watch

```mermaid
classDiagram
    class Debouncer {
        <<watch_raw.py>>
        +record_event(rel)
        +settled_files() list~str~
    }
    class _RawEventHandler {
        <<watch_raw.py>>
        +on_any_event(event)
    }
    class FileSystemEventHandler {
        <<watchdog, external>>
    }
    class WatchPaths {
        <<dataclass · watch_raw.py>>
        +for_root(root)$ WatchPaths
    }
    class watch_raw_module {
        <<module · watch_raw.py>>
        +write_lock(lock_path, pid, started_at)
        +acquire_lock(...)
        +append_queue(queue_path, rel)
        +remove_from_queue(queue_path, rel)
        +read_queue(queue_path) list~str~
        +check_and_enqueue(eligible, settled, queue_path) bool
    }
    class ingest_scan {
        <<Ingestion pipeline cluster>>
    }
    class Vault {
        <<Vault ops cluster>>
    }

    _RawEventHandler --|> FileSystemEventHandler
    _RawEventHandler --> Debouncer : records settled events into
    watch_raw_module --> WatchPaths : paths for lock/queue files
    watch_raw_module ..> ingest_scan : eligibility check before enqueue
    watch_raw_module ..> Vault : resolves root
```

### One-off / migration

```mermaid
classDiagram
    class migrate_kind_folders {
        <<module · migrate_kind_folders_0114.py>>
        +plan(root) list~tuple~
        +migrate(root, dry_run) list~tuple~
    }
    class MigrationError
    class Vault {
        <<Vault ops cluster>>
    }

    MigrationError --|> Exception
    migrate_kind_folders ..> MigrationError : raises
    migrate_kind_folders ..> Vault : self-healing, invoked from Vault.__init__
```

### Stats

```mermaid
classDiagram
    class tool_call_stats {
        <<module · tool_call_stats.py, no classes>>
        +log_path(session_id, state_dir) Path
        +read_log(session_id, state_dir) list~dict~
        +summarize(events) dict
        +format_summary(stats) str
    }
    class session_state {
        <<Session capture cluster>>
    }

    tool_call_stats ..> session_state : log_path() built from sessions_dir()
```

