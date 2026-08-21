/**
 * Tests for the wikipage module, with the two property-tested contracts
 * (page-move and frontmatter round-trip, ADR-0012) guarded by fast-check.
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fc from "fast-check";
import path from "node:path";
import {
  Page,
  percentEncode,
  percentDecode,
  splitDest,
  splitFrontmatter,
  iterLinks,
  linkDest,
  resolveLinkDest,
  composeLink,
  normalizeBodyLinks,
  planMove,
} from "./wikipage.js";
import type { LinkMatch } from "./wikipage.js";

// ---------------------------------------------------------------------------
// splitFrontmatter
// ---------------------------------------------------------------------------

describe("splitFrontmatter", () => {
  const cases = [
    {
      name: "leading block",
      text: "---\ntitle: A\n---\nbody\n",
      fm: "title: A\n",
      body: "body\n",
      present: true,
    },
    {
      name: "empty block",
      text: "---\n---\nbody\n",
      fm: "",
      body: "body\n",
      present: true,
    },
    {
      name: "no frontmatter",
      text: "body\n---\nnot metadata\n",
      fm: "",
      body: "body\n---\nnot metadata\n",
      present: false,
    },
    {
      name: "thematic break mid-document is not frontmatter",
      text: "# Title\n\n---\n\nmore\n",
      fm: "",
      body: "# Title\n\n---\n\nmore\n",
      present: false,
    },
    {
      name: "closing fence at end of file",
      text: "---\ntitle: A\n---",
      fm: "title: A\n",
      body: "",
      present: true,
    },
    {
      name: "CRLF line endings — parsed identically to LF",
      text: "---\r\ntitle: A\r\n---\r\nbody\r\n",
      fm: "title: A\r\n",
      body: "body\r\n",
      present: true,
    },
    {
      name: "CRLF with multiple frontmatter fields",
      text: '---\r\ntitle: Foo\r\nraw_source: "[f.txt](../../raw/f.txt)"\r\n---\r\nbody\r\n',
      fm: 'title: Foo\r\nraw_source: "[f.txt](../../raw/f.txt)"\r\n',
      body: "body\r\n",
      present: true,
    },
    {
      name: "CRLF closing fence at end of file",
      text: "---\r\ntitle: A\r\n---",
      fm: "title: A\r\n",
      body: "",
      present: true,
    },
  ];
  for (const tc of cases) {
    it(tc.name, () => {
      const r = splitFrontmatter(tc.text);
      assert.equal(r.hasFrontmatter, tc.present);
      assert.equal(r.frontmatter, tc.fm);
      assert.equal(r.body, tc.body);
      assert.equal(tc.text.slice(r.bodyOffset), r.body);
    });
  }
});

// ---------------------------------------------------------------------------
// PercentEncode / PercentDecode / SplitDest
// ---------------------------------------------------------------------------

describe("percentEncode", () => {
  it("encodes only the minimal charset", () => {
    const got = percentEncode("raw/Über & Co's, notes (draft) #1 <x>.md");
    const want =
      "raw/Über%20&%20Co's,%20notes%20%28draft%29%20%231%20%3Cx%3E.md";
    assert.equal(got, want);
  });
});

describe("percentDecode", () => {
  it("leaves invalid escapes verbatim", () => {
    for (const input of ["100%", "a%zz", "trailing%2"]) {
      assert.equal(percentDecode(input), input);
    }
  });

  it("round-trips any string", () => {
    fc.assert(
      fc.property(fc.string(), (s) => {
        assert.equal(percentDecode(percentEncode(s)), s);
      }),
    );
  });
});

describe("splitDest", () => {
  it("splits before decoding", () => {
    // The whole point of the single decode boundary: an encoded `#` in a
    // filename must not be mistaken for an anchor separator.
    assert.deepEqual(splitDest("raw/notes%20%231.md"), {
      path: "raw/notes #1.md",
      anchor: "",
    });
    assert.deepEqual(splitDest("wiki/concepts/a.md#some%20heading"), {
      path: "wiki/concepts/a.md",
      anchor: "some heading",
    });
  });
});

// ---------------------------------------------------------------------------
// IterLinks
// ---------------------------------------------------------------------------

describe("iterLinks", () => {
  it("finds links in frontmatter and body, skipping code blocks", () => {
    const text = [
      "---",
      'raw_source: "[notes.md](../../raw/notes%20%281%29.md)"',
      "---",
      "",
      "A [link](../entities/x.md) and an ![image](img/y.png).",
      "An <angle> one: [t](<a b.md>).",
      'A titled one: [t](z.md "the title").',
      "",
      "```",
      "[not a link](nope.md)",
      "```",
      "",
      "    [indented code](nope2.md)",
      "",
      "[external](https://example.com/a(b)c).",
    ].join("\n");

    const dests = iterLinks(text).map((m) => m.decodedPath);
    for (const m of iterLinks(text)) {
      assert.equal(text.slice(m.start, m.end), m.dest);
    }

    assert.deepEqual(dests, [
      "../../raw/notes (1).md",
      "../entities/x.md",
      "img/y.png",
      "a b.md",
      "z.md",
      "https://example.com/a(b)c",
    ]);
  });

  it("marks images", () => {
    const links = iterLinks("[a](a.md) ![b](b.png)");
    assert.equal(links.length, 2);
    assert.equal(links[0].isImage, false);
    assert.equal(links[1].isImage, true);
  });
});

describe("linkDest", () => {
  it("extracts a decoded destination", () => {
    assert.deepEqual(linkDest("[Some page](../concepts/some-page.md)"), {
      dest: "../concepts/some-page.md",
      ok: true,
    });
    assert.deepEqual(linkDest("just a string"), { dest: "", ok: false });
  });
});

describe("resolveLinkDest", () => {
  const cases: Array<[string, string, string]> = [
    ["../entities/x.md", "wiki/concepts", "wiki/entities/x.md"],
    ["a.md", "wiki/concepts", "wiki/concepts/a.md"],
    ["../../raw/n.md", "wiki/sources", "raw/n.md"],
    ["wiki/concepts/a.md", "", "wiki/concepts/a.md"],
    ["./a.md", "wiki/concepts", "wiki/concepts/a.md"],
  ];
  for (const [dest, pageDir, want] of cases) {
    it(`resolveLinkDest(${dest}, ${pageDir})`, () => {
      assert.equal(resolveLinkDest(dest, pageDir), want);
    });
  }
});

// ---------------------------------------------------------------------------
// Page get/set/merge
// ---------------------------------------------------------------------------

describe("Page.set", () => {
  it("mints a frontmatter block", () => {
    const page = new Page("body text\n").set("title", "A Page");
    assert.equal(page.text, "---\ntitle: A Page\n---\nbody text\n");
  });

  it("preserves key order and body", () => {
    const src =
      "---\ntitle: A\nsummary: s\nvolatility: stable\n---\n\n# Heading\n\nbody\n";
    const page = new Page(src).set("summary", "new summary");
    assert.equal(
      page.text,
      "---\ntitle: A\nsummary: new summary\nvolatility: stable\n---\n\n# Heading\n\nbody\n",
    );
  });

  it("appends a new key at the end", () => {
    const page = new Page("---\ntitle: A\n---\nbody\n").set(
      "volatility",
      "stable",
    );
    assert.ok(page.text.startsWith("---\ntitle: A\nvolatility: stable\n---\n"));
  });

  it("renders a link list at spec indentation", () => {
    const page = new Page("---\ntitle: A\n---\nbody\n").set("source", [
      "[Stub](../sources/stub.md)",
    ]);
    assert.equal(
      page.text,
      '---\ntitle: A\nsource:\n  - "[Stub](../sources/stub.md)"\n---\nbody\n',
    );
  });

  it("leaves non-link scalars unquoted", () => {
    const page = new Page("").set("tags", ["deploy", "ci"]);
    assert.ok(!page.text.includes('"'));
  });
});

describe("Page.merge", () => {
  it("unions preserving order", () => {
    const page = new Page("---\ntags:\n  - a\n  - b\n---\nbody\n").mergeStrings(
      "tags",
      ["b", "c"],
    );
    assert.deepEqual(page.getStringList("tags"), ["a", "b", "c"]);
  });

  it("behaves like set on an absent key", () => {
    const page = new Page("---\ntitle: A\n---\nbody\n").mergeStrings("tags", [
      "x",
    ]);
    assert.deepEqual(page.getStringList("tags"), ["x"]);
  });
});

describe("Page.get", () => {
  it("is absent without frontmatter or for a missing key", () => {
    assert.equal(new Page("body\n").get("title").ok, false);
    assert.equal(new Page("---\ntitle: A\n---\n").get("summary").ok, false);
  });

  it("errors on invalid YAML", () => {
    assert.throws(() =>
      new Page("---\ntitle: [unclosed\n---\nbody\n").get("title"),
    );
  });
});

describe("Page.frontmatter", () => {
  it("is null without a block", () => {
    assert.equal(new Page("body\n").frontmatter(), null);
  });
});

// ---------------------------------------------------------------------------
// ComposeLink / NormalizeBodyLinks / Retarget
// ---------------------------------------------------------------------------

describe("composeLink", () => {
  const cases: Array<[string, string, string, string]> = [
    [
      "Foo",
      "wiki/concepts/foo.md",
      "wiki/synthesis",
      "[Foo](../concepts/foo.md)",
    ],
    ["Foo", "wiki/concepts/foo.md", "wiki/concepts", "[Foo](foo.md)"],
    [
      "raw doc.md",
      "raw/raw doc.md",
      "wiki/sources",
      "[raw doc.md](../../raw/raw%20doc.md)",
    ],
    ["Foo", "wiki/concepts/foo.md", "", "[Foo](wiki/concepts/foo.md)"],
  ];
  for (const [title, target, pageDir, want] of cases) {
    it(`composeLink(${title}, ${target}, ${pageDir})`, () => {
      assert.equal(composeLink(title, target, pageDir), want);
    });
  }
});

describe("normalizeBodyLinks", () => {
  it("is idempotent and leaves absolute/external/anchor alone", () => {
    const src =
      "See [raw](../../raw/spec(v2).md) and [ext](https://example.com/x(1)) and [a](#anchor).\n";
    const once = normalizeBodyLinks(src);
    assert.ok(once.includes("spec%28v2%29.md"));
    assert.ok(once.includes("https://example.com/x(1)"));
    assert.ok(once.includes("](#anchor)"));
    assert.equal(normalizeBodyLinks(once), once);
  });
});

describe("PlanMove", () => {
  it("fixes inbound and outbound links, including frontmatter", () => {
    const pages = {
      "wiki/concepts/a.md":
        '---\nrelated:\n  - "[B](b.md)"\n---\nSee [B](b.md).\n',
      "wiki/concepts/b.md": "Back to [A](a.md).\n",
    };
    const moved = planMove(pages, "wiki/concepts/b.md", "wiki/entities/b.md");

    assert.equal(moved["wiki/concepts/b.md"], undefined);
    assert.ok(moved["wiki/concepts/a.md"].includes("(../entities/b.md)"));
    assert.ok(moved["wiki/entities/b.md"].includes("(../concepts/a.md)"));
  });

  it("leaves absolute and external destinations alone", () => {
    const src = "[x](/abs/b.md) [y](https://example.com/b.md) [z](#anchor)\n";
    const got = new Page(src).retarget(
      "wiki/concepts/a.md",
      "wiki/concepts/b.md",
      "wiki/entities/b.md",
    );
    assert.equal(got.text, src);
  });

  it("preserves anchors", () => {
    const src = "[B](b.md#a%20section)\n";
    const got = new Page(src).retarget(
      "wiki/concepts/a.md",
      "wiki/concepts/b.md",
      "wiki/entities/b.md",
    );
    assert.equal(got.text, "[B](../entities/b.md#a%20section)\n");
  });

  it("skips links in code blocks", () => {
    const src = "```\n[B](b.md)\n```\n";
    const got = new Page(src).retarget(
      "wiki/concepts/a.md",
      "wiki/concepts/b.md",
      "wiki/entities/b.md",
    );
    assert.equal(got.text, src);
  });
});

// ---------------------------------------------------------------------------
// Property tests — the page-move and frontmatter contracts (ADR-0012)
// ---------------------------------------------------------------------------

// vaultDirs are the directories a generated page may live in — enough shape
// variation (sibling, cousin, vault root) to exercise every `../` case.
const vaultDirs = [
  "wiki/concepts",
  "wiki/entities",
  "wiki/sources",
  "wiki/synthesis",
  "",
];

function posixBasename(ref: string): string {
  return path.posix.basename(ref);
}
function posixDirname(ref: string): string {
  return path.posix.dirname(ref);
}

// genVault draws a small vault of pages that link to each other, plus the
// old/new ref of a move to plan over it.
const genVaultArb = fc
  .record({
    names: fc.shuffledSubarray(["a", "b", "c", "d"], {
      minLength: 2,
      maxLength: 4,
    }),
    dirs: fc.array(fc.constantFrom(...vaultDirs), {
      minLength: 1,
      maxLength: 4,
    }),
    newDir: fc.constantFrom(...vaultDirs),
    oldIdx: fc.integer({ min: 0, max: 3 }),
  })
  .map(({ names, dirs, newDir, oldIdx }) => {
    const refs = names.map((name, i) =>
      path.posix.join(dirs[i % dirs.length], `${name}.md`),
    );
    const oldRel = refs[Math.min(oldIdx, refs.length - 1)];
    const newRel = path.posix.join(newDir, posixBasename(oldRel));
    const pages = buildVault(refs);
    return { pages, oldRel, newRel };
  })
  .filter(
    ({ pages, oldRel, newRel }) =>
      newRel === oldRel || !Object.prototype.hasOwnProperty.call(pages, newRel),
  );

/** Build a small vault of pages that link to each other, plus a frozen link
 * inside a code block. */
