#!/usr/bin/env bash
#
# Cut a release: bump the plugin version, rebuild the TypeScript bundle from
# source, and commit the freshly-built artifacts into wiki-plugin/scripts/ so a
# marketplace install ships them with no build step (D2 #287, D4 #289).
#
# Usage:  scripts/release.sh <new-version>
#   e.g.  scripts/release.sh 0.10.0
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

# 1. Bump the version in plugin.json (the single source tag-release.yml reads).
jq --arg v "$new_version" '.version = $v' "$plugin_json" > "$plugin_json.tmp"
mv "$plugin_json.tmp" "$plugin_json"

# 2. Rebuild the bundle + wasm from source (fresh, no stale dist/).
(cd enchiridion-ts && npm ci && npm run build)

# 3. Copy the built artifacts into the shipped path.
cp enchiridion-ts/dist/cli.cjs wiki-plugin/scripts/cli.cjs
cp enchiridion-ts/dist/node-sqlite3-wasm.wasm wiki-plugin/scripts/node-sqlite3-wasm.wasm

# 4. Commit and push to the current branch's remote.
git add "$plugin_json" wiki-plugin/scripts/cli.cjs wiki-plugin/scripts/node-sqlite3-wasm.wasm
git commit -m "chore: release v$new_version (bundle + wasm)"
git push origin "$current_branch"

echo
echo "Pushed v$new_version on '$current_branch'. Open (or update) the PR; CI's"
echo "freshness guard will re-verify the committed bundle before merge."
