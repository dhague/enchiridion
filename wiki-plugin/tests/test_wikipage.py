"""TDD for wikipage.py — WikiPage and the shared markdown primitives it's
built on (frontmatter split, AST-positioned link discovery, percent-encoding,
plan_move). Everything here is pure: no test in this file touches a vault.
``Vault``'s own coverage lives in test_vault.py.

Consolidates the coverage that previously lived in test_frontmatter.py,
test_links.py, test_links_frontmatter.py and test_md.py (#32 replaces
frontmatter.py + lib/md.py + links.py with this one module). Both required
hypothesis property tests carry forward unchanged in spirit:

* ``test_prop_untouched_key_byte_identical*`` — a naive YAML round-trip would
  reformat the *whole* document; this is the guard.
* ``test_prop_move_only_touches_link_lines_and_resolves`` — the guard against
  a move rewriting a link by round-tripping through a stringifier (reformats
  everything) or by naive text replace (wrong offsets / collateral edits).
"""
import posixpath
from io import StringIO

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from ruamel.yaml import YAML

import wikipage
from wikipage import WikiPage

# --- split_frontmatter ---------------------------------------------------


def test_split_with_frontmatter():
    text = "---\ntitle: Foo\ntags: [a, b]\n---\n# Body\n\nhello\n"
    fm, body, offset = wikipage.split_frontmatter(text)
    assert fm == "title: Foo\ntags: [a, b]\n"
    assert body == "# Body\n\nhello\n"
    assert text[offset:] == body


def test_split_without_frontmatter():
    text = "# Just a body\n\nno frontmatter here\n"
    fm, body, offset = wikipage.split_frontmatter(text)
    assert fm is None
    assert body == text
    assert offset == 0


def test_thematic_break_is_not_frontmatter():
    # A `---` that is not on the very first line is a horizontal rule, not frontmatter.
    text = "# Title\n\n---\n\nbody\n"
    fm, body, offset = wikipage.split_frontmatter(text)
    assert fm is None
    assert offset == 0


def test_empty_frontmatter_block():
    text = "---\n---\nbody\n"
    fm, body, offset = wikipage.split_frontmatter(text)
    assert fm == ""
    assert body == "body\n"


# --- iter_links ------------------------------------------------------------


def test_iter_links_plain_link_offsets():
    body = "see [the page](concepts/foo.md) now\n"
    links = list(wikipage.iter_links(body))
    assert len(links) == 1
    lk = links[0]
    assert lk.dest == "concepts/foo.md"
    assert body[lk.start:lk.end] == "concepts/foo.md"
    assert lk.is_image is False


def test_iter_links_image_embed():
    body = "![alt text](assets/pic.png)\n"
    links = list(wikipage.iter_links(body))
    assert len(links) == 1
    assert links[0].is_image is True
    assert links[0].dest == "assets/pic.png"
    assert body[links[0].start:links[0].end] == "assets/pic.png"


def test_iter_links_preserves_anchor_in_dest():
    body = "jump [here](entities/bar.md#section-2)\n"
    (lk,) = list(wikipage.iter_links(body))
    assert lk.dest == "entities/bar.md#section-2"
    assert body[lk.start:lk.end] == "entities/bar.md#section-2"


def test_iter_links_skips_code_fence():
    body = "real [a](one.md)\n\n```\nfake [b](two.md)\n```\n"
    dests = [lk.dest for lk in wikipage.iter_links(body)]
    assert dests == ["one.md"]


def test_iter_links_multiple_same_line_distinct_offsets():
    body = "[a](x.md) and [b](x.md) and [c](y.md)\n"
    links = list(wikipage.iter_links(body))
    assert [lk.dest for lk in links] == ["x.md", "x.md", "y.md"]
    starts = [lk.start for lk in links]
    assert starts == sorted(starts)
    assert len(set(starts)) == 3
    for lk in links:
        assert body[lk.start:lk.end] == lk.dest


def test_iter_links_ignores_title_after_dest():
    body = '[a](path.md "a title")\n'
    (lk,) = list(wikipage.iter_links(body))
    assert lk.dest == "path.md"
    assert body[lk.start:lk.end] == "path.md"


# --- WikiPage.get ------------------------------------------------------------


def test_get_existing_key():
    text = "---\ntitle: Prepared statements\nvolatility: stable\n---\nbody\n"
    page = WikiPage(text)
    assert page.get("title") == "Prepared statements"
    assert page.get("volatility") == "stable"


def test_get_missing_key_returns_none():
    page = WikiPage("---\ntitle: Foo\n---\nbody\n")
    assert page.get("nope") is None