function buildVault(refs: string[]): Record<string, string> {
  const pages: Record<string, string> = {};
  for (const ref of refs) {
    const base = posixBasename(ref);
    const dir = posixDirname(ref);
    let b = `---\ntitle: ${base}\nrelated:\n`;
    for (const target of refs) {
      b += `  - "[t](${percentEncode(relPathForTest(target, dir))})"\n`;
    }
    b += "---\n\n";
    for (const target of refs) {
      b += `Body link [t](${percentEncode(relPathForTest(target, dir))})\n`;
    }
    b += "\n```\n[frozen](never-touched.md)\n```\n";
    pages[ref] = b;
  }
  return pages;
}

/** A minimal relPath for test fixture generation (mirrors the module's). */
function relPathForTest(target: string, base: string): string {
  const targetParts = posixPathParts(target);
  const baseParts = posixPathParts(base);
  let common = 0;
  while (
    common < targetParts.length &&
    common < baseParts.length &&
    targetParts[common] === baseParts[common]
  ) {
    common++;
  }
  const parts: string[] = [];
  for (let i = common; i < baseParts.length; i++) parts.push("..");
  parts.push(...targetParts.slice(common));
  return parts.length === 0 ? "." : parts.join("/");
}

function posixPathParts(p: string): string[] {
  const cleaned = path.posix.normalize(p);
  if (cleaned === "." || cleaned === "") return [];
  return cleaned.split("/");
}

