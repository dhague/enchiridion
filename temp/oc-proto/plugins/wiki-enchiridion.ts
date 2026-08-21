/**
 * PROTOTYPE — OpenCode plugin that runs the enchiridion bundle in-process.
 * Ticket #293. Not production. Demonstrates:
 *   - a plugin custom tool executes inside OpenCode's embedded Bun (no node
 *     on PATH required);
 *   - the tool imports the bundle and calls its `run(argv)` export instead of
 *     shelling out — needs the cli.ts restructure (guard main(), export run());
 *   - output is captured in-process and returned as the tool result.
 *
 * Install: copy to .opencode/plugins/. The plugin finds the bundle next to
 * itself in the vault layout OR from the marker's plugin_root (the install
 * already writes `.opencode/wiki-knowledge/config.json`).
 */
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { tool, type Plugin } from "@opencode-ai/plugin"

// Where the marker lives in an installed vault (install-opencode.py writes it).
const MARKER = join(".opencode", "wiki-knowledge", "config.json")

async function resolveBundle(directory: string): Promise<string> {
  // 1. marker → plugin_root → scripts/cli.cjs (the shipped artifact).
  try {
    const marker = JSON.parse(readFileSync(join(directory, MARKER), "utf8"))
    if (marker.plugin_root) {
      const cand = join(marker.plugin_root, "scripts", "cli.cjs")
      readFileSync(cand) // throw if absent
      return cand
    }
  } catch {
    /* fall through */
  }
  // 2. co-located bundle (vault layout variant).
  const cand = join(directory, "scripts", "cli.cjs")
  readFileSync(cand)
  return cand
}

export const WikiEnchiridion: Plugin = async ({ directory }) => {
  return {
    tool: {
      wiki: tool({
        description:
          "Run the wiki-knowledge enchiridion script layer in-process (search, ingest, discover, vault, ...).",
        args: {
          args: tool.schema.array(tool.schema.string()).describe(
            "enchiridion subcommand and its arguments, e.g. ['search', 'bm25']",
          ),
          cwd: tool.schema.optional(tool.schema.string()).describe(
            "working directory (defaults to the session directory)",
          ),
        },
        async execute(input, context) {
          const dir = input.cwd ?? context.directory ?? directory
          const bundle = await resolveBundle(dir)
          const mod = await import(bundle) // CJS → default-interop namespace
          const { run } = mod as { run: (argv: string[]) => Promise<unknown> }
          const result = (await run(input.args)) as {
            stdout: string
            stderr: string
            exitCode: number
          }
          if (result.exitCode !== 0) {
            return `enchiridion exited ${result.exitCode}\n${result.stderr.trim()}`
          }
          return result.stdout
        },
      }),
    },
  }
}