def test_get_no_frontmatter_returns_none():
    assert WikiPage("# just a body\n").get("title") is None


def test_get_per_type_edge_key_returns_link_list():
    text = (
        "---\n"
        "title: Pooling\n"
        "refines:\n"
        '  - "[Prepared statements](../concepts/prepared-statements.md)"\n'
        '  - "[Indexing](../concepts/indexing.md)"\n'
        "---\n"
        "# Body\n"
    )
    edges = WikiPage(text).get("refines")
    assert list(edges) == [
        "[Prepared statements](../concepts/prepared-statements.md)",
        "[Indexing](../concepts/indexing.md)",
    ]


def test_get_raw_source_returns_single_link():
    text = (
        "---\n"
        "title: X\n"
        'raw_source: "[x.md](../../raw/notes/x.md)"\n'
        "---\n"
        "# X\n"
    )
    assert WikiPage(text).get("raw_source") == "[x.md](../../raw/notes/x.md)"


# --- WikiPage.set --------------------------------------------------------------


def test_set_updates_value_and_get_roundtrips():
    text = "---\ntitle: Foo\nvolatility: stable\n---\nbody\n"
    out = WikiPage(text).set("volatility", "evolving")
    assert out.get("volatility") == "evolving"
    assert out.get("title") == "Foo"


def test_set_adds_new_key():
    text = "---\ntitle: Foo\n---\nbody\n"
    out = WikiPage(text).set("source_date", "2026-03-01")
    assert out.get("source_date") == "2026-03-01"
    assert "title: Foo" in out.text


def test_set_preserves_body_exactly_including_thematic_break():
    body = "# Heading\n\nsome text\n\n---\n\nafter the rule\n"
    text = "---\ntitle: Foo\n---\n" + body
    out = WikiPage(text).set("title", "Bar")
    assert out.text.endswith(body)


def test_set_preserves_comment_on_other_key():
    text = "---\ntitle: Foo\nvolatility: stable  # do not lose me\n---\nbody\n"
    out = WikiPage(text).set("title", "Bar")
    assert "# do not lose me" in out.text


def test_set_preserves_flow_list_on_other_key():
    text = "---\ntitle: Foo\ntags: [db, sql]\n---\nbody\n"
    out = WikiPage(text).set("title", "Bar")
    assert "tags: [db, sql]" in out.text


def test_noop_set_is_byte_identical():
    text = "---\ntitle: Foo\ntags: [db, sql]\nvolatility: stable\n---\n# Body\n\nhi\n"
    out = WikiPage(text).set("title", "Foo")
    assert out.text == text


def test_set_scalar_leaves_edge_and_raw_source_blocks_byte_identical():
    text = (
        "---\n"
        "title: X source\n"
        "summary: old summary\n"
        'raw_source: "[x.md](../../raw/notes/x.md)"\n'
        "source:\n"
        '  - "[A](../concepts/a.md)"\n'
        '  - "[B](../concepts/b.md)"\n'
        "---\n"
        "# X\n"
    )
    out = WikiPage(text).set("summary", "new summary")
    assert out.get("summary") == "new summary"
    assert 'raw_source: "[x.md](../../raw/notes/x.md)"\n' in out.text
    assert (
        "source:\n"
        '  - "[A](../concepts/a.md)"\n'
        '  - "[B](../concepts/b.md)"\n'
    ) in out.text


def test_set_creates_frontmatter_when_absent():
    out = WikiPage("# just a body\n").set("title", "New")
    assert out.get("title") == "New"
    assert out.text.endswith("# just a body\n")


def test_set_new_raw_source_link_is_double_quoted():
    text = "---\ntitle: X\n---\n# X\n"
    out = WikiPage(text).set("raw_source", "[x.md](../../raw/notes/x.md)")
    assert 'raw_source: "[x.md](../../raw/notes/x.md)"\n' in out.text
    assert out.get("raw_source") == "[x.md](../../raw/notes/x.md)"


def test_set_new_edge_list_links_are_double_quoted():
    text = "---\ntitle: X\n---\n# X\n"
    out = WikiPage(text).set("refines", ["[A](../concepts/a.md)", "[B](../concepts/b.md)"])
    assert (
        "refines:\n"
        '  - "[A](../concepts/a.md)"\n'
        '  - "[B](../concepts/b.md)"\n'
    ) in out.text


# --- WikiPage.merge ------------------------------------------------------------


def test_merge_unions_with_existing_list():
    text = "---\ntitle: X\ntags: [db, sql]\n---\nbody\n"
    out = WikiPage(text).merge("tags", ["sql", "perf"])
    assert list(out.get("tags")) == ["db", "sql", "perf"]