/** Every link in text resolved from pageDir — the "where does this page point"
 * fact a move must leave unchanged. */
function resolvedTargets(text: string, pageDir: string): string[] {
  const out: string[] = [];
  for (const link of iterLinks(text)) {
    if (isRelativeDestForTest(link))
      out.push(resolveLinkDest(link.decodedPath, pageDir));
  }
  return out;
}

function isRelativeDestForTest(link: LinkMatch): boolean {
  return (
    link.decodedPath !== "" &&
    !link.decodedPath.startsWith("/") &&
    !link.decodedPath.startsWith("#") &&
    !link.decodedPath.includes("://")
  );
}

const MOVE_NUM_RUNS = 100;

describe("move preserves every link target", () => {
  it("after the move, every link resolves to what it pointed at before", () => {
    fc.assert(
      fc.property(genVaultArb, ({ pages, oldRel, newRel }) => {
        const moved = planMove(pages, oldRel, newRel);
        for (const [ref, before] of Object.entries(pages)) {
          const afterRef = ref === oldRel ? newRel : ref;
          const after = moved[afterRef];
          assert.ok(
            after !== undefined,
            `page ${afterRef} missing after the move`,
          );

          const wantTargets = resolvedTargets(before, posixDirname(ref));
          const gotTargets = resolvedTargets(after, posixDirname(afterRef));
          assert.equal(
            wantTargets.length,
            gotTargets.length,
            `${ref}: link count changed`,
          );
          for (let i = 0; i < wantTargets.length; i++) {
            let want = wantTargets[i];
            if (want === oldRel) want = newRel;
            assert.equal(gotTargets[i], want, `${ref}: link ${i}`);
          }
        }
      }),
      { numRuns: MOVE_NUM_RUNS },
    );
  });
});

