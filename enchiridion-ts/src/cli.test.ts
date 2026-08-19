/**
 * CLI-level smoke tests (#252's "Testing Decisions": one test per
 * subcommand is enough at this level — correctness lives in module tests
 * once those land; these confirm the commander wiring).
 *
 * Spawns `tsx src/cli.ts` as a subprocess for each case so commander's own
 * process.exit()/process.exitCode calls behave exactly as they would for a
 * real invocation, without taking down the test runner.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const cliPath = path.join(__dirname, "cli.ts");
const tsxBin = path.join(__dirname, "..", "node_modules", ".bin", "tsx");

function run(args: string[]): {
  status: number | null;
  stdout: string;
  stderr: string;
} {
  const result = spawnSync(tsxBin, [cliPath, ...args], { encoding: "utf8" });
  return {
    status: result.status,
    stdout: result.stdout,
    stderr: result.stderr,
  };
}

test("no arguments: prints help, exits 0 (parity with the Go/cobra binary)", () => {
  const { status, stdout } = run([]);
  assert.equal(status, 0);
  assert.match(stdout, /Usage:/);
  assert.match(stdout, /enchiridion/);
});

test("--help: prints help, exits 0", () => {
  const { status, stdout } = run(["--help"]);
  assert.equal(status, 0);
  assert.match(stdout, /Usage:/);
});

for (const name of ["search", "init", "ingest", "discover", "watch"]) {
  test(`${name}: stub exits non-zero with "not yet implemented"`, () => {
    const { status, stderr } = run([name]);
    assert.notEqual(status, 0);
    assert.match(stderr, /not yet implemented/);
  });
}

test("vault (bare): stub exits non-zero", () => {
  const { status, stderr } = run(["vault"]);
  assert.notEqual(status, 0);
  assert.match(stderr, /not yet implemented/);
});

test("vault root: stub exits non-zero", () => {
  const { status, stderr } = run(["vault", "root"]);
  assert.notEqual(status, 0);
  assert.match(stderr, /not yet implemented/);
});

test("vault move: stub exits non-zero", () => {
  const { status, stderr } = run(["vault", "move", "old.md", "new.md"]);
  assert.notEqual(status, 0);
  assert.match(stderr, /not yet implemented/);
});

test("page get: stub exits non-zero", () => {
  const { status, stderr } = run(["page", "get", "file.md", "kind"]);
  assert.notEqual(status, 0);
  assert.match(stderr, /not yet implemented/);
});

test("hook session-start: stub exits non-zero", () => {
  const { status, stderr } = run(["hook", "session-start"]);
  assert.notEqual(status, 0);
  assert.match(stderr, /not yet implemented/);
});

test("unknown command: commander itself errors non-zero", () => {
  const { status } = run(["totally-bogus-command"]);
  assert.notEqual(status, 0);
});
