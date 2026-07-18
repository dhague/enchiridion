"""Regression tests: links.py rewrites the amended per-key markdown-link frontmatter.

Since commit 19be866 the frontmatter carries relationships as *quoted relative
markdown links* — one YAML key per edge type (`refines`/`contradicts`/
`example-of`/`source`/`related`), plus `supersedes` (a list) and `raw_source`
(a single link into `raw/`). links.py locates links via lib.md.iter_links,
which scans the whole document, so these frontmatter links are rewritten on a
move by the same rule as body links. These tests lock that behavior.
"""
import posixpath

import links
from lib import md


def _resolves(files: dict[str, str]) -> None:
    """Assert every relative link in every file points at a file in the set."""
    for rel, text in files.items():
        for lk in md.iter_links(text):
            path = lk.dest.split("#", 1)[0]
            if "://" in path or path.startswith(("/", "#")) or path == "":
                continue
            target = posixpath.normpath(
                posixpath.join(posixpath.dirname(rel) or ".", path)
            )
            assert target in files, f"dangling {lk.dest!r} in {rel} -> {target}"


def test_inbound_frontmatter_edge_rewritten_on_move():
    files = {
        "wiki/concept/pooling.md": (
            "---\n"
            "title: Connection pooling\n"
            "refines:\n"
            '  - "[Prepared statements](../concept/prepared-statements.md)"\n'
            "---\n"
            "# Pooling\n"
        ),
        "wiki/concept/prepared-statements.md": "---\ntitle: PS\n---\n# PS\n",
    }
    out = links.plan_move(
        files,
        "wiki/concept/prepared-statements.md",
        "wiki/entity/prepared-statements.md",
    )
    assert (
        '  - "[Prepared statements](../entity/prepared-statements.md)"\n'
        in out["wiki/concept/pooling.md"]
    )
    _resolves(out)


def test_outbound_frontmatter_edges_rebased_when_page_moves():
    files = {
        "wiki/concept/a.md": (
            "---\n"
            "title: A\n"
            "related:\n"
            '  - "[B](../entity/b.md)"\n'
            "supersedes:\n"
            '  - "[Old A](a-old.md)"\n'
            "---\n"
            "# A\n"
        ),
        "wiki/entity/b.md": "---\ntitle: B\n---\n# B\n",
        "wiki/concept/a-old.md": "---\ntitle: Old A\n---\n# old\n",
    }
    out = links.plan_move(files, "wiki/concept/a.md", "wiki/source/a.md")
    moved = out["wiki/source/a.md"]
    # related edge re-based from the new (source/) directory
    assert '  - "[B](../entity/b.md)"\n' in moved
    # supersedes link re-based (a-old.md is still in concept/)
    assert '  - "[Old A](../concept/a-old.md)"\n' in moved
    _resolves(out)


def test_raw_source_survives_cross_dir_move():
    files = {
        "wiki/source/x.md": (
            "---\n"
            "title: X source\n"
            'raw_source: "[x.md](../../raw/notes/x.md)"\n'
            "---\n"
            "# X\n"
        ),
        "raw/notes/x.md": "raw bytes\n",
    }
    out = links.plan_move(files, "wiki/source/x.md", "wiki/x.md")
    moved = out["wiki/x.md"]
    # Moved up one level: ../../raw -> ../raw, still resolving to raw/notes/x.md.
    assert 'raw_source: "[x.md](../raw/notes/x.md)"\n' in moved
    target = posixpath.normpath(posixpath.join("wiki", "../raw/notes/x.md"))
    assert target == "raw/notes/x.md"


def test_same_dir_rename_leaves_raw_source_untouched():
    files = {
        "wiki/source/deploy.md": (
            "---\n"
            "title: Deploy\n"
            'raw_source: "[deploy.md](../../raw/notes/deploy.md)"\n'
            "---\n"
            "# Deploy\n"
        ),
        "raw/notes/deploy.md": "raw\n",
    }
    out = links.plan_move(
        files, "wiki/source/deploy.md", "wiki/source/deploy-github-actions.md"
    )
    # Same directory, so the raw_source pointer is byte-for-byte unchanged.
    assert (
        'raw_source: "[deploy.md](../../raw/notes/deploy.md)"\n'
        in out["wiki/source/deploy-github-actions.md"]
    )


def test_synthesis_source_edge_rewritten_others_byte_identical():
    files = {
        "wiki/synthesis/s.md": (
            "---\n"
            "title: S\n"
            "source:\n"
            '  - "[A](../concept/a.md)"\n'
            '  - "[B](../concept/b.md)"\n'
            "---\n"
            "# S\n"
        ),
        "wiki/concept/a.md": "---\ntitle: A\n---\n# A\n",
        "wiki/concept/b.md": "---\ntitle: B\n---\n# B\n",
    }
    out = links.plan_move(files, "wiki/concept/a.md", "wiki/entity/a.md")
    s = out["wiki/synthesis/s.md"]
    assert '  - "[A](../entity/a.md)"\n' in s        # rewritten
    assert '  - "[B](../concept/b.md)"\n' in s        # untouched, byte-identical
    _resolves(out)


def test_supersedes_link_rewritten_on_target_move():
    files = {
        "wiki/source/new.md": (
            "---\n"
            "title: New deploy\n"
            "supersedes:\n"
            '  - "[Old deploy](old.md)"\n'
            "---\n"
            "# New\n"
        ),
        "wiki/source/old.md": "---\ntitle: Old deploy\n---\n# old\n",
    }
    out = links.plan_move(files, "wiki/source/old.md", "wiki/concept/old.md")
    assert '  - "[Old deploy](../concept/old.md)"\n' in out["wiki/source/new.md"]
    _resolves(out)


def test_tags_flow_list_not_mistaken_for_a_link():
    # `tags: [db, sql]` must not be rewritten — it is not a markdown link.
    files = {
        "wiki/concept/a.md": (
            "---\ntitle: A\ntags: [db, sql]\n"
            'related:\n  - "[B](../entity/b.md)"\n---\n# A\n'
        ),
        "wiki/entity/b.md": "---\ntitle: B\n---\n# B\n",
    }
    out = links.plan_move(files, "wiki/entity/b.md", "wiki/concept/b.md")
    assert "tags: [db, sql]\n" in out["wiki/concept/a.md"]
    assert '  - "[B](b.md)"\n' in out["wiki/concept/a.md"]
