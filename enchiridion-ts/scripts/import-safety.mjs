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
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

// Inertness is asserted in a CHILD process, not here: if main() ran
// unconditionally it would process.exit during import — exit code 0 for a
// bare run, killing the host before any assertion could run, which would look
// like a pass. The child imports the bundle then prints a sentinel; the
// sentinel's absence (the child died on import) is the failure signal, and a
// non-zero child exit makes execFileSync throw. Either way this script fails.
const bundlePath = fileURLToPath(new URL("../dist/cli.cjs", import.meta.url));
const inert = execFileSync(
  process.execPath,
  [
    "-e",
    "import(process.argv[1]).then(() => console.log('import-alive'))",
    bundlePath,
  ],
  { encoding: "utf8" },
);
assert.ok(
  inert.includes("import-alive"),
  "importing the bundle must not run main() (host died on import)",
);

const mod = await import("../dist/cli.cjs");
const entry = mod.run ? mod : mod.default;
assert.equal(typeof entry.run, "function", "bundle must export run()");

const usage = await entry.run([]);
assert.equal(usage.exitCode, 0, "run([]) must exit 0");
assert.ok(
  usage.stdout.includes("Usage:"),
  "run([]) must print usage to stdout",
);

const placed = await entry.run(["place", "concept", "import-safety-check"]);
assert.equal(placed.exitCode, 0, "run(place) must exit 0");
assert.ok(
  placed.stdout.includes("import-safety-check"),
  "run(place) must produce the placed page path (an action handler ran in-process)",
);

assert.ok(
  process.exitCode == null || process.exitCode === 0,
  `host process must not be left with a failing exitCode (got ${process.exitCode})`,
);

console.log(
  "import-safety: bundle imports inertly, run() works in-process, host unaffected",
);
