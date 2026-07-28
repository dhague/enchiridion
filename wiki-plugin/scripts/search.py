"""Search the wiki vault via the lexical index.

The default mode is a query: positional ``text`` plus any of the metadata
filters. With ``--reindex`` or ``--status`` the CLI switches to index
management. ``--json`` emits :class:`search_index.SearchHit` records as
JSON Lines for programmatic use; the default is a compact one-line-per-hit
table a Haiku agent can read directly.

The vault root is resolved by ``vault.resolve_vault_root()``, so both
deployment modes work unchanged.

CLI::

    python search.py "connection pooling" \\
        --tag database --since 2026-07-20 --date-field git_date \\
        --kind concept --limit 10 --json
    python search.py --reindex [--full]
    python search.py --status
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

import vault as vault_mod


def _render_hit(rel_width: int, hit) -> str:
    """A compact one-line-per-hit table for an agent reader.

    Path is right-padded to a fixed width so the title column aligns.
    Tags are joined with ``,``; dates are kept short. Volatility is
    shown in brackets — it is the second piece of the trust signal
    (after age) the agent must convey in any answer.
    """
    rel = hit.rel.ljust(rel_width)
    score = f"{hit.score:.2f}"
    title = hit.title or "-"
    volatility = hit.volatility or "-"
    source_date = hit.source_date or "-"
    git_date = hit.git_date or "-"
    return f"{rel}  {score:>7}  {title}  [{volatility}]  src={source_date}  git={git_date}"


def _hit_to_dict(hit) -> dict:
    """JSON-serialisable view of a :class:`SearchHit`."""
    return {
        "rel": hit.rel,
        "score": hit.score,
        "title": hit.title,
        "summary": hit.summary,
        "tags": list(hit.tags),
        "kind": hit.kind,
        "source_date": hit.source_date,
        "git_date": hit.git_date,
        "volatility": hit.volatility,
        "superseded_by": hit.superseded_by,
        "snippet": hit.snippet,
    }


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "text", nargs="?", default=None,
        help="free-text query; tokenized and phrase-quoted unless --raw",
    )
    parser.add_argument(
        "--tag", action="append", default=[],
        help="filter by tag; repeat for tags_all (AND) and combine with --tag-any for OR",
    )
    parser.add_argument(
        "--tag-any", action="append", default=[],
        help="filter by tag (OR semantics across the listed tags)",
    )
    parser.add_argument(
        "--kind", default=None,
        help="filter by kind (concept|entity|source|synthesis); comma-separated for multiple",
    )
    parser.add_argument("--since", default=None, help="ISO date; inclusive lower bound on date_field")
    parser.add_argument("--until", default=None, help="ISO date; inclusive upper bound on date_field")
    parser.add_argument(
        "--date-field", default="source_date", choices=("source_date", "git_date"),
        help="which date the --since/--until bounds apply to (default: source_date)",
    )
    parser.add_argument(
        "--volatility", default=None,
        help="filter by volatility (stable|evolving|volatile); comma-separated for multiple",
    )
    parser.add_argument("--limit", type=int, default=20, help="max hits (default 20)")
    parser.add_argument(
        "--include-superseded", action="store_true",
        help="include pages that have been superseded (default: filter them out)",
    )
    parser.add_argument(
        "--raw", action="store_true",
        help="pass the text through as a literal FTS5 expression (escape hatch)",
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help="emit results as JSON Lines (one object per line)",
    )

    # Index-management subcommands.
    parser.add_argument("--reindex", action="store_true", help="rebuild the index")
    parser.add_argument(
        "--full", action="store_true",
        help="with --reindex: wipe the index and rebuild from scratch",
    )
    parser.add_argument("--status", action="store_true", help="print index status and exit")

    args = parser.parse_args(argv)

    root = vault_mod.resolve_vault_root()
    v = vault_mod.Vault(root)

    if args.status:
        status = v.index_status()
        if args.as_json:
            print(json.dumps({
                "pages": status.pages,
                "db_size_bytes": status.db_size_bytes,
                "backend": status.backend,
                "schema_version": status.schema_version,
            }))
        else:
            print(f"pages:          {status.pages}")
            print(f"db_size_bytes:  {status.db_size_bytes}")
            print(f"backend:        {status.backend}")
            print(f"schema_version: {status.schema_version}")
        return 0

    if args.reindex:
        stats = v.reindex(full=args.full)
        if args.as_json:
            print(json.dumps({
                "pages": stats.pages,
                "inserted": stats.inserted,
                "updated": stats.updated,
                "removed": stats.removed,
                "duration_ms": stats.duration_ms,
            }))
        else:
            action = "full reindex" if args.full else "reindex"
            print(f"{action}: {stats.pages} pages "
                  f"(+{stats.inserted} ~{stats.updated} -{stats.removed}) "
                  f"in {stats.duration_ms:.1f} ms")
        return 0

    kind: str | list[str] | None = None
    if args.kind:
        kind = [k.strip() for k in args.kind.split(",") if k.strip()]
        if len(kind) == 1:
            kind = kind[0]

    volatility: list[str] = []
    if args.volatility:
        volatility = [v.strip() for v in args.volatility.split(",") if v.strip()]

    hits = v.search(
        args.text,
        tags_all=args.tag,
        tags_any=args.tag_any,
        kind=kind,
        since=args.since,
        until=args.until,
        date_field=args.date_field,
        volatility=volatility,
        include_superseded=args.include_superseded,
        raw=args.raw,
        limit=args.limit,
    )

    if args.as_json:
        for hit in hits:
            print(json.dumps(_hit_to_dict(hit)))
    elif hits:
        rel_width = max(len(h.rel) for h in hits)
        for hit in hits:
            print(_render_hit(rel_width, hit))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
