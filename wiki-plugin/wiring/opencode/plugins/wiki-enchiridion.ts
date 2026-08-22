/** OpenCode plugin exposing the wiki-knowledge script layer as a `wiki` tool.
 *
 * Runs the enchiridion bundle in-process instead of shelling out: the tool
 * imports `scripts/cli.cjs` (or a co-located bundle) and drives its
 * import-safe `run(argv)` export, capturing stdout/stderr into the tool
 * result. This is what lets the plugin work inside OpenCode's embedded Bun
 * with no node on PATH.
 *
 * Bundle resolution (#331): the install (install-opencode.py) writes
 * `.opencode/wiki-knowledge/config.json` carrying `plugin_root`; the tool
 * prefers that marker's `/scripts/cli.cjs`, falling back to a bundle shipped
 * next to the plugin itself (vault layout variant).
 *
 * The install must also write `.opencode/package.json` declaring
 * `@opencode-ai/plugin` as a runtime dependency — this plugin imports `tool`
 * as a value (not just `type Plugin`), and OpenCode runs `bun install` on the
 * config directory's package.json at startup.
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