def test_merge_on_missing_key_behaves_like_set():
    text = "---\ntitle: X\n---\nbody\n"
    out = WikiPage(text).merge("related", ["[A](../concepts/a.md)"])
    assert list(out.get("related")) == ["[A](../concepts/a.md)"]


def test_merge_new_links_are_double_quoted():
    text = '---\ntitle: X\nrefines:\n  - "[A](../concepts/a.md)"\n---\nbody\n'
    out = WikiPage(text).merge("refines", ["[B](../concepts/b.md)"])
    assert (
        "refines:\n"
        '  - "[A](../concepts/a.md)"\n'
        '  - "[B](../concepts/b.md)"\n'
    ) in out.text


def test_merge_is_idempotent():
    text = "---\ntitle: X\ntags: [db]\n---\nbody\n"
    once = WikiPage(text).merge("tags", ["db", "sql"])
    twice = once.merge("tags", ["db", "sql"])
    assert once.text == twice.text


# --- WikiPage.frontmatter (full mapping) --------------------------------------


def test_frontmatter_returns_full_mapping():
    text = (
        "---\n"
        "title: Pooling\n"
        "tags: [db, perf]\n"
        "related:\n"
        '  - "[B](../entities/b.md)"\n'
        "---\n"
        "# Body\n"
    )
    data = WikiPage(text).frontmatter
    assert data["title"] == "Pooling"
    assert list(data["tags"]) == ["db", "perf"]
    assert list(data["related"]) == ["[B](../entities/b.md)"]


def test_frontmatter_no_frontmatter_returns_none():
    assert WikiPage("# just a body\n").frontmatter is None


# --- property: the reformatting-stringifier guard -----------------------------

_YAML_KEYWORDS = {"true", "false", "yes", "no", "on", "off", "null", "y", "n"}

_ident = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=8
).filter(lambda s: s not in _YAML_KEYWORDS)

_scalar = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    min_size=1,
    max_size=20,
).map(str.strip).filter(bool)


def _canonical_fm(mapping: dict) -> str:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    buf = StringIO()
    y.dump(mapping, buf)
    return buf.getvalue()


@settings(max_examples=200)
@given(
    mapping=st.dictionaries(_ident, _scalar, min_size=1, max_size=6),
    body=st.text(alphabet="abc \n#", max_size=40),
)
def test_prop_untouched_key_byte_identical(mapping, body):
    """Re-writing a key with its own value leaves every byte identical."""
    text = "---\n" + _canonical_fm(mapping) + "---\n" + body
    for key, value in mapping.items():
        out = WikiPage(text).set(key, value)
        assert out.text == text, f"no-op set of {key!r} was not byte-identical"


_link = st.builds(
    lambda title, path: f'"[{title}]({path}.md)"',
    st.sampled_from(["A", "B", "C", "Prepared statements"]),
    st.sampled_from(["../concepts/a", "../entities/b", "../../raw/notes/x"]),
)

_EDGE_KEYS = {"refines", "source", "related", "supersedes"}
_scalar_key = _ident.filter(lambda s: s not in _EDGE_KEYS)

_safe_scalar = st.sampled_from(
    ["stable", "evolving", "volatile", "Prepared statements", "hello world", "A"]
)


@settings(max_examples=200)
@given(
    scalars=st.dictionaries(_scalar_key, _safe_scalar, min_size=1, max_size=3),
    edges=st.dictionaries(
        st.sampled_from(sorted(_EDGE_KEYS)),
        st.lists(_link, min_size=1, max_size=3),
        min_size=1,
        max_size=3,
    ),
    body=st.text(alphabet="abc \n#", max_size=30),
)
def test_prop_untouched_key_byte_identical_with_edge_lists(scalars, edges, body):
    """No-op set stays byte-identical even with 2-space-indented edge lists.

    This is the shape the earlier scalar-only property test could not reach —
    and the one that exposed the block-sequence reindentation bug (19be866).
    """
    fm = "".join(f"{k}: {v}\n" for k, v in scalars.items())
    for key, items in edges.items():
        fm += f"{key}:\n" + "".join(f"  - {item}\n" for item in items)
    text = "---\n" + fm + "---\n" + body
    for key, value in scalars.items():
        assert WikiPage(text).set(key, value).text == text


@settings(max_examples=200)
@given(
    mapping=st.dictionaries(_ident, _scalar, min_size=2, max_size=6),
    new_value=_scalar,
)
def test_prop_changing_one_key_leaves_other_lines_identical(mapping, new_value):
    """Changing one key never disturbs the physical lines of the others."""
    text = "---\n" + _canonical_fm(mapping) + "---\nbody\n"
    target = next(iter(mapping))
    out = WikiPage(text).set(target, new_value)
    before = text.splitlines()
    after = out.text.splitlines()
    assert len(before) == len(after)
    for b, a in zip(before, after):
        if b.startswith(f"{target}:"):
            continue
        assert b == a


