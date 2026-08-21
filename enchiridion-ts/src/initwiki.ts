/**
/**
 * Scaffolds a brand-new, empty wiki vault — folders, git repo, .gitignore,
 * and (for query-from-anywhere mode) the plugin-registration settings.json —
 * or seeds a repo around a directory that already carries a `wiki/` tree but
 * no git work tree, the conversion path a Joule user lands on (#323).
 *
 * One-time setup, distinct from `wiki-ingest`, which fills a vault that
 * already exists — [init] refuses to run against a directory that is already
 * a vault ([isVault]: a marker **and** a git work tree). A marker without
 * git still resolves as a vault root for reads ([vault.resolveRoot]'s walk
 * is untouched), but it isn't a seedable-away vault; [init] proceeds over
 * the existing tree, git-inits around it, and the initial commit sweeps the
 * existing pages in.
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
  ".wiki-knowledge/\n" +
  // LLM-wiki/Obsidian navigation scaffolding (log.md, index.md, _index.md)
  // is not knowledge, so a converted vault's initial commit skips it — and
  // since search reads git blobs (ADR-0015), a gitignored index is invisible
  // to search by construction (#323).
  "log.md\n" +
  "index.md\n" +
  "_index.md\n";

/**
 * Report whether root already looks like a vault — the gate [init] refuses
 * on.
 *
 * A root is a vault only when it carries a marker (`wiki/` dir or
 * `.wiki-root`) **and** is a git work tree. A marker without git still
 * resolves as a vault root for reads; it just isn't a seedable-away vault —
 * that's the conversion path (#323), where [init] proceeds over the existing
 * tree instead of refusing.
 */
export async function isVault(root: string): Promise<boolean> {
  if (!hasMarker(root)) return false;
  return await new VaultGit(root).isWorkTree();
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

  if (await isVault(vaultRoot)) {
    throw new Error(
      `${vaultRoot} already looks like a vault (a wiki/ or .wiki-root marker in a git work tree)`,
    );
  }

  fs.mkdirSync(vaultRoot, { recursive: true, mode: 0o755 });

  // A pre-existing wiki/ tree means conversion (#323): init proceeds over
  // the existing tree instead of scaffolding empty, and the initial commit
  // sweeps the existing pages in. Existing kind-folders are left untouched;
  // missing ones (a converted LLM-wiki typically carries only a few) are
  // created with a .gitkeep so the canonical layout is complete and tracked.
  const converting = fs.existsSync(path.join(vaultRoot, "wiki"));
  for (const folder of Object.values(KindFolders)) {
    const kindDir = path.join(vaultRoot, "wiki", folder);
    if (fs.existsSync(kindDir)) continue;
    fs.mkdirSync(kindDir, { recursive: true, mode: 0o755 });
    touch(path.join(kindDir, ".gitkeep"));
  }
  // raw/ is part of a fresh scaffold; a converted vault that has no inbox yet
  // keeps none — addPaths below stages only what exists.
  const rawDir = path.join(vaultRoot, "raw");
  if (!converting && !fs.existsSync(rawDir)) {
    fs.mkdirSync(rawDir, { recursive: true, mode: 0o755 });
    touch(path.join(rawDir, ".gitkeep"));
  }

  fs.writeFileSync(path.join(vaultRoot, ".gitignore"), gitignore, {
    mode: 0o644,
  });

  // Stage only what exists: a fresh init stages every scaffolded path; a
  // conversion of an existing wiki/ tree may lack raw/ entirely, and a raw/
  // that does exist — even one carrying content rather than a scaffold
  // .gitkeep — is swept into the initial commit.
  const addPaths: string[] = [];
  for (const rel of ["wiki", "raw", ".gitignore"]) {
    const abs = path.join(vaultRoot, ...rel.split("/"));
    if (fs.existsSync(abs)) addPaths.push(rel);
  }
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
