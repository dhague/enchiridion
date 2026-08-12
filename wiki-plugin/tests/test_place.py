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
    assert place.path("concept", "Prepared Statements") == "wiki/concepts/prepared-statements.md"


def test_path_covers_all_four_kinds():
    for kind in ("source", "synthesis", "entity", "concept"):
        assert place.path(kind, "X") == f"wiki/{place.KIND_FOLDERS[kind]}/x.md"


def test_path_rejects_unknown_kind():
    with pytest.raises(ValueError):
        place.path("bogus", "X")


def test_kind_folders_pluralize_except_synthesis():
    assert place.KIND_FOLDERS == {
        "source": "sources",
        "synthesis": "synthesis",
        "entity": "entities",
        "concept": "concepts",
    }


def test_folder_kinds_is_the_exact_inverse():
    assert place.FOLDER_KINDS == {folder: kind for kind, folder in place.KIND_FOLDERS.items()}


# --- CLI ---------------------------------------------------------------------

def test_cli_prints_path(capsys):
    place._main(["concept", "Prepared Statements"])
    assert capsys.readouterr().out == "wiki/concepts/prepared-statements.md\n"


def test_cli_rejects_unknown_kind(capsys):
    with pytest.raises(SystemExit):
        place._main(["bogus", "X"])


# --- slug truncation (#70) ----------------------------------------------------


def test_slugify_no_truncation_within_limit():
    assert place.slugify("short-title") == "short-title"


def test_slugify_no_truncation_when_max_length_is_none():
    slug = place.slugify("a" * 100)
    assert len(slug) == 100


def test_slugify_truncates_at_hyphen_boundary():
    slug = place.slugify(
        "this-is-a-very-long-title-that-goes-on-and-on-way-beyond-sixty-four-characters",
        max_length=40,
    )
    assert slug == "this-is-a-very-long-title-that-goes-on"
    assert len(slug) <= 40


def test_slugify_hard_cut_when_no_hyphen_found():
    slug = place.slugify("abcdefghijklmnopqrstuvwxyz", max_length=14)
    assert slug == "abcdefghijklmn"
    assert len(slug) == 14


def test_slugify_hard_cut_when_hyphen_too_early():
    slug = place.slugify("abc-defghijklmnopqrstuvwxyz", max_length=14)
    assert slug == "abc-defghijklm"
    assert len(slug) == 14


def test_path_truncates_slug_to_64_chars():
    title = (
        "this-is-an-extremely-long-title-that-goes-on-and-on-and-on-"
        "way-past-the-sixty-four-character-limit-we-enforce-for-windows"
    )
    result = place.path("concept", title)
    slug = result.removeprefix("wiki/concepts/").removesuffix(".md")
    assert len(slug) <= place.MAX_SLUG_LENGTH


def test_path_cuts_at_hyphen_boundary():
    title = (
        "the-quick-brown-fox-jumps-over-the-lazy-dog-and-then-runs-"
        "away-into-the-forest-never-to-be-seen-again"
    )
    result = place.path("concept", title)
    slug = result.removeprefix("wiki/concepts/").removesuffix(".md")
    assert slug == "the-quick-brown-fox-jumps-over-the-lazy-dog-and-then-runs-away"
    assert len(slug) <= place.MAX_SLUG_LENGTH


# --- folder_to_kind (ADR-0008 singularization) --------------------------------

def test_folder_to_kind_strips_trailing_s():
    assert place.folder_to_kind("decisions") == "decision"
    assert place.folder_to_kind("meetings") == "meeting"
    assert place.folder_to_kind("projects") == "project"
    assert place.folder_to_kind("issues") == "issue"


def test_folder_to_kind_verbatim_when_no_trailing_s():
    assert place.folder_to_kind("people") == "people"
    assert place.folder_to_kind("research") == "research"


def test_folder_to_kind_canonical_folders_still_work():
    """Canonical folders like 'concepts' use the same rule (no special-casing)."""
    assert place.folder_to_kind("concepts") == "concept"
    assert place.folder_to_kind("sources") == "source"


# --- path with extra_kind_folders --------------------------------------------

def test_path_accepts_discovered_kind():
    extra = {"decision": "decisions"}
    assert place.path("decision", "My Decision", extra_kind_folders=extra) == \
        "wiki/decisions/my-decision.md"


def test_path_accepts_verbatim_kind():
    extra = {"people": "people"}
    assert place.path("people", "Alice", extra_kind_folders=extra) == \
        "wiki/people/alice.md"


def test_path_still_rejects_unknown_kind_without_extra():
    with pytest.raises(ValueError, match="decision"):
        place.path("decision", "X")
