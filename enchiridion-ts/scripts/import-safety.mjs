// Import-safety test for the esbuild CJS bundle (the #330 acceptance gate,
// wired into CI by #333).
//
// The bundle is the whole `enchiridion` surface, built as CommonJS
// (`dist/cli.cjs` + the node-sqlite3-wasm .wasm sidecar). A host (an OpenCode
// plugin) imports it and calls `run(argv)` in-process; importing must be
// inert — `main()` runs only when the module is the direct CLI entry
// (`isMainModule()`), so a bare import must never hijack the process. This
// script proves that on both runtimes CI covers (Node and Bun):
//
//   - importing the bundle does not run main() (we are still alive; the
//     exported entry is a function, not a process that already exited);
//   - `run([])` prints usage to captured stdout and exits 0;
//   - `run(['place', ...])` executes an action handler in-process and exits 0;
//   - the host process is not left with a non-zero exitCode.
//
// Runs under Node ESM and Bun alike. On Node ESM the CJS namespace's `default`
// IS module.exports; on Bun `mod.run` is present directly — resolve both
// shapes before asserting.
import { strict as assert } from "node:assert";

const mod = await import("../dist/cli.cjs");
const entry = mod.run ? mod : mod.default;
assert.equal(
  typeof entry.run,
  "function",
  "bundle must export run() (import must not have run main())",
);

const usage = await entry.run([]);
assert.equal(usage.exitCode, 0, "run([]) must exit 0");
assert.ok(
  usage.stdout.includes("Usage:"),
  "run([]) must print usage to stdout",
);

const placed = await entry.run(["place", "concept", "import-safety-check"]);
assert.equal(placed.exitCode, 0, "run(place) must exit 0");
assert.ok(
  placed.stdout.trim().length > 0,
  "run(place) must produce output (an action handler ran in-process)",
);

assert.ok(
  process.exitCode == null || process.exitCode === 0,
  `host process must not be left with a failing exitCode (got ${process.exitCode})`,
);

console.log(
  "import-safety: bundle imports inertly, run() works in-process, host unaffected",
);