# --- percent-encoding for raw/ filenames -------------------------------------------


def test_percent_encode_space():
    assert wikipage.percent_encode("my file.txt") == "my%20file.txt"


def test_percent_encode_hash():
    assert wikipage.percent_encode("file#1.txt") == "file%231.txt"


def test_percent_encode_percent():
    assert wikipage.percent_encode("50%.txt") == "50%25.txt"


def test_percent_encode_parens():
    assert wikipage.percent_encode("note (draft).txt") == "note%20%28draft%29.txt"


def test_percent_encode_combined():
    assert wikipage.percent_encode("my file#1 (draft).txt") == "my%20file%231%20%28draft%29.txt"


def test_percent_encode_unchanged_chars():
    # These chars should NOT be encoded
    assert wikipage.percent_encode("file&name.txt") == "file&name.txt"
    assert wikipage.percent_encode("file+name.txt") == "file+name.txt"
    assert wikipage.percent_encode("file'name.txt") == "file'name.txt"


def test_percent_decode_space():
    assert wikipage.percent_decode("my%20file.txt") == "my file.txt"


def test_percent_decode_hash():
    assert wikipage.percent_decode("file%231.txt") == "file#1.txt"


def test_percent_decode_combined():
    assert wikipage.percent_decode("my%20file%231%20%28draft%29.txt") == "my file#1 (draft).txt"


def test_percent_roundtrip():
    original = "my file#1 (50%) - draft.txt"
    encoded = wikipage.percent_encode(original)
    decoded = wikipage.percent_decode(encoded)
    assert decoded == original


# --- link_dest / resolve_link_dest ---------------------------------------------
#
# The single owner of "given a markdown link, produce a vault-relative path"
# (#58) — previously reimplemented, with subtle differences, in ingest.py,
# commit.py and page_record.py.


def test_link_dest_returns_decoded_path():
    assert wikipage.link_dest("[Notes](../raw/my%20notes.md)") == "../raw/my notes.md"


def test_link_dest_strips_anchor():
    assert wikipage.link_dest("[A](../concepts/a.md#section)") == "../concepts/a.md"


def test_link_dest_none_for_non_link():
    assert wikipage.link_dest("not a link") is None


def test_resolve_link_dest_vault_relative_by_default():
    # page_dir is vault-relative; the default prefix is "" (ADR-0009)
    assert (
        wikipage.resolve_link_dest("../../raw/notes.md", "wiki/sources")
        == "raw/notes.md"
    )


def test_resolve_link_dest_explicit_prefix_still_supported():
    # An explicit prefix is the escape hatch for a caller with a different base.
    assert (
        wikipage.resolve_link_dest("../raw/notes.md", "source", prefix="wiki")
        == "wiki/raw/notes.md"
    )


def test_resolve_link_dest_empty_page_dir():
    assert wikipage.resolve_link_dest("a.md", "") == "a.md"


# --- compose_link (#101: plans name rels, ingest.py composes the link) ---------


def test_compose_link_relativises_across_kinds():
    assert (
        wikipage.compose_link("Notes", "wiki/sources/notes.md", "wiki/concepts")
        == "[Notes](../sources/notes.md)"
    )


def test_compose_link_same_directory_is_the_bare_filename():
    assert (
        wikipage.compose_link("Existing", "wiki/concepts/existing.md", "wiki/concepts")
        == "[Existing](existing.md)"
    )


def test_compose_link_encodes_the_destination_not_the_label():
    assert (
        wikipage.compose_link("My Notes (draft).md", "raw/My Notes (draft).md", "wiki/sources")
        == "[My Notes (draft).md](../../raw/My%20Notes%20%28draft%29.md)"
    )


def test_compose_link_empty_page_dir():
    assert wikipage.compose_link("A", "a.md", "") == "[A](a.md)"


# --- iter_links: balanced-paren destinations (#101) -----------------------------


def test_iter_links_balanced_parens_in_dest():
    body = "[Foo](https://en.wikipedia.org/wiki/Foo_(disambiguation))"
    (lk,) = list(wikipage.iter_links(body))
    assert lk.dest == "https://en.wikipedia.org/wiki/Foo_(disambiguation)"


def test_iter_links_balanced_parens_does_not_swallow_a_following_link():
    body = "[a](x_(y).md) [b](z.md)"
    dests = [lk.dest for lk in wikipage.iter_links(body)]
    assert dests == ["x_(y).md", "z.md"]