describe("move touches only link lines", () => {
  it("every line that changed must have held a link whose destination moved", () => {
    fc.assert(
      fc.property(genVaultArb, ({ pages, oldRel, newRel }) => {
        const moved = planMove(pages, oldRel, newRel);
        for (const [ref, before] of Object.entries(pages)) {
          const afterRef = ref === oldRel ? newRel : ref;
          const after = moved[afterRef];

          const beforeLines = before.split("\n");
          const afterLines = after.split("\n");
          assert.equal(
            beforeLines.length,
            afterLines.length,
            `${ref}: line count changed`,
          );

          const linkLines = new Set(iterLinks(before).map((l) => l.line));
          for (let i = 0; i < beforeLines.length; i++) {
            if (beforeLines[i] !== afterLines[i]) {
              assert.ok(
                linkLines.has(i),
                `${ref}: line ${i} changed but held no link:\n${beforeLines[i]}\n${afterLines[i]}`,
              );
            }
          }
        }
      }),
      { numRuns: MOVE_NUM_RUNS },
    );
  });
});

// ---------------------------------------------------------------------------
// Property test — no-op Set and the ADR-0012 frontmatter round-trip
// ---------------------------------------------------------------------------

// Value pool for generated frontmatter: safe plain tokens plus markdown-link
// scalars, authored with single quotes (non-canonical) so a no-op Set provably
// normalises the quote style and is never byte-identical.
const FM_KEYS = ["title", "summary", "volatility", "tags", "source"];
const FM_VALUES = [
  "deploy",
  "ci",
  "stable",
  "hello",
  "[B](b.md)",
  "[x](../c.md)",
];

