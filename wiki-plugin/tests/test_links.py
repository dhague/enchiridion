"""TDD for links.py — position-splice link rewrite on move/rename.

The risk this module carries: rewriting relative links by round-tripping the
markdown through a stringifier (reformats everything) or by naive text replace
(wrong offsets / collateral edits). The property test
``test_prop_move_only_touches_link_lines_and_resolves`` is the guard.
"""
import posixpath

from hypothesis import given, settings
from hypothesis import strategies as st

import links
from lib import md


def _resolve(file_rel: str, dest: str) -> str:
    """Resolve a link destination (path part only) to a vault-relative path."""
    path = dest.split("#", 1)[0]
    return posixpath.normpath(posixpath.join(posixpath.dirname(file_rel) or ".", path))


# --- example cases -----------------------------------------------------------

def test_inbound_link_rewritten_on_move():
    files = {
        "wiki/concept/a.md": "see [b](../entity/b.md)\n",
        "wiki/entity/b.md": "# B\n",
    }
    out = links.plan_move(files, "wiki/entity/b.md", "wiki/concept/b.md")
    assert out["wiki/concept/a.md"] == "see [b](b.md)\n"
    assert "wiki/concept/b.md" in out


def test_outbound_links_recomputed_when_file_moves():
    files = {
        "wiki/concept/a.md": "see [b](b.md) and [c](../entity/c.md)\n",
        "wiki/concept/b.md": "# B\n",
        "wiki/entity/c.md": "# C\n",
    }
    out = links.plan_move(files, "wiki/concept/a.md", "wiki/entity/a.md")
    # a.md now lives in wiki/entity/, so its links must be re-based from there.
    assert out["wiki/entity/a.md"] == "see [b](../concept/b.md) and [c](c.md)\n"


def test_anchor_preserved():
    files = {
        "wiki/concept/a.md": "jump [x](../entity/b.md#section-2)\n",
        "wiki/entity/b.md": "# B\n",
    }
    out = links.plan_move(files, "wiki/entity/b.md", "wiki/concept/b.md")
    assert out["wiki/concept/a.md"] == "jump [x](b.md#section-2)\n"


def test_image_embed_rewritten():
    files = {
        "wiki/concept/a.md": "![pic](../raw/img.png)\n",
        "raw/img.png": "",  # asset present in the vault map
    }
    out = links.plan_move(files, "wiki/concept/a.md", "wiki/a.md")
    # a.md moved up one level; the image path re-bases accordingly.
    assert out["wiki/a.md"] == "![pic](raw/img.png)\n"


def test_link_inside_list_item():
    files = {
        "wiki/concept/a.md": "- first\n- see [b](../entity/b.md)\n- last\n",
        "wiki/entity/b.md": "# B\n",
    }
    out = links.plan_move(files, "wiki/entity/b.md", "wiki/concept/b.md")
    assert out["wiki/concept/a.md"] == "- first\n- see [b](b.md)\n- last\n"


def test_self_link_follows_the_move():
    files = {
        "wiki/concept/a.md": "I link to [myself](a.md) here\n",
    }
    out = links.plan_move(files, "wiki/concept/a.md", "wiki/entity/a.md")
    # The self-link still points at the (now moved) file.
    assert out["wiki/entity/a.md"] == "I link to [myself](a.md) here\n"


def test_pure_rename_same_dir_updates_inbound_only():
    files = {
        "wiki/concept/a.md": "see [old](old-name.md)\n",
        "wiki/concept/old-name.md": "# Old\nlink to [a](a.md)\n",
    }
    out = links.plan_move(
        files, "wiki/concept/old-name.md", "wiki/concept/new-name.md"
    )
    assert out["wiki/concept/a.md"] == "see [old](new-name.md)\n"
    # The moved file's outbound sibling link is unchanged (same dir).
    assert out["wiki/concept/new-name.md"] == "# Old\nlink to [a](a.md)\n"


def test_external_and_anchor_only_links_untouched():
    files = {
        "wiki/concept/a.md": (
            "[web](https://example.com/b.md) and [frag](#heading)\n"
        ),
        "wiki/entity/b.md": "# B\n",
    }
    out = links.plan_move(files, "wiki/entity/b.md", "wiki/concept/b.md")
    assert out["wiki/concept/a.md"] == files["wiki/concept/a.md"]


