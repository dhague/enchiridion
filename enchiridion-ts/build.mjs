import esbuild from "esbuild";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Copy the .wasm sidecar from node-sqlite3-wasm into dist/, alongside the
// bundled CLI. node-sqlite3-wasm is INLINED (not external, below): an
// inlined bundle looks for the `.wasm` next to the bundle itself
// (__dirname of the output), which is exactly the co-location a packaged
// plugin ships — cli.cjs + node-sqlite3-wasm.wasm in the same directory with
// no node_modules present (D3 #288).
const wasmSrc = path.join(
  __dirname,
  "node_modules",
  "node-sqlite3-wasm",
  "dist",
  "node-sqlite3-wasm.wasm",
);
fs.mkdirSync(path.join(__dirname, "dist"), { recursive: true });
fs.copyFileSync(
  wasmSrc,
  path.join(__dirname, "dist", "node-sqlite3-wasm.wasm"),
);

// Single bundled entry point per #254's acceptance criteria: one file (plus
// the .wasm sidecar above) that `node`/`bun` both run directly.
// wiki-plugin/bin/enchiridion execs this output.
//
// Output is CommonJS (.cjs), not ESM: several inlined third-party packages
// (yaml, isomorphic-git) are CommonJS and their `require("node:...")` calls
// fail as dynamic requires under esbuild's ESM output on Node. Bundling to
// CJS keeps those requires native. The `.cjs` extension sidesteps the
// `"type": "module"` in this package.json and is the natural format for the
// shipped artifact, which lives in wiki-plugin/scripts/ (no type field).
await esbuild.build({
  entryPoints: ["src/cli.ts"],
  bundle: true,
  platform: "node",
  format: "cjs",
  outfile: "dist/cli.cjs",
  external: [
    // node built-ins are external automatically under platform: "node";
    // listed for clarity and to survive an esbuild config change.
    "node:*",
    // No third-party packages are external: the bundled cli.cjs must be
    // self-contained so a packaged install (a plain checkout, no node_modules)
    // runs it with no build/npm/network step. isomorphic-git, yaml, and
    // node-sqlite3-wasm are all inlined (the latter via a static import in
    // src/searchindex.ts).
  ],
});

console.log("Build complete: dist/cli.cjs + dist/node-sqlite3-wasm.wasm");
