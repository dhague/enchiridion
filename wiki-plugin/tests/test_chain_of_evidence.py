"""TDD for chain_of_evidence.py — the one shared page -> stub -> raw file
check (#34 point 4), called by both ingest.py (agent-time) and commit.py
(commit-time hard gate) so a divergence between the two is impossible.
"""
import chain_of_evidence
from wikipage import WikiPage


def _stub(raw_source_link='[notes.md](../../raw/notes.md)'):
    return WikiPage(
        "---\n"
        "title: Notes\n"
        f'raw_source: "{raw_source_link}"\n'
        "---\n"
        "# Notes\n"
    )


def _distilled(source_link='[Notes](../source/notes.md)'):
    return WikiPage(
        "---\n"
        "title: Prepared Statements\n"
        f'source:\n  - "{source_link}"\n'
        "---\n"
        "# Prepared Statements\n"
    )


def test_passes_when_stub_and_source_edge_are_present():
    staged = {
        "wiki/source/notes.md": _stub(),
        "wiki/concept/prepared-statements.md": _distilled(),
    }
    assert chain_of_evidence.check(staged, "raw/notes.md") == []


def test_fails_when_no_stub_present():
    staged = {"wiki/concept/prepared-statements.md": _distilled()}
    errors = chain_of_evidence.check(staged, "raw/notes.md")
    assert len(errors) == 1
    assert "source/ page" in errors[0]


def test_fails_when_stub_points_elsewhere():
    staged = {
        "wiki/source/notes.md": _stub('[other.md](../../raw/other.md)'),
        "wiki/concept/prepared-statements.md": _distilled(),
    }
    errors = chain_of_evidence.check(staged, "raw/notes.md")
    assert len(errors) == 1
    assert "source/ page" in errors[0]


def test_fails_when_a_page_is_missing_its_source_edge():
    staged = {
        "wiki/source/notes.md": _stub(),
        "wiki/concept/prepared-statements.md": WikiPage(
            "---\ntitle: Prepared Statements\n---\n# Prepared Statements\n"
        ),
    }
    errors = chain_of_evidence.check(staged, "raw/notes.md")
    assert errors == ["wiki/concept/prepared-statements.md needs a source edge to the stub wiki/source/notes.md"]


def test_stub_is_exempt_from_needing_its_own_source_edge():
    staged = {"wiki/source/notes.md": _stub()}
    assert chain_of_evidence.check(staged, "raw/notes.md") == []


def test_order_independent_over_staged_dict():
    forward = {
        "wiki/source/notes.md": _stub(),
        "wiki/concept/prepared-statements.md": _distilled(),
    }
    backward = dict(reversed(list(forward.items())))
    assert (
        chain_of_evidence.check(forward, "raw/notes.md")
        == chain_of_evidence.check(backward, "raw/notes.md")
        == []
    )


def test_handles_percent_encoded_raw_filename():
    staged = {
        "wiki/source/notes.md": _stub("[my notes.md](../../raw/my%20notes.md)"),
        "wiki/concept/prepared-statements.md": _distilled(),
    }
    assert chain_of_evidence.check(staged, "raw/my notes.md") == []
