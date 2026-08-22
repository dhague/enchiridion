/** OpenCode plugin exposing the wiki-knowledge script layer as a `wiki` tool.
 *
 * Runs the enchiridion bundle in-process instead of shelling out: the tool
 * imports `scripts/cli.cjs` (or a co-located bundle) and drives its
 * import-safe `run(argv)` export, capturing stdout/stderr into the tool
 * result. This is what lets the plugin work inside OpenCode's embedded Bun
 * with no node on PATH.
 *
 * Bundle resolution (#331): the install (install-opencode.py) writes
 * `wiki-knowledge/config.json` carrying `plugin_root` into the config dir that
 * also holds this plugin — `.opencode/` for a project install,
 * `~/.config/opencode/` for `--global` — so the marker is always one level up
 * from this file. The tool prefers that marker's `/scripts/cli.cjs`, falling
 * back to a session-directory marker and then a bundle shipped next to the
 * plugin itself (vault layout variant).
 *
 * The install must also write `.opencode/package.json` declaring
 * `@opencode-ai/plugin` as a runtime dependency — this plugin imports `tool`
 * as a value (not just `type Plugin`), and OpenCode runs `bun install` on the
 * config directory's package.json at startup.
 */
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { tool, type Plugin } from "@opencode-ai/plugin"

// This file's own directory — the config dir's plugins/ that holds the
// marker's sibling wiki-knowledge/ (portable; import.meta.dir isn't typed).
const HERE = dirname(fileURLToPath(import.meta.url))

// Where the marker lives in an installed config dir (install-opencode.py
// writes it): a project install puts it at `.opencode/wiki-knowledge/`, a
// `--global` install at `~/.config/opencode/wiki-knowledge/` — in both cases a
// sibling of the `plugins/` directory that holds this file.
const MARKER = join("wiki-knowledge", "config.json")

function bundleFromMarker(markerPath: string): string | undefined {
  try {
    const marker = JSON.parse(readFileSync(markerPath, "utf8"))
    if (marker.plugin_root) {
      const cand = join(marker.plugin_root, "scripts", "cli.cjs")
      readFileSync(cand) // throw if absent
      return cand
    }
  } catch {
    /* fall through */
  }
  return undefined
}

async function resolveBundle(directory: string): Promise<string> {
  // 1. The marker the install wrote next to this plugin — works for both
  //    install modes and doesn't depend on the session being in the vault.
  const ownMarker = bundleFromMarker(join(HERE, "..", MARKER))
  if (ownMarker) return ownMarker
  // 2. A session-directory project install (session at the vault root).
  const sessionMarker = bundleFromMarker(join(directory, ".opencode", MARKER))
  if (sessionMarker) return sessionMarker
  // 3. Co-located bundle (vault layout variant).
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
        },
        async execute(input, context) {
          const dir = context.directory ?? directory
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
