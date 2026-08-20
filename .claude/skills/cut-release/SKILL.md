---
name: cut-release
description: Cut a new release of the wiki-knowledge plugin. Use when asked to release, bump the version, or cut a release. Runs scripts/release.sh, opens the PR, and explains what CI does from there.
---

# Cut a release

Releases are cut by a **human (or agent) running `scripts/release.sh`** on a PR
branch (D2 #287); CI takes over from the merge. Do **not** manually tag or run
`gh release create` — `tag-release.yml` derives and pushes the tag from
`plugin.json`, and `release.yml` creates the GitHub Release from that tag.

## Procedure

1. Create a worktree + PR branch off `main` (which is protected; never cut
   from main directly).
2. Choose the new version. The plugin version lives in
   `wiki-plugin/.claude-plugin/plugin.json` (currently `0.9.0`).
3. Run, from the repo root:

   ```sh
   scripts/release.sh <new-version>
   ```

   The script: bumps `plugin.json`, runs `npm ci && npm run build` in
   `enchiridion-ts/`, copies the freshly-built `cli.cjs` + `node-sqlite3-wasm.wasm`
   into `wiki-plugin/scripts/`, commits, and pushes the branch.
4. Open a PR for the branch. CI's **freshness guard** (`ts-enchiridion.yml`)
   independently rebuilds and fails the PR if the committed bundle drifts from
   source, so this is the safety net if the script's copy was stale.
5. Wait for the human to confirm the PR merged. On merge to main,
   `tag-release.yml` pushes `v<version>` and `release.yml` creates the GitHub
   Release (source archives only — the real artifact ships committed in
   `wiki-plugin/scripts/`, so installs need no build/network step).

Do not clean up the branch/worktree until the human confirms the PR merged.
