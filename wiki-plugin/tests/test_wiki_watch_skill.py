"""Structural test for the wiki-watch SKILL.md (#62).

Skill bodies are prose, not code — the existing convention (see the other
`skills/*/SKILL.md` files) is to test structure only: the file exists, has
valid frontmatter, and references the script it orchestrates. Behavior of
the procedure itself isn't unit-testable and isn't tested here.
"""
from __future__ import annotations

from pathlib import Path

SKILL_PATH = Path(__file__).parent.parent / "skills" / "wiki-watch" / "SKILL.md"


def _frontmatter(text: str) -> dict[str, str]:
    assert text.startswith("---\n"), "SKILL.md must open with a frontmatter block"
    _, _, rest = text.partition("---\n")
    block, _, _ = rest.partition("\n---")
    fields = {}
    for line in block.splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def test_skill_file_exists():
    assert SKILL_PATH.is_file()


def test_frontmatter_has_expected_name_and_description():
    text = SKILL_PATH.read_text(encoding="utf-8")
    fields = _frontmatter(text)
    assert fields["name"] == "wiki-watch"
    assert "description" in fields and fields["description"]


def test_procedure_references_watch_subcommand():
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert "enchiridion" in text
    assert "watch" in text


def test_procedure_references_ingest_scan_and_wiki_ingest():
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert "ingest-scan" in text
    assert "wiki-ingest" in text


def test_mentions_ctrl_c_or_sigint_shutdown():
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert "Ctrl-C" in text or "SIGINT" in text or "SIGTERM" in text
