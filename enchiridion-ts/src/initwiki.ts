/**
 * Scaffolds a brand-new, empty wiki vault: folders, git repo, .gitignore,
 * and (for query-from-anywhere mode) the plugin-registration settings.json.
 * Ported from enchiridion-go/internal/initwiki.
 *
 * One-time setup, distinct from `wiki-ingest`, which fills a vault that
 * already exists — [init] refuses to run against a directory that already
 * looks like one ([isVault]).
 *
 * Deployment mode (ADR-0004) is the caller's judgment call, never inferred
 * here: [ModeQueryFromAnywhere] writes `.claude/settings.json` registering
 * pluginRoot as a local-directory marketplace; [ModeDedicated] skips that
 * write, since installing a plugin project-scope into someone else's
 * directory isn't this module's job.
 */

import fs from "node:fs";
import path from "node:path";
import { KindFolders } from "./place.js";
import { hasMarker } from "./vault.js";
import { VaultGit } from "./vaultgit.js";

/** The two deployment modes of ADR-0004. */
export const ModeQueryFromAnywhere = "query-from-anywhere";
export const ModeDedicated = "dedicated";

/** The accepted --mode values, for CLI help and validation. */
export const Modes = [ModeQueryFromAnywhere, ModeDedicated];

/**
 * Session-tracker state is per-host and never committed: Claude Code's
 * SessionStart hook writes under `.claude/`, OpenCode's session-tracker plugin
 * under `.opencode/`. In dedicated mode the project dir *is* the vault, so both
 * land here.
 */
export const gitignore =
  "*.rsls\n" +
  ".claude/wiki-knowledge/sessions/\n" +
  ".opencode/wiki-knowledge/sessions/\n" +
  // Search index, gitignored per ADR-0006. Must ALSO be added to Resilio
  // Sync's own ignore list — a gitignore doesn't propagate to the syncer,
  // and a synced SQLite sidecar corrupts.
  ".wiki-knowledge/\n";

/** Report whether root already looks like a vault — either vault marker
 * being present is enough. */
export function isVault(root: string): boolean {
  return hasMarker(root);
}

function settingsJSON(pluginRoot: string): string {
  const settings = {
    extraKnownMarketplaces: {
      "wiki-knowledge-plugin": {
        source: { source: "directory", path: pluginRoot },
      },
    },
    enabledPlugins: { "wiki-knowledge@wiki-knowledge-plugin": true },
  };
  return JSON.stringify(settings, null, 2) + "\n";
}

/**
 * Scaffolds vaultRoot as a new vault and returns the vault root.
 *
 * mode is [ModeQueryFromAnywhere] (requires pluginRoot, the plugin's install
 * directory) or [ModeDedicated] (no settings.json; the caller installs the
 * plugin themselves).
 *
 * git comes from [VaultGit], the one module that talks to git (#126) — there
 * is no "git missing on PATH" failure mode; an unwritable root or an
 * unconfigured committer identity instead surface from the git verbs
 * themselves.
 */
export async function init(
  vaultRoot: string,
  mode: string,
  pluginRoot: string,
): Promise<string> {
  switch (mode) {
    case ModeQueryFromAnywhere:
      if (pluginRoot === "") {
        throw new Error(`${ModeQueryFromAnywhere} mode requires a plugin root`);
      }
      break;
    case ModeDedicated:
      break;
    default:
      throw new Error(
        `unknown mode "${mode}"; must be one of ${Modes.join(", ")}`,
      );
  }

  if (isVault(vaultRoot)) {
    throw new Error(
      `${vaultRoot} already looks like a vault (wiki/ or .wiki-root exists)`,
    );
  }

  fs.mkdirSync(vaultRoot, { recursive: true, mode: 0o755 });

  for (const folder of Object.values(KindFolders)) {
    const kindDir = path.join(vaultRoot, "wiki", folder);
    fs.mkdirSync(kindDir, { recursive: true, mode: 0o755 });
    touch(path.join(kindDir, ".gitkeep"));
  }
  const rawDir = path.join(vaultRoot, "raw");
  fs.mkdirSync(rawDir, { recursive: true, mode: 0o755 });
  touch(path.join(rawDir, ".gitkeep"));

  fs.writeFileSync(path.join(vaultRoot, ".gitignore"), gitignore, {
    mode: 0o644,
  });

  const addPaths = ["wiki", ".gitignore", "raw/.gitkeep"];
  if (mode === ModeQueryFromAnywhere) {
    const claudeDir = path.join(vaultRoot, ".claude");
    fs.mkdirSync(claudeDir, { recursive: true, mode: 0o755 });
    fs.writeFileSync(
      path.join(claudeDir, "settings.json"),
      settingsJSON(pluginRoot),
      {
        mode: 0o644,
      },
    );
    addPaths.push(".claude/settings.json");
  }

  const repo = new VaultGit(vaultRoot);
  if (!(await repo.isWorkTree())) {
    await repo.init();
  }
  await repo.add(addPaths);
  await repo.commit("Initialize wiki vault");

  return path.resolve(vaultRoot);
}

function touch(file: string): void {
  fs.closeSync(fs.openSync(file, "a", 0o644));
}
