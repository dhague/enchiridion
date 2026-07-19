"""TDD for frontmatter.py — ruamel round-trip get/set.

The risk this module carries: a naive YAML round-trip reformats the *whole*
document (reorders keys, drops comments, re-quotes, coerces dates). The
property test below is the guard — see ``test_prop_untouched_key_byte_identical``.
"""
from io import StringIO

from hypothesis import given, settings
from hypothesis import strategies as st
from ruamel.yaml import YAML

import frontmatter


def _canonical_fm(mapping: dict) -> str:
    """Render a mapping to the exact frontmatter text ruamel would emit."""
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    buf = StringIO()
    y.dump(mapping, buf)
    return buf.getvalue()


# --- get ---------------------------------------------------------------------

def test_get_existing_key():
    text = "---\ntitle: Prepared statements\nvolatility: stable\n---\nbody\n"
    assert frontmatter.get(text, "title") == "Prepared statements"
    assert frontmatter.get(text, "volatility") == "stable"


def test_get_missing_key_returns_none():
    text = "---\ntitle: Foo\n---\nbody\n"
    assert frontmatter.get(text, "nope") is None


def test_get_no_frontmatter_returns_none():
    assert frontmatter.get("# just a body\n", "title") is None


# --- set ---------------------------------------------------------------------

def test_set_updates_value_and_get_roundtrips():
    text = "---\ntitle: Foo\nvolatility: stable\n---\nbody\n"
    out = frontmatter.set(text, "volatility", "evolving")
    assert frontmatter.get(out, "volatility") == "evolving"
    assert frontmatter.get(out, "title") == "Foo"


def test_set_adds_new_key():
    text = "---\ntitle: Foo\n---\nbody\n"
    out = frontmatter.set(text, "source_date", "2026-03-01")
    assert frontmatter.get(out, "source_date") == "2026-03-01"
    assert "title: Foo" in out


def test_set_preserves_body_exactly_including_thematic_break():
    body = "# Heading\n\nsome text\n\n---\n\nafter the rule\n"
    text = "---\ntitle: Foo\n---\n" + body
    out = frontmatter.set(text, "title", "Bar")
    # Body after the frontmatter must be byte-identical.
    assert out.endswith(body)


def test_set_preserves_comment_on_other_key():
    text = "---\ntitle: Foo\nvolatility: stable  # do not lose me\n---\nbody\n"
    out = frontmatter.set(text, "title", "Bar")
    assert "# do not lose me" in out


def test_set_preserves_flow_list_on_other_key():
    text = "---\ntitle: Foo\ntags: [db, sql]\n---\nbody\n"
    out = frontmatter.set(text, "title", "Bar")
    assert "tags: [db, sql]" in out


def test_noop_set_is_byte_identical():
    text = "---\ntitle: Foo\ntags: [db, sql]\nvolatility: stable\n---\n# Body\n\nhi\n"
    out = frontmatter.set(text, "title", "Foo")
    assert out == text


def test_get_per_type_edge_key_returns_link_list():
    # Amended schema (19be866): typed edges are one key per type, a list of
    # quoted markdown links.
    text = (
        "---\n"
        "title: Pooling\n"
        "refines:\n"
        '  - "[Prepared statements](../concept/prepared-statements.md)"\n'
        '  - "[Indexing](../concept/indexing.md)"\n'
        "---\n"
        "# Body\n"
    )
    edges = frontmatter.get(text, "refines")
    assert list(edges) == [
        "[Prepared statements](../concept/prepared-statements.md)",
        "[Indexing](../concept/indexing.md)",
    ]


def test_get_raw_source_returns_single_link():
    text = (
        "---\n"
        "title: X\n"
        'raw_source: "[x.md](../../raw/notes/x.md)"\n'
        "---\n"
        "# X\n"
    )
    assert frontmatter.get(text, "raw_source") == "[x.md](../../raw/notes/x.md)"


