"""TDD for superseded_by.py -- the scripted replacement for wiki-retrieval/
SKILL.md step 4's Grep-based ``superseded_by`` map (#89).

``resolve()`` is pure (a candidate set plus an already-loaded
``{page_ref: PageRecord}`` map in, resolutions out); the CLI half wires it
to a real vault via ``WIKI_ROOT``, mirroring search.py's ``_main`` test
pattern (test_search_index.py).
"""
from __future__ import annotations

import json
from pathlib import Path

import page_record
from superseded_by import Resolution, resolve


# --- resolve(): pure chain-walking -----------------------------------------


def _records(pages: dict[str, str]) -> dict[str, page_record.PageRecord]:
    return page_record.load_records(pages)


def _page(title: str, supersedes: str | None = None) -> str:
    lines = [
        "---",
        f"title: {title}",
        "summary: s",
        "tags: []",
        "source_date: 2026-01-01",
        "volatility: stable",
    ]
    if supersedes:
        lines.append("supersedes:")
        lines.append(f'  - "[Old]({supersedes})"')
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def test_current_page_resolves_to_itself():
    records = _records({"wiki/concepts/a.md": _page("A")})
    (res,) = resolve(["wiki/concepts/a.md"], records)
    assert res == Resolution(seed="wiki/concepts/a.md", active="wiki/concepts/a.md", chain=[])


def test_superseded_seed_resolves_to_replacement():
    records = _records({
        "wiki/concepts/old.md": _page("Old"),
        "wiki/concepts/new.md": _page("New", supersedes="old.md"),
    })
    (res,) = resolve(["wiki/concepts/old.md"], records)
    assert res.seed == "wiki/concepts/old.md"
    assert res.active == "wiki/concepts/new.md"
    assert res.chain == ["wiki/concepts/new.md"]


def test_head_returned_even_when_outside_candidate_set():
    """A seed superseded by a page that isn't itself a seed still resolves
    to that head -- retrieval's "head not in set" rule (SKILL.md step 4)."""
    records = _records({
        "wiki/concepts/old.md": _page("Old"),
        "wiki/concepts/new.md": _page("New", supersedes="old.md"),
    })
    (res,) = resolve(["wiki/concepts/old.md"], records)
    assert res.active == "wiki/concepts/new.md"


def test_multi_hop_chain_walks_to_final_head():
    records = _records({
        "wiki/concepts/a.md": _page("A"),
        "wiki/concepts/b.md": _page("B", supersedes="a.md"),
        "wiki/concepts/c.md": _page("C", supersedes="b.md"),
    })
    (res,) = resolve(["wiki/concepts/a.md"], records)
    assert res.active == "wiki/concepts/c.md"
    assert res.chain == ["wiki/concepts/b.md", "wiki/concepts/c.md"]


def test_multiple_seeds_resolved_independently():
    records = _records({
        "wiki/concepts/old.md": _page("Old"),
        "wiki/concepts/new.md": _page("New", supersedes="old.md"),
        "wiki/concepts/current.md": _page("Current"),
    })
    resolutions = resolve(
        ["wiki/concepts/old.md", "wiki/concepts/current.md"], records,
    )
    by_seed = {r.seed: r for r in resolutions}
    assert by_seed["wiki/concepts/old.md"].active == "wiki/concepts/new.md"
    assert by_seed["wiki/concepts/current.md"].active == "wiki/concepts/current.md"


def test_seed_missing_from_vault_resolves_to_itself():
    records = _records({"wiki/concepts/a.md": _page("A")})
    (res,) = resolve(["wiki/concepts/gone.md"], records)
    assert res == Resolution(seed="wiki/concepts/gone.md", active="wiki/concepts/gone.md", chain=[])


def test_supersedes_cycle_does_not_infinite_loop():
    """Malformed data (a cycle) is never something a well-formed ingestion
    produces, but the walk must terminate rather than hang."""
    records = _records({
        "wiki/concepts/a.md": _page("A", supersedes="b.md"),
        "wiki/concepts/b.md": _page("B", supersedes="a.md"),
    })
    (res,) = resolve(["wiki/concepts/a.md"], records)
    assert res.active in ("wiki/concepts/a.md", "wiki/concepts/b.md")


# --- CLI ---------------------------------------------------------------


def _write_page(root: Path, rel: str, title: str, supersedes: str | None = None) -> None:
    path = root / "wiki" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_page(title, supersedes=supersedes), encoding="utf-8")


def test_cli_json_output(tmp_path, capsys, monkeypatch):
    _write_page(tmp_path, "concepts/old.md", "Old")
    _write_page(tmp_path, "concepts/new.md", "New", supersedes="old.md")
    monkeypatch.setenv("WIKI_ROOT", str(tmp_path))
    from superseded_by import _main

    _main(["wiki/concepts/old.md", "--json"])
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload == {
        "seed": "wiki/concepts/old.md",
        "active": "wiki/concepts/new.md",
        "chain": ["wiki/concepts/new.md"],
    }


def test_cli_table_output_marks_current_pages(tmp_path, capsys, monkeypatch):
    _write_page(tmp_path, "concepts/current.md", "Current")
    monkeypatch.setenv("WIKI_ROOT", str(tmp_path))
    from superseded_by import _main

    _main(["wiki/concepts/current.md"])
    out = capsys.readouterr().out
    assert "wiki/concepts/current.md" in out
    assert "(current)" in out
