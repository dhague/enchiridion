// PROTOTYPE harness: loads the plugin module and drives its wiki tool's
// execute() exactly as OpenCode would (embedded Bun aside), verifying the
// in-process bundle path end to end.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { run } from "./cli-proto.cjs";

// Replicate the plugin's execute body against the installed vault.
const directory = join(process.cwd(), "vault");
const marker = JSON.parse(
  readFileSync(join(directory, ".opencode", "wiki-knowledge", "config.json"), "utf8"),
);
const bundle = join(marker.plugin_root, "scripts", "cli.cjs");
console.log("resolved bundle:", bundle);

const tests = [
  ["place", "concept", "Plugin-run page"],
  ["search", "bm25", "--limit", "2"],
  ["vault"],
];
for (const args of tests) {
  const r = await run(args);
  console.log(`>> ${JSON.stringify(args)} exit=${r.exitCode}`);
  console.log("   " + r.stdout.trim().split("\n").slice(0, 2).join("\n   "));
}
console.log("host alive; process.exitCode =", process.exitCode);