def test_iter_links_unbalanced_close_paren_still_ends_the_destination():
    # a title in parens after the dest still works once the dest itself balances
    body = '[a](x.md "a title")'
    (lk,) = list(wikipage.iter_links(body))
    assert lk.dest == "x.md"


# --- normalize_body_links (#101: ingest.py re-encodes body links on write) -----


def test_normalize_body_links_encodes_an_unencoded_paren():
    body = "See [notes](../../raw/My%20Notes%20(draft).md)."
    assert (
        wikipage.normalize_body_links(body)
        == "See [notes](../../raw/My%20Notes%20%28draft%29.md)."
    )


def test_normalize_body_links_is_idempotent():
    body = "See [notes](../../raw/My%20Notes%20%28draft%29.md)."
    assert wikipage.normalize_body_links(body) == body


def test_normalize_body_links_leaves_absolute_urls_alone():
    body = "[Wikipedia](https://en.wikipedia.org/wiki/Foo_(disambiguation))"
    assert wikipage.normalize_body_links(body) == body


def test_normalize_body_links_leaves_bare_anchors_alone():
    body = "[Section](#some-heading)"
    assert wikipage.normalize_body_links(body) == body


@given(
    dest=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="/\\"),
        min_size=1,
        max_size=15,
    ).filter(lambda s: s not in (".", "..") and s.strip() == s and s),
    page_dir=st.lists(
        st.text(
            alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="/\\"),
            min_size=1,
            max_size=10,
        ).filter(lambda s: s not in (".", "..") and s.strip() == s and s),
        max_size=3,
    ).map(lambda parts: "/".join(parts)),
)
@settings(max_examples=50)
def test_prop_resolve_link_dest_stable_under_re_resolution(dest, page_dir):
    """Resolving an already-resolved (vault-relative) path a second time,
    with no further prefix to add, is a no-op — no double-prefixing."""
    once = wikipage.resolve_link_dest(dest, page_dir)
    twice = wikipage.resolve_link_dest(once, "")
    assert twice == once


# --- plan_move: example cases --------------------------------------------------


def test_inbound_link_rewritten_on_move():
    files = {
        "wiki/concepts/a.md": "see [b](../entities/b.md)\n",
        "wiki/entities/b.md": "# B\n",
    }
    out = wikipage.plan_move(files, "wiki/entities/b.md", "wiki/concepts/b.md")
    assert out["wiki/concepts/a.md"] == "see [b](b.md)\n"
    assert "wiki/concepts/b.md" in out


def test_outbound_links_recomputed_when_file_moves():
    files = {
        "wiki/concepts/a.md": "see [b](b.md) and [c](../entities/c.md)\n",
        "wiki/concepts/b.md": "# B\n",
        "wiki/entities/c.md": "# C\n",
    }
    out = wikipage.plan_move(files, "wiki/concepts/a.md", "wiki/entities/a.md")
    assert out["wiki/entities/a.md"] == "see [b](../concepts/b.md) and [c](c.md)\n"


def test_anchor_preserved():
    files = {
        "wiki/concepts/a.md": "jump [x](../entities/b.md#section-2)\n",
        "wiki/entities/b.md": "# B\n",
    }
    out = wikipage.plan_move(files, "wiki/entities/b.md", "wiki/concepts/b.md")
    assert out["wiki/concepts/a.md"] == "jump [x](b.md#section-2)\n"


def test_image_embed_rewritten():
    files = {
        "wiki/concepts/a.md": "![pic](../raw/img.png)\n",
        "raw/img.png": "",
    }
    out = wikipage.plan_move(files, "wiki/concepts/a.md", "wiki/a.md")
    assert out["wiki/a.md"] == "![pic](raw/img.png)\n"


def test_link_inside_list_item():
    files = {
        "wiki/concepts/a.md": "- first\n- see [b](../entities/b.md)\n- last\n",
        "wiki/entities/b.md": "# B\n",
    }
    out = wikipage.plan_move(files, "wiki/entities/b.md", "wiki/concepts/b.md")
    assert out["wiki/concepts/a.md"] == "- first\n- see [b](b.md)\n- last\n"


def test_self_link_follows_the_move():
    files = {"wiki/concepts/a.md": "I link to [myself](a.md) here\n"}
    out = wikipage.plan_move(files, "wiki/concepts/a.md", "wiki/entities/a.md")
    assert out["wiki/entities/a.md"] == "I link to [myself](a.md) here\n"