def test_set_scalar_leaves_edge_and_raw_source_blocks_byte_identical():
    text = (
        "---\n"
        "title: X source\n"
        "summary: old summary\n"
        'raw_source: "[x.md](../../raw/notes/x.md)"\n'
        "source:\n"
        '  - "[A](../concept/a.md)"\n'
        '  - "[B](../concept/b.md)"\n'
        "---\n"
        "# X\n"
    )
    out = frontmatter.set(text, "summary", "new summary")
    assert frontmatter.get(out, "summary") == "new summary"
    # The raw_source line and the whole source: edge block survive untouched.
    assert 'raw_source: "[x.md](../../raw/notes/x.md)"\n' in out
    assert (
        "source:\n"
        '  - "[A](../concept/a.md)"\n'
        '  - "[B](../concept/b.md)"\n'
    ) in out


def test_set_creates_frontmatter_when_absent():
    text = "# just a body\n"
    out = frontmatter.set(text, "title", "New")
    assert frontmatter.get(out, "title") == "New"
    assert out.endswith("# just a body\n")


# --- load ----------------------------------------------------------------

def test_load_returns_full_mapping():
    text = (
        "---\n"
        "title: Pooling\n"
        "tags: [db, perf]\n"
        "related:\n"
        '  - "[B](../entity/b.md)"\n'
        "---\n"
        "# Body\n"
    )
    data = frontmatter.load(text)
    assert data["title"] == "Pooling"
    assert list(data["tags"]) == ["db", "perf"]
    assert list(data["related"]) == ["[B](../entity/b.md)"]


def test_load_no_frontmatter_returns_none():
    assert frontmatter.load("# just a body\n") is None


# --- property: the reformatting-stringifier guard ----------------------------

# YAML keywords that ruamel must quote when used as a plain scalar key/value —
# excluded so generated frontmatter is unambiguous and its raw text is canonical
# (this test isolates round-trip fidelity, not ruamel's scalar disambiguation).
_YAML_KEYWORDS = {"true", "false", "yes", "no", "on", "off", "null", "y", "n"}

_ident = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz_",
    min_size=1,
    max_size=8,
).filter(lambda s: s not in _YAML_KEYWORDS)

_scalar = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    min_size=1,
    max_size=20,
).map(str.strip).filter(bool)


@settings(max_examples=200)
@given(
    mapping=st.dictionaries(_ident, _scalar, min_size=1, max_size=6),
    body=st.text(alphabet="abc \n#", max_size=40),
)
def test_prop_untouched_key_byte_identical(mapping, body):
    """Re-writing a key with its own value leaves every byte identical."""
    text = "---\n" + _canonical_fm(mapping) + "---\n" + body
    for key, value in mapping.items():
        out = frontmatter.set(text, key, value)
        assert out == text, f"no-op set of {key!r} was not byte-identical"


_link = st.builds(
    lambda title, path: f'"[{title}]({path}.md)"',
    st.sampled_from(["A", "B", "C", "Prepared statements"]),
    st.sampled_from(["../concept/a", "../entity/b", "../../raw/notes/x"]),
)

_EDGE_KEYS = {"refines", "source", "related", "supersedes"}
# Disjoint from _EDGE_KEYS so a drawn scalar key can never collide with a
# drawn edge key and produce two identical keys in one YAML document.
_scalar_key = _ident.filter(lambda s: s not in _EDGE_KEYS)


# Values that ruamel emits unquoted and reads back identically, so raw
# `key: value` text is already canonical — isolating the indentation invariant
# from scalar-quoting noise.
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
        assert frontmatter.set(text, key, value) == text


@settings(max_examples=200)
@given(
    mapping=st.dictionaries(_ident, _scalar, min_size=2, max_size=6),
    new_value=_scalar,
)
def test_prop_changing_one_key_leaves_other_lines_identical(mapping, new_value):
    """Changing one key never disturbs the physical lines of the others."""
    text = "---\n" + _canonical_fm(mapping) + "---\nbody\n"
    target = next(iter(mapping))
    out = frontmatter.set(text, target, new_value)
    before = text.splitlines()
    after = out.splitlines()
    assert len(before) == len(after)
    for b, a in zip(before, after):
        # The only line permitted to differ is the target key's line.
        if b.startswith(f"{target}:"):
            continue
        assert b == a
