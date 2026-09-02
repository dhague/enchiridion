import { describe, it, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { mkdirSafe } from "./fsutil.js";

describe("mkdirSafe", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), "fsutil-test-"));
  });

  afterEach(() => {
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it("creates a new directory", () => {
    const dir = path.join(tmp, "new");
    mkdirSafe(dir);
    assert.ok(fs.statSync(dir).isDirectory());
  });

  it("creates nested directories", () => {
    const dir = path.join(tmp, "a", "b", "c");
    mkdirSafe(dir);
    assert.ok(fs.statSync(dir).isDirectory());
  });

  it("succeeds when directory already exists", () => {
    const dir = path.join(tmp, "exists");
    fs.mkdirSync(dir);
    assert.doesNotThrow(() => mkdirSafe(dir));
  });

  it("throws with clear message when path is a file, not a directory", () => {
    const file = path.join(tmp, "file.txt");
    fs.writeFileSync(file, "data");
    assert.throws(
      () => mkdirSafe(file),
      (err: unknown) => {
        assert.ok(err instanceof Error);
        assert.match(err.message, /exists as a file.*delete it/);
        return true;
      },
    );
  });

  it("preserves mode when creating directory", () => {
    const dir = path.join(tmp, "withmode");
    mkdirSafe(dir, 0o755);
    assert.ok(fs.statSync(dir).isDirectory());
  });
});
