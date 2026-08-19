import esbuild from "esbuild";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Copy the .wasm sidecar from node-sqlite3-wasm into dist/, alongside the
// bundled CLI. node-sqlite3-wasm is kept external (below) precisely so its
// runtime `fetch`/`readFile` of this file resolves relative to the bundle
// rather than needing the wasm bytes inlined.
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

// Single bundled entry point per #254's acceptance criteria: one .js file
// (plus the .wasm sidecar above) that `node`/`bun` both run directly.
// wiki-plugin/bin/enchiridion execs this output.
await esbuild.build({
  entryPoints: ["src/cli.ts"],
  bundle: true,
  platform: "node",
  format: "esm",
  outfile: "dist/cli.js",
  external: [
    // node built-ins are external automatically under platform: "node";
    // listed for clarity and to survive an esbuild config change.
    "node:*",
    "node-sqlite3-wasm", // kept external so the .wasm sidecar can be co-located
    "isomorphic-git",
    "yaml",
  ],
});

console.log("Build complete: dist/cli.js + dist/node-sqlite3-wasm.wasm");