def test_pure_rename_same_dir_updates_inbound_only():
    files = {
        "wiki/concepts/a.md": "see [old](old-name.md)\n",
        "wiki/concepts/old-name.md": "# Old\nlink to [a](a.md)\n",
    }
    out = wikipage.plan_move(files, "wiki/concepts/old-name.md", "wiki/concepts/new-name.md")
    assert out["wiki/concepts/a.md"] == "see [old](new-name.md)\n"
    assert out["wiki/concepts/new-name.md"] == "# Old\nlink to [a](a.md)\n"


def test_external_and_anchor_only_links_untouched():
    files = {
        "wiki/concepts/a.md": "[web](https://example.com/b.md) and [frag](#heading)\n",
        "wiki/entities/b.md": "# B\n",
    }
    out = wikipage.plan_move(files, "wiki/entities/b.md", "wiki/concepts/b.md")
    assert out["wiki/concepts/a.md"] == files["wiki/concepts/a.md"]


def test_unrelated_file_untouched_bytewise():
    files = {
        "wiki/concepts/a.md": "see [b](../entities/b.md)\n",
        "wiki/entities/b.md": "# B\n",
        "wiki/concepts/unrelated.md": "no links here, just [text] brackets\n",
    }
    out = wikipage.plan_move(files, "wiki/entities/b.md", "wiki/concepts/b.md")
    assert out["wiki/concepts/unrelated.md"] == files["wiki/concepts/unrelated.md"]


# --- plan_move: frontmatter-link regressions -----------------------------------


def _resolves(files: dict[str, str]) -> None:
    for rel, text in files.items():
        for lk in wikipage.iter_links(text):
            path = lk.decoded_path
            if "://" in path or path.startswith(("/", "#")) or path == "":
                continue
            target = posixpath.normpath(posixpath.join(posixpath.dirname(rel) or ".", path))
            assert target in files, f"dangling {lk.dest!r} in {rel} -> {target}"


def test_inbound_frontmatter_edge_rewritten_on_move():
    files = {
        "wiki/concepts/pooling.md": (
            "---\n"
            "title: Connection pooling\n"
            "refines:\n"
            '  - "[Prepared statements](../concepts/prepared-statements.md)"\n'
            "---\n"
            "# Pooling\n"
        ),
        "wiki/concepts/prepared-statements.md": "---\ntitle: PS\n---\n# PS\n",
    }
    out = wikipage.plan_move(
        files, "wiki/concepts/prepared-statements.md", "wiki/entities/prepared-statements.md"
    )
    assert (
        '  - "[Prepared statements](../entities/prepared-statements.md)"\n'
        in out["wiki/concepts/pooling.md"]
    )
    _resolves(out)


def test_outbound_frontmatter_edges_rebased_when_page_moves():
    files = {
        "wiki/concepts/a.md": (
            "---\n"
            "title: A\n"
            "related:\n"
            '  - "[B](../entities/b.md)"\n'
            "supersedes:\n"
            '  - "[Old A](a-old.md)"\n'
            "---\n"
            "# A\n"
        ),
        "wiki/entities/b.md": "---\ntitle: B\n---\n# B\n",
        "wiki/concepts/a-old.md": "---\ntitle: Old A\n---\n# old\n",
    }
    out = wikipage.plan_move(files, "wiki/concepts/a.md", "wiki/sources/a.md")
    moved = out["wiki/sources/a.md"]
    assert '  - "[B](../entities/b.md)"\n' in moved
    assert '  - "[Old A](../concepts/a-old.md)"\n' in moved
    _resolves(out)


def test_raw_source_survives_cross_dir_move():
    files = {
        "wiki/sources/x.md": (
            "---\n"
            "title: X source\n"
            'raw_source: "[x.md](../../raw/notes/x.md)"\n'
            "---\n"
            "# X\n"
        ),
        "raw/notes/x.md": "raw bytes\n",
    }
    out = wikipage.plan_move(files, "wiki/sources/x.md", "wiki/x.md")
    moved = out["wiki/x.md"]
    assert 'raw_source: "[x.md](../raw/notes/x.md)"\n' in moved
    target = posixpath.normpath(posixpath.join("wiki", "../raw/notes/x.md"))
    assert target == "raw/notes/x.md"


def test_same_dir_rename_leaves_raw_source_untouched():
    files = {
        "wiki/sources/deploy.md": (
            "---\n"
            "title: Deploy\n"
            'raw_source: "[deploy.md](../../raw/notes/deploy.md)"\n'
            "---\n"
            "# Deploy\n"
        ),
        "raw/notes/deploy.md": "raw\n",
    }
    out = wikipage.plan_move(files, "wiki/sources/deploy.md", "wiki/sources/deploy-github-actions.md")
    assert (
        'raw_source: "[deploy.md](../../raw/notes/deploy.md)"\n'
        in out["wiki/sources/deploy-github-actions.md"]
    )


