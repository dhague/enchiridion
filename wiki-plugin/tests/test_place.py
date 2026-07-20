"""TDD for place.py — deterministic kebab-slug + kind-folder path computation.

The placement *algorithm* (which kind a page belongs to) is judgment and stays
with the ingesting agent; this module only makes the mechanical half —
title -> slug -> vault-relative path — a pure function instead of prose the
agent has to execute by hand each time.
"""
import pytest

import place


# --- slugify -------------------------------------------------------------

def test_slugify_lowercases_and_hyphenates():
    assert place.slugify("Prepared Statements") == "prepared-statements"


def test_slugify_strips_punctuation():
    assert place.slugify("What's a Connection Pool?") == "whats-a-connection-pool"


def test_slugify_collapses_whitespace_and_symbols():
    assert place.slugify("DB   Pooling -- Notes") == "db-pooling-notes"


def test_slugify_strips_leading_trailing_hyphens():
    assert place.slugify("  -Postgres Tuning-  ") == "postgres-tuning"


# --- path ------------------------------------------------------------------

def test_path_joins_kind_folder_and_slug():
    assert place.path("concept", "Prepared Statements") == "wiki/concept/prepared-statements.md"


def test_path_covers_all_four_kinds():
    for kind in ("source", "synthesis", "entity", "concept"):
        assert place.path(kind, "X") == f"wiki/{kind}/x.md"


def test_path_rejects_unknown_kind():
    with pytest.raises(ValueError):
        place.path("bogus", "X")


# --- CLI ---------------------------------------------------------------------

def test_cli_prints_path(capsys):
    place._main(["concept", "Prepared Statements"])
    assert capsys.readouterr().out == "wiki/concept/prepared-statements.md\n"


def test_cli_rejects_unknown_kind(capsys):
    with pytest.raises(SystemExit):
        place._main(["bogus", "X"])
