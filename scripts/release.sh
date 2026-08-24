#!/usr/bin/env bash
#
# Cut a release: bump the plugin version, rebuild the TypeScript bundle from
# source, commit the freshly-built artifacts into wiki-plugin/scripts/, and
# assemble the OpenCode npm package (wiki-plugin/opencode-npm/) so a
# marketplace install and `npm publish` ship the same build.
#
# Usage:  scripts/release.sh <new-version>
#   e.g.  scripts/release.sh 0.10.0
#
# Version coupling: plugin.json is the single source of truth for the version.
# This script bumps plugin.json; assemble-opencode-package.py then reads it and
# writes the version into wiki-plugin/opencode-npm/package.json, so one bump
# drives both artifacts. `npm publish` is deliberately NOT automated (no CI
# keys): this script prints the human's next step and stops.
#
# Must be run from the repo root, on a worktree/PR branch (never main, which
# is protected). Commits the version bump plus the regenerated artifacts, then
# pushes to the current branch's remote so a PR can be opened/updated. The
# freshness guard in ts-enchiridion.yml then independently re-verifies on the
# PR that the committed bundle equals a fresh build.
#
# Do NOT manually tag or run `gh release create`: tag-release.yml derives the
# tag from plugin.json on merge to main, and release.yml creates the GitHub
# Release from that tag.

set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <new-version>" >&2
  echo "  e.g. $0 0.10.0" >&2
  exit 1
fi

new_version="$1"

if ! printf '%s' "$new_version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "error: '$new_version' is not a semantic version (X.Y.Z)" >&2
  exit 1
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

current_branch="$(git rev-parse --abbrev-ref HEAD)"
if [ "$current_branch" = "main" ]; then
  echo "error: refusing to run on main (protected). Work on a PR branch." >&2
  exit 1
fi

plugin_json="wiki-plugin/.claude-plugin/plugin.json"
current_version="$(jq -r .version "$plugin_json")"

echo "Cutting release $current_version -> $new_version on branch '$current_branch'"

# 1. Bump the version in plugin.json (the single source tag-release.yml and
#    the npm package both read).
jq --arg v "$new_version" '.version = $v' "$plugin_json" > "$plugin_json.tmp"
mv "$plugin_json.tmp" "$plugin_json"

# 2. Rebuild the bundle + wasm from source (fresh, no stale dist/).
(cd enchiridion-ts && npm ci && npm run build)

# 3. Copy the built artifacts into the shipped paths.
cp enchiridion-ts/dist/cli.cjs wiki-plugin/scripts/cli.cjs
cp enchiridion-ts/dist/node-sqlite3-wasm.wasm wiki-plugin/scripts/node-sqlite3-wasm.wasm

# 4. Copy the same artifacts to the Joule skills directories. The AI Skills
#    Library fetches them directly from the repo; the freshness guard in
#    ts-enchiridion.yml verifies they match wiki-plugin/scripts/ before merge.
cp enchiridion-ts/dist/cli.cjs skills/wiki-retrieval/scripts/enchiridion.cjs
cp enchiridion-ts/dist/node-sqlite3-wasm.wasm skills/wiki-retrieval/scripts/node-sqlite3-wasm.wasm
cp enchiridion-ts/dist/cli.cjs skills/wiki-ingest/scripts/enchiridion.cjs
cp enchiridion-ts/dist/node-sqlite3-wasm.wasm skills/wiki-ingest/scripts/node-sqlite3-wasm.wasm

# 5. Assemble the OpenCode npm package: regenerates
#    agents/commands, copies the six skill dirs + session-tracker + the
#    runtime above, and writes package.json's version from plugin.json. The
#    generated surface is gitignored; package.json + templates/ are the
#    committed durable source and land in the release commit.
#    The assembly subprocesses generate-opencode.py, which imports
#    ruamel.yaml — the only interpreter guaranteed to have it is the
#    gitignored wiki-plugin/.venv (CLAUDE.md).
python="${repo_root}/wiki-plugin/.venv/bin/python"
if [ ! -x "$python" ]; then
  echo "error: $python not found — recreate wiki-plugin/.venv (CLAUDE.md) so" >&2
  echo "generate-opencode.py's ruamel.yaml dependency resolves." >&2
  exit 1
fi
"$python" wiki-plugin/scripts/assemble-opencode-package.py

# 6. Commit and push to the current branch's remote.
git add "$plugin_json" wiki-plugin/scripts/cli.cjs wiki-plugin/scripts/node-sqlite3-wasm.wasm
git add skills/wiki-retrieval/scripts/enchiridion.cjs skills/wiki-retrieval/scripts/node-sqlite3-wasm.wasm
git add skills/wiki-ingest/scripts/enchiridion.cjs skills/wiki-ingest/scripts/node-sqlite3-wasm.wasm
git add wiki-plugin/opencode-npm/package.json wiki-plugin/opencode-npm/templates/
git commit -m "chore: release v$new_version (bundle + wasm + npm package)"
git push origin "$current_branch"

echo
echo "Pushed v$new_version on '$current_branch'. Open (or update) the PR; CI's"
echo "freshness guard will re-verify the committed bundle before merge."
echo
echo "npm package assembled at wiki-plugin/opencode-npm/. To publish (human step):"
echo "  cd wiki-plugin/opencode-npm && npm publish"
echo "  # or, to dry-run the tarball:  npm pack"
