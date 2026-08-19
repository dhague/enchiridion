import esbuild from "esbuild";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const _require = createRequire(import.meta.url);

// Copy the .wasm sidecar from node-sqlite3-wasm into dist/.
const wasmSrc = path.join(
  __dirname,
  "node_modules",
  "node-sqlite3-wasm",
  "dist",
  "node-sqlite3-wasm.wasm",
);
fs.mkdirSync(path.join(__dirname, "dist"), { recursive: true });
fs.copyFileSync(wasmSrc, path.join(__dirname, "dist", "node-sqlite3-wasm.wasm"));

await esbuild.build({
  entryPoints: ["src/searchindex.ts"],
  bundle: true,
  platform: "node",
  format: "esm",
  outfile: "dist/searchindex.js",
  external: [
    // node built-ins always external in Node/Bun
    "node:*",
    "node-sqlite3-wasm",    // kept external so .wasm sidecar can be co-located
    "isomorphic-git",
    "yaml",
  ],
});

console.log("Build complete: dist/searchindex.js + dist/node-sqlite3-wasm.wasm");
