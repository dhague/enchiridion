/**
 * chainofevidence tests — mirror enchiridion-go/internal/chainofevidence/
 * chainofevidence_test.go, ported to the TS module.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { Page } from "./wikipage.js";
import { check } from "./chainofevidence.js";

function page(frontmatter: string): Page {
  return new Page(`---\n${frontmatter}---\nbody\n`);
}

const rawRef = "raw/doc.md";

test("chain holds with a stub and a back edge", () => {
  const staged: Record<string, Page> = {
    "wiki/sources/doc.md": page('raw_source: "[doc.md](../../raw/doc.md)"\n'),
    "wiki/concepts/a.md": page('source:\n  - "[doc.md](../sources/doc.md)"\n'),
  };
  const errs = check(staged, rawRef);
  assert.deepEqual(errs, []);
});

test("missing stub is reported", () => {
  const staged: Record<string, Page> = {
    "wiki/concepts/a.md": page("title: A\n"),
  };
  const errs = check(staged, rawRef);
  assert.equal(errs.length, 1);
  assert.match(errs[0], /needs a sources\/ page/);
});

test("a sources/ page pointing elsewhere does not count as the stub", () => {
  const staged: Record<string, Page> = {
    "wiki/sources/other.md": page(
      'raw_source: "[other.md](../../raw/other.md)"\n',
    ),
  };
  const errs = check(staged, rawRef);
  assert.equal(errs.length, 1);
  assert.match(errs[0], /needs a sources\/ page/);
});

test("a page without a source edge is reported", () => {
  const staged: Record<string, Page> = {
    "wiki/sources/doc.md": page('raw_source: "[doc.md](../../raw/doc.md)"\n'),
    "wiki/concepts/a.md": page("title: A\n"),
    "wiki/concepts/b.md": page('source:\n  - "[doc.md](../sources/doc.md)"\n'),
  };
  const errs = check(staged, rawRef);
  assert.equal(errs.length, 1);
  assert.match(errs[0], /wiki\/concepts\/a\.md needs a source edge/);
});

test("raw ref is normalized and destination decoded", () => {
  const staged: Record<string, Page> = {
    "wiki/sources/doc.md": page(
      'raw_source: "[a doc.md](../../raw/notes/../a%20doc.md)"\n',
    ),
  };
  const errs = check(staged, "raw/./a doc.md");
  assert.deepEqual(errs, []);
});

test("errors are deterministically ordered", () => {
  const staged: Record<string, Page> = {
    "wiki/sources/doc.md": page('raw_source: "[doc.md](../../raw/doc.md)"\n'),
    "wiki/concepts/c.md": page("title: C\n"),
    "wiki/concepts/a.md": page("title: A\n"),
    "wiki/concepts/b.md": page("title: B\n"),
  };
  for (let i = 0; i < 5; i++) {
    const errs = check(staged, rawRef);
    const got = errs.join("|");
    assert.ok(got.startsWith("wiki/concepts/a.md"), got);
    assert.ok(got.includes("|wiki/concepts/b.md"), got);
    assert.ok(got.includes("|wiki/concepts/c.md"), got);
  }
});

test("invalid frontmatter is an error", () => {
  const staged: Record<string, Page> = {
    "wiki/sources/doc.md": new Page("---\nraw_source: [unclosed\n---\nbody\n"),
  };
  assert.throws(() => check(staged, rawRef));
});