# --- encoded links for raw/ files with special characters ---


def test_iter_links_encoded_destination():
    body = 'see [my file](raw/my%20file.txt) now\n'
    links = list(wikipage.iter_links(body))
    assert len(links) == 1
    lk = links[0]
    assert lk.dest == "raw/my%20file.txt"  # encoded form (invariant)
    assert lk.decoded_path == "raw/my file.txt"  # decoded for logic
    assert body[lk.start:lk.end] == "raw/my%20file.txt"


def test_iter_links_hash_in_filename():
    body = 'see [file](raw/file%231.txt) now\n'
    links = list(wikipage.iter_links(body))
    assert len(links) == 1
    lk = links[0]
    assert lk.dest == "raw/file%231.txt"
    assert lk.decoded_path == "raw/file#1.txt"
    # Offsets still point to the encoded form
    assert body[lk.start:lk.end] == "raw/file%231.txt"


def test_inbound_link_rewritten_with_encoded_raw_filename():
    files = {
        "wiki/sources/a.md": 'see [file](../../raw/my%20file.txt) now\n',
        "raw/my file.txt": "content",
    }
    # Rename the raw file in the vault model (encoded in the link)
    out = wikipage.plan_move(files, "raw/my file.txt", "raw/2026-01-01-0000-my file.txt")
    assert "raw/2026-01-01-0000-my%20file.txt" in out["wiki/sources/a.md"]


def test_encoded_link_with_anchor():
    body = 'see [file](raw/my%20file.md#section) now\n'
    links = list(wikipage.iter_links(body))
    assert len(links) == 1
    lk = links[0]
    assert lk.dest == "raw/my%20file.md#section"
    assert lk.decoded_path == "raw/my file.md"
    assert lk.decoded_anchor == "section"


def test_encoded_hash_in_filename_not_confused_with_anchor():
    # An encoded `#` (from a filename) must not be treated as the anchor
    # separator once a *literal* `#` anchor follows it.
    body = 'see [file](raw/file%231.md#section) now\n'
    links = list(wikipage.iter_links(body))
    assert len(links) == 1
    lk = links[0]
    assert lk.decoded_path == "raw/file#1.md"
    assert lk.decoded_anchor == "section"


def test_inbound_encoded_link_with_anchor_rewritten():
    files = {
        "wiki/sources/a.md": 'see [file](../../raw/my%20file.md#sec) now\n',
        "raw/my file.md": "content",
    }
    out = wikipage.plan_move(files, "raw/my file.md", "raw/other/my file.md")
    # The link should be rewritten with encoding preserved
    assert "raw/other/my%20file.md#sec" in out["wiki/sources/a.md"]


def test_raw_source_with_encoded_filename():
    files = {
        "wiki/sources/x.md": (
            "---\n"
            "title: X\n"
            'raw_source: "[my file.txt](../../raw/my%20file.txt)"\n'
            "---\n"
            "# X\n"
        ),
        "raw/my file.txt": "content",
    }
    out = wikipage.plan_move(files, "raw/my file.txt", "raw/2026-01-01-0000-my file.txt")
    assert 'raw_source: "[my file.txt](../../raw/2026-01-01-0000-my%20file.txt)"\n' in out["wiki/sources/x.md"]


def test_synthesis_source_edge_rewritten_others_byte_identical():
    files = {
        "wiki/synthesis/s.md": (
            "---\n"
            "title: S\n"
            "source:\n"
            '  - "[A](../concepts/a.md)"\n'
            '  - "[B](../concepts/b.md)"\n'
            "---\n"
            "# S\n"
        ),
        "wiki/concepts/a.md": "---\ntitle: A\n---\n# A\n",
        "wiki/concepts/b.md": "---\ntitle: B\n---\n# B\n",
    }
    out = wikipage.plan_move(files, "wiki/concepts/a.md", "wiki/entities/a.md")
    s = out["wiki/synthesis/s.md"]
    assert '  - "[A](../entities/a.md)"\n' in s
    assert '  - "[B](../concepts/b.md)"\n' in s
    _resolves(out)


def test_supersedes_link_rewritten_on_target_move():
    files = {
        "wiki/sources/new.md": (
            "---\n"
            "title: New deploy\n"
            "supersedes:\n"
            '  - "[Old deploy](old.md)"\n'
            "---\n"
            "# New\n"
        ),
        "wiki/sources/old.md": "---\ntitle: Old deploy\n---\n# old\n",
    }
    out = wikipage.plan_move(files, "wiki/sources/old.md", "wiki/concepts/old.md")
    assert '  - "[Old deploy](../concepts/old.md)"\n' in out["wiki/sources/new.md"]
    _resolves(out)


