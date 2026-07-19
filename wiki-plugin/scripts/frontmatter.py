"""Read and patch YAML frontmatter with ``ruamel.yaml`` round-trip fidelity.

Only the frontmatter block is ever re-serialised — the body is spliced back
byte-for-byte. Within the block, ruamel's round-trip loader preserves key
order, comments, quoting and flow style, so patching one key leaves the others
untouched. This is why we use ``ruamel.yaml`` and **not** ``pyyaml`` (which
reorders keys, drops comments and coerces types).

CLI::

    python frontmatter.py get <file> <key>
    python frontmatter.py set <file> <key> <value> [--json]

``set`` rewrites ``<file>`` in place. ``--json`` parses ``<value>`` as JSON so
list/scalar values (e.g. ``tags``) can be set from the shell.
"""
from __future__ import annotations

import argparse
import json
import sys
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML

from lib import md


def _yaml() -> YAML:
    y = YAML()  # typ='rt' — round-trip, preserves formatting
    y.preserve_quotes = True
    y.width = 4096  # never line-wrap long scalars
    # Match the conventions-spec indentation so block sequences (the per-type
    # edge keys, `supersedes`, …) round-trip byte-for-byte: `  - "[t](p.md)"`.
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _load(fm_text: str):
    if fm_text.strip() == "":
        return _yaml().load("{}\n")
    return _yaml().load(fm_text)


def _dump(data) -> str:
    buf = StringIO()
    _yaml().dump(data, buf)
    return buf.getvalue()


def get(text: str, key: str):
    """Return the value of ``key`` in ``text``'s frontmatter, or ``None``."""
    fm, _body, _offset = md.split_frontmatter(text)
    if fm is None:
        return None
    data = _load(fm)
    if data is None:
        return None
    return data.get(key)


def load(text: str) -> dict | None:
    """Return ``text``'s full frontmatter mapping, or ``None`` if it has none.

    Unlike :func:`get`, this hands back every key at once — used by callers
    (``build_index.py``) that need the whole record rather than one field.
    """
    fm, _body, _offset = md.split_frontmatter(text)
    if fm is None:
        return None
    return _load(fm)


def set(text: str, key: str, value) -> str:
    """Return ``text`` with frontmatter ``key`` set to ``value``.

    Creates a frontmatter block if the document has none. Only the block is
    reformatted; the body is preserved exactly.
    """
    fm, body, _offset = md.split_frontmatter(text)
    if fm is None:
        # No frontmatter yet — mint a fresh block ahead of the untouched body.
        data = _load("")
        data[key] = value
        return "---\n" + _dump(data) + "---\n" + text
    data = _load(fm)
    data[key] = value
    return "---\n" + _dump(data) + "---\n" + body


# --- CLI ---------------------------------------------------------------------

def _main(argv=None) -> int:  # pragma: no cover - thin CLI wrapper
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("get", help="print a frontmatter value")
    g.add_argument("file")
    g.add_argument("key")

    s = sub.add_parser("set", help="set a frontmatter value in place")
    s.add_argument("file")
    s.add_argument("key")
    s.add_argument("value")
    s.add_argument("--json", action="store_true", help="parse value as JSON")

    args = parser.parse_args(argv)
    path = Path(args.file)
    text = path.read_text(encoding="utf-8")

    if args.cmd == "get":
        value = get(text, args.key)
        if value is None:
            return 1
        print(value)
        return 0

    value = json.loads(args.value) if args.json else args.value
    path.write_text(set(text, args.key, value), encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
