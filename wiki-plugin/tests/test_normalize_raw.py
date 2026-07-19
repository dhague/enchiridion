"""TDD for normalize_raw.py — raw/ filename normalization drives raw_source rewrite.

normalize_raw.py never touches a raw file's *contents* (byte-identical
rename); it only fixes the *name* (`YYYY-MM-DD-hhmm-` prefix, spaces to
underscores) and, if any `source/` page's `raw_source` link points at the old
name, follows the rename there too. The rewrite reuses links.py's
`plan_move` directly: the raw file is deliberately never added to the
`{rel: text}` pages map (it usually isn't markdown at all), so `plan_move`'s
per-file loop only ever rewrites *inbound* links at the old raw path —
exactly what's needed here.
"""
from datetime import datetime

import pytest

import normalize_raw


# --- normalize_name (pure) ----------------------------------------------------

def test_adds_datetime_prefix():
    when = datetime(2026, 3, 1, 14, 5)
    assert normalize_raw.normalize_name("notes.txt", when) == "2026-03-01-1405-notes.txt"


def test_replaces_spaces_with_underscores():
    when = datetime(2026, 3, 1, 14, 5)
    assert (
        normalize_raw.normalize_name("Meeting Notes.txt", when)
        == "2026-03-01-1405-Meeting_Notes.txt"
    )


def test_idempotent_on_already_normalized_name():
    # A far-future `when` proves the existing prefix wins outright, not just coincidentally.
    when = datetime(2099, 1, 1, 0, 0)
    name = "2026-03-01-1405-notes.txt"
    assert normalize_raw.normalize_name(name, when) == name


def test_prefix_like_but_invalid_pattern_is_not_mistaken_for_normalized():
    # Date-only, no hhmm segment — not the real prefix shape, so it still gets one.
    when = datetime(2026, 3, 1, 14, 5)
    out = normalize_raw.normalize_name("2026-03-01-note.txt", when)
    assert out == "2026-03-01-1405-2026-03-01-note.txt"


# --- apply_normalize (disk integration) ---------------------------------------

def _vault(tmp_path):
    (tmp_path / "wiki" / "source").mkdir(parents=True)
    (tmp_path / "raw" / "notes").mkdir(parents=True)
    return tmp_path


def test_renames_and_content_byte_identical(tmp_path):
    root = _vault(tmp_path)
    raw = root / "raw" / "notes" / "notes.txt"
    raw.write_bytes(b"some raw bytes\x00\x01")

    result = normalize_raw.apply_normalize(
        root, "raw/notes/notes.txt", when=datetime(2026, 3, 1, 14, 5)
    )

    assert result.renamed is True
    assert result.new_rel == "raw/notes/2026-03-01-1405-notes.txt"
    new_path = root / result.new_rel
    assert new_path.read_bytes() == b"some raw bytes\x00\x01"
    assert not raw.exists()


def test_idempotent_noop_when_already_normalized(tmp_path):
    root = _vault(tmp_path)
    raw = root / "raw" / "notes" / "2026-03-01-1405-notes.txt"
    raw.write_bytes(b"unchanged")

    result = normalize_raw.apply_normalize(
        root, "raw/notes/2026-03-01-1405-notes.txt", when=datetime(2099, 1, 1, 0, 0)
    )

    assert result.renamed is False
    assert result.new_rel == "raw/notes/2026-03-01-1405-notes.txt"
    assert raw.exists()
    assert raw.read_bytes() == b"unchanged"


def test_updates_referencing_source_page_raw_source(tmp_path):
    root = _vault(tmp_path)
    (root / "raw" / "notes" / "notes.txt").write_bytes(b"raw content")
    page = root / "wiki" / "source" / "x.md"
    page.write_text(
        "---\n"
        "title: X source\n"
        'raw_source: "[notes.txt](../../raw/notes/notes.txt)"\n'
        "---\n"
        "# X\n",
        encoding="utf-8",
    )

    result = normalize_raw.apply_normalize(
        root, "raw/notes/notes.txt", when=datetime(2026, 3, 1, 14, 5)
    )

    assert result.renamed is True
    assert result.updated_pages == ["wiki/source/x.md"]
    updated = page_text = (root / "wiki" / "source" / "x.md").read_text(encoding="utf-8")
    assert (
        'raw_source: "[2026-03-01-1405-notes.txt](../../raw/notes/2026-03-01-1405-notes.txt)"\n'
        in updated
    )


def test_no_matching_source_page_is_fine(tmp_path):
    root = _vault(tmp_path)
    (root / "raw" / "notes" / "notes.txt").write_bytes(b"raw content")
    page = root / "wiki" / "source" / "unrelated.md"
    page.write_text(
        "---\ntitle: Unrelated\nraw_source: \"[y.txt](../../raw/notes/y.txt)\"\n---\n# Y\n",
        encoding="utf-8",
    )

    result = normalize_raw.apply_normalize(
        root, "raw/notes/notes.txt", when=datetime(2026, 3, 1, 14, 5)
    )

    assert result.renamed is True
    assert result.updated_pages == []
    assert page.read_text(encoding="utf-8") == (
        "---\ntitle: Unrelated\nraw_source: \"[y.txt](../../raw/notes/y.txt)\"\n---\n# Y\n"
    )


def test_when_defaults_to_mtime_when_not_provided(tmp_path):
    import os
    import time

    root = _vault(tmp_path)
    raw = root / "raw" / "notes" / "notes.txt"
    raw.write_bytes(b"raw content")
    stamp = datetime(2025, 6, 15, 9, 30).timestamp()
    os.utime(raw, (stamp, stamp))

    result = normalize_raw.apply_normalize(root, "raw/notes/notes.txt")

    assert result.new_rel == "raw/notes/2025-06-15-0930-notes.txt"


def test_missing_raw_file_raises(tmp_path):
    root = _vault(tmp_path)
    with pytest.raises(FileNotFoundError):
        normalize_raw.apply_normalize(root, "raw/notes/missing.txt")


# --- scan_and_normalize --------------------------------------------------------

def test_scan_normalizes_every_unnormalized_raw_file(tmp_path):
    root = _vault(tmp_path)
    (root / "raw" / "notes" / "a.txt").write_bytes(b"a")
    (root / "raw" / "notes" / "b.txt").write_bytes(b"b")
    (root / "raw" / "notes" / "2026-01-01-0000-already.txt").write_bytes(b"already")

    results = normalize_raw.scan_and_normalize(root, when=datetime(2026, 3, 1, 14, 5))

    renamed = {r.old_rel: r.new_rel for r in results if r.renamed}
    assert renamed == {
        "raw/notes/a.txt": "raw/notes/2026-03-01-1405-a.txt",
        "raw/notes/b.txt": "raw/notes/2026-03-01-1405-b.txt",
    }
    still_present = [r for r in results if not r.renamed]
    assert [r.new_rel for r in still_present] == ["raw/notes/2026-01-01-0000-already.txt"]


def test_scan_never_touches_wiki(tmp_path):
    root = _vault(tmp_path)
    (root / "raw" / "notes" / "a.txt").write_bytes(b"a")
    page = root / "wiki" / "source" / "x.md"
    page.write_text("---\ntitle: X\n---\n# X\n", encoding="utf-8")

    normalize_raw.scan_and_normalize(root, when=datetime(2026, 3, 1, 14, 5))

    assert page.read_text(encoding="utf-8") == "---\ntitle: X\n---\n# X\n"
