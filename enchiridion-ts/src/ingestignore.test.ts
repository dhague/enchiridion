/**
 * ingestignore tests — parse and append to the per-folder .ingestignore.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Filename, append, parse } from "./ingestignore.js";

test("parse strips comments and blanks", () => {
  const patterns = parse(
    "# a comment\n\nliteral.md\n*.tmp  # trailing comment\n   \n",
  );
  assert.deepEqual(patterns, ["literal.md", "*.tmp"]);
});

test("parse rejects richer patterns", () => {
  for (const line of ["sub/dir.md", "!keep.md", "**/deep.md"]) {
    assert.throws(() => parse(line));
  }
});

test("append creates the file", () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ii-"));
  append(folder, "doc.md", "ingested before back-pointers were mandatory");
  const text = readIgnore(folder);
  assert.equal(
    text,
    "doc.md  # ingested before back-pointers were mandatory\n",
  );
});

test("append is idempotent", () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ii-"));
  for (let i = 0; i < 3; i++) append(folder, "doc.md", "");
  assert.equal(readIgnore(folder), "doc.md\n");
});

test("append preserves existing content", () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ii-"));
  fs.writeFileSync(path.join(folder, Filename), "# policy\n*.tmp\n");
  append(folder, "doc.md", "");
  assert.equal(readIgnore(folder), "# policy\n*.tmp\ndoc.md\n");
});

test("append refuses a missing folder", () => {
  const folder = path.join(
    fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-ii-")),
    "emials",
  );
  assert.throws(() => append(folder, "doc.eml", ""));
  assert.equal(fs.existsSync(folder), false);
});

function readIgnore(folder: string): string {
  return fs.readFileSync(path.join(folder, Filename), "utf8");
}