const genPageArb = fc
  .record({
    keys: fc.shuffledSubarray(FM_KEYS, { minLength: 1, maxLength: 5 }),
    values: fc.array(fc.constantFrom(...FM_VALUES), {
      minLength: 1,
      maxLength: 5,
    }),
    body: fc.string(),
  })
  .map(({ keys, values, body }) => {
    // Keys are distinct (shuffledSubarray) so the frontmatter is always valid
    // YAML; values cycle to pair with every key.
    const pairs = keys.map(
      (k, i) => [k, values[i % values.length]] as [string, string],
    );
    const fm = pairs.map(([k, v]) => `${k}: '${v}'`).join("\n");
    return { text: `---\n${fm}\n---\n${body}`, pairs };
  });

describe("no-op Set preserves key order and changes only quote style", () => {
  it("is not byte-identical but keeps keys, values, order and body", () => {
    fc.assert(
      fc.property(genPageArb, ({ text, pairs }) => {
        const [key, value] = pairs[0];
        const page = new Page(text);
        const updated = page.set(key, value).text;

        // Not byte-identical: the single-quoted input normalises.
        assert.notEqual(updated, text);

        // Body after the frontmatter block is untouched.
        assert.equal(
          splitFrontmatter(updated).body,
          splitFrontmatter(text).body,
        );

        // Key order and values are preserved semantically.
        const before = new Page(text).frontmatter()!;
        const after = new Page(updated).frontmatter()!;
        assert.deepEqual(Object.keys(after), Object.keys(before));
        assert.deepEqual(after, before);

        // The only textual divergence is scalar quote style: strip quote
        // characters from both and they are identical.
        const strip = (s: string) => s.replace(/['"]/g, "");
        assert.equal(strip(updated), strip(text));
      }),
      { numRuns: 100 },
    );
  });
});
