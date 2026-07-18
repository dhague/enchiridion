"""TDD for lib/md.py — frontmatter split + AST-positioned link discovery."""
from lib import md


# --- split_frontmatter -------------------------------------------------------

def test_split_with_frontmatter():
    text = "---\ntitle: Foo\ntags: [a, b]\n---\n# Body\n\nhello\n"
    fm, body, offset = md.split_frontmatter(text)
    assert fm == "title: Foo\ntags: [a, b]\n"
    assert body == "# Body\n\nhello\n"
    assert text[offset:] == body


def test_split_without_frontmatter():
    text = "# Just a body\n\nno frontmatter here\n"
    fm, body, offset = md.split_frontmatter(text)
    assert fm is None
    assert body == text
    assert offset == 0


def test_thematic_break_is_not_frontmatter():
    # A `---` that is not on the very first line is a horizontal rule, not frontmatter.
    text = "# Title\n\n---\n\nbody\n"
    fm, body, offset = md.split_frontmatter(text)
    assert fm is None
    assert offset == 0


def test_empty_frontmatter_block():
    text = "---\n---\nbody\n"
    fm, body, offset = md.split_frontmatter(text)
    assert fm == ""
    assert body == "body\n"


# --- parse / source positions ------------------------------------------------

def test_parse_returns_tokens_with_line_maps():
    tokens = md.parse("# Heading\n\npara text\n")
    # Block tokens carry a `map` = [start_line, end_line]; that is the source position.
    mapped = [t for t in tokens if t.map is not None]
    assert mapped, "expected at least one token carrying a source map"


def test_code_line_ranges_covers_fenced_block():
    body = "before\n\n```\nlink [x](y.md) inside code\n```\n\nafter\n"
    code_lines = md.code_line_ranges(body)
    # The fenced line with the fake link must be flagged as code.
    fence_content_line = body.splitlines().index("link [x](y.md) inside code")
    assert fence_content_line in code_lines
    assert 0 not in code_lines  # "before" is not code


# --- iter_links --------------------------------------------------------------

def test_iter_links_plain_link_offsets():
    body = "see [the page](concept/foo.md) now\n"
    links = list(md.iter_links(body))
    assert len(links) == 1
    lk = links[0]
    assert lk.dest == "concept/foo.md"
    assert body[lk.start:lk.end] == "concept/foo.md"
    assert lk.is_image is False


def test_iter_links_image_embed():
    body = "![alt text](assets/pic.png)\n"
    links = list(md.iter_links(body))
    assert len(links) == 1
    assert links[0].is_image is True
    assert links[0].dest == "assets/pic.png"
    assert body[links[0].start:links[0].end] == "assets/pic.png"


def test_iter_links_preserves_anchor_in_dest():
    body = "jump [here](entity/bar.md#section-2)\n"
    (lk,) = list(md.iter_links(body))
    assert lk.dest == "entity/bar.md#section-2"
    assert body[lk.start:lk.end] == "entity/bar.md#section-2"


def test_iter_links_skips_code_fence():
    body = "real [a](one.md)\n\n```\nfake [b](two.md)\n```\n"
    dests = [lk.dest for lk in md.iter_links(body)]
    assert dests == ["one.md"]


def test_iter_links_multiple_same_line_distinct_offsets():
    body = "[a](x.md) and [b](x.md) and [c](y.md)\n"
    links = list(md.iter_links(body))
    assert [lk.dest for lk in links] == ["x.md", "x.md", "y.md"]
    # Offsets must be distinct and monotonically increasing.
    starts = [lk.start for lk in links]
    assert starts == sorted(starts)
    assert len(set(starts)) == 3
    for lk in links:
        assert body[lk.start:lk.end] == lk.dest


def test_iter_links_ignores_title_after_dest():
    body = '[a](path.md "a title")\n'
    (lk,) = list(md.iter_links(body))
    assert lk.dest == "path.md"
    assert body[lk.start:lk.end] == "path.md"