def test_tags_flow_list_not_mistaken_for_a_link():
    files = {
        "wiki/concepts/a.md": (
            "---\ntitle: A\ntags: [db, sql]\n"
            'related:\n  - "[B](../entities/b.md)"\n---\n# A\n'
        ),
        "wiki/entities/b.md": "---\ntitle: B\n---\n# B\n",
    }
    out = wikipage.plan_move(files, "wiki/entities/b.md", "wiki/concepts/b.md")
    assert "tags: [db, sql]\n" in out["wiki/concepts/a.md"]
    assert '  - "[B](b.md)"\n' in out["wiki/concepts/a.md"]


# --- plan_move: property test ---------------------------------------------------

_DIRS = ["wiki/concepts", "wiki/entities", "wiki/sources", "raw/notes"]
# Filenames with each char the percent-encoding charset covers, so the
# property test actually generates the hazard it's meant to guard: a name
# with `#` exercises the encoded-# vs. anchor-separator distinction.
_NAMES = ["a", "b", "c", "d", "e", "my file", "50%", "draft (v2)", "file#1"]
_MOVE_EDGE_KEYS = ["refines", "contradicts", "example-of", "source", "related"]


def _resolve(file_rel: str, decoded_path: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(file_rel) or ".", decoded_path))


@st.composite
def _vaults(draw):
    """A small vault whose relative links — in body *and* per-key frontmatter — resolve."""
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
            # Encode the destination to handle special chars in filenames
            dest = wikipage.percent_encode(dest)
            if with_anchor:
                dest += "#sec"
            return f"[{posixpath.basename(target)}]({dest})"

        fm = ["---", f"title: {posixpath.basename(rel)}", "tags: [x, y]"]
        edge_keys = draw(st.lists(st.sampled_from(_MOVE_EDGE_KEYS), unique=True, max_size=2))
        for key in edge_keys:
            edge_targets = draw(st.lists(st.sampled_from(others or [rel]), min_size=1, max_size=2))
            fm.append(f"{key}:")
            for t in edge_targets:
                fm.append(f'  - "{_md_link(t)}"')
        fm.append("---")

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
    assume_ok = new_rel == old_rel or new_rel not in files
    if not assume_ok:
        return

    out = wikipage.plan_move(files, old_rel, new_rel)

    for rel, text in out.items():
        for lk in wikipage.iter_links(text):
            path = lk.decoded_path
            if "://" in path or path.startswith(("/", "#")) or path == "":
                continue
            target = _resolve(rel, path)
            assert target in out, f"dangling link {lk.dest!r} in {rel} -> {target}"

    for rel, text in files.items():
        new_rel_for = new_rel if rel == old_rel else rel
        before = text.splitlines()
        after = out[new_rel_for].splitlines()
        assert len(before) == len(after)
        for b, a in zip(before, after):
            if b != a:
                assert "](" in b, f"non-link line changed in {rel}: {b!r} -> {a!r}"


# --- CLI: run as a real subprocess (regression for import cycles that only
# bite when wikipage.py is the executed file) --------------------------------


def test_cli_get_runs_as_subprocess(tmp_path):
    """``python wikipage.py get ...`` must work when wikipage.py is the
    *executed* file, not just when it's imported.

    Every test above imports wikipage as a library, so pytest always sees
    it under the module name ``wikipage`` — never as ``__main__``. That
    hid a real bug: wikipage.py used to import search_index (for the Vault
    facade's type hints), search_index imports page_record, and
    page_record imports wikipage back — a cycle that's harmless when
    wikipage is loaded once under one name, but broke when running
    ``python wikipage.py ...`` loaded it a *second* time under the name
    ``wikipage`` (triggered by page_record's ``import wikipage``), which
    re-entered search_index mid-initialization and raised an ImportError
    on a name search_index hadn't defined yet. Moving ``Vault`` out to
    vault.py dissolved that cycle — the dependency now runs one way,
    ``vault -> wikipage`` — but the double-load is structural, so this
    stays as the guard. Only a real subprocess invocation reproduces it;
    an in-process ``import`` never will.
    """
    import subprocess
    import sys

    # A bare file, not a vault: every wikipage.py subcommand takes a path and
    # does pure text work, so this CLI never resolves a vault root.
    page = tmp_path / "b.md"
    page.write_text("---\ntitle: B\n---\n# B\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, wikipage.__file__, "get", str(page), "title"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "B"
