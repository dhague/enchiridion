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


def test_set_creates_frontmatter_when_absent():
    text = "# just a body\n"
    out = frontmatter.set(text, "title", "New")
    assert frontmatter.get(out, "title") == "New"
    assert out.endswith("# just a body\n")


# --- property: the reformatting-stringifier guard ----------------------------

_ident = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz_",
    min_size=1,
    max_size=8,
).filter(lambda s: not s[0].isdigit())

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