def test_unrelated_file_untouched_bytewise():
    files = {
        "wiki/concept/a.md": "see [b](../entity/b.md)\n",
        "wiki/entity/b.md": "# B\n",
        "wiki/concept/unrelated.md": "no links here, just [text] brackets\n",
    }
    out = links.plan_move(files, "wiki/entity/b.md", "wiki/concept/b.md")
    assert out["wiki/concept/unrelated.md"] == files["wiki/concept/unrelated.md"]


# --- property test -----------------------------------------------------------

_DIRS = ["wiki/concept", "wiki/entity", "wiki/source", "raw/notes"]
_NAMES = ["a", "b", "c", "d", "e"]


_EDGE_KEYS = ["refines", "contradicts", "example-of", "source", "related"]


@st.composite
def _vaults(draw):
    """A small vault whose relative links — in body *and* per-key frontmatter — resolve.

    Each page carries an amended-schema frontmatter block: a title, a flow-list
    `tags`, and zero or more typed-edge keys, each holding quoted markdown links
    to other pages. This exercises the move invariant against frontmatter links,
    not just body links.
    """
    rels = draw(
        st.lists(
            st.tuples(st.sampled_from(_DIRS), st.sampled_from(_NAMES)),
            min_size=2,
            max_size=6,
            unique=True,
        ).map(lambda pairs: [f"{d}/{n}.md" for d, n in pairs])
    )
    rels = list(dict.fromkeys(rels))
    files = {}
    for rel in rels:
        others = [r for r in rels if r != rel]
        rel_dir = posixpath.dirname(rel) or "."

        def _md_link(target, with_anchor=False):
            dest = posixpath.relpath(target, rel_dir)
            if with_anchor:
                dest += "#sec"
            return f"[{posixpath.basename(target)}]({dest})"

        # --- frontmatter with per-key typed edges (quoted markdown links) ---
        fm = ["---", f"title: {posixpath.basename(rel)}", "tags: [x, y]"]
        edge_keys = draw(
            st.lists(st.sampled_from(_EDGE_KEYS), unique=True, max_size=2)
        )
        for key in edge_keys:
            edge_targets = draw(
                st.lists(st.sampled_from(others or [rel]), min_size=1, max_size=2)
            )
            fm.append(f"{key}:")
            for t in edge_targets:
                fm.append(f'  - "{_md_link(t)}"')
        fm.append("---")

        # --- body links ---
        body = ["# " + rel]
        for t in draw(st.lists(st.sampled_from(others or [rel]), max_size=3)):
            body.append(f"- see {_md_link(t, with_anchor=draw(st.booleans()))}")
        body.append("plain trailing line")

        files[rel] = "\n".join(fm + body) + "\n"
    return files


@settings(max_examples=250, deadline=None)
@given(data=st.data())
def test_prop_move_only_touches_link_lines_and_resolves(data):
    files = data.draw(_vaults())
    rels = list(files)
    old_rel = data.draw(st.sampled_from(rels))
    dest_dir = data.draw(st.sampled_from(_DIRS))
    new_name = data.draw(st.sampled_from(_NAMES + ["moved"]))
    new_rel = f"{dest_dir}/{new_name}.md"
    # A move must not collide with an existing (non-moved) file.
    assume_ok = new_rel == old_rel or new_rel not in files
    if not assume_ok:
        return

    out = links.plan_move(files, old_rel, new_rel)

    # 1. Every relative link in the result resolves to a file in the result.
    for rel, text in out.items():
        for lk in md.iter_links(text):
            path = lk.dest.split("#", 1)[0]
            if "://" in path or path.startswith(("/", "#")) or path == "":
                continue
            target = _resolve(rel, lk.dest)
            assert target in out, (
                f"dangling link {lk.dest!r} in {rel} -> {target}"
            )

    # 2. Only link-bearing lines may differ (byte-for-byte on the rest).
    for rel, text in files.items():
        new_rel_for = new_rel if rel == old_rel else rel
        before = text.splitlines()
        after = out[new_rel_for].splitlines()
        assert len(before) == len(after)
        for b, a in zip(before, after):
            if b != a:
                assert "](" in b, f"non-link line changed in {rel}: {b!r} -> {a!r}"
