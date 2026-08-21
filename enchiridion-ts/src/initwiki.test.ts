/**
 * initwiki tests — scaffold a fresh vault: folders, git repo, gitignore,
 * optional plugin-registration settings.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  init,
  isVault,
  ModeDedicated,
  ModeQueryFromAnywhere,
} from "./initwiki.js";
import { KindFolders } from "./place.js";
import { VaultGit } from "./vaultgit.js";

function tmpVault(): string {
  return path.join(
    fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-initwiki-")),
    "vault",
  );
}

test("init scaffolds the kind-axed layout", async () => {
  const root = tmpVault();
  const got = await init(root, ModeDedicated, "");
  assert.equal(got, path.resolve(root));
  for (const folder of Object.values(KindFolders)) {
    assert.ok(
      fs.existsSync(path.join(root, "wiki", folder, ".gitkeep")),
      `${folder} missing`,
    );
  }
  assert.ok(fs.existsSync(path.join(root, "raw", ".gitkeep")));
  assert.ok(fs.existsSync(path.join(root, ".gitignore")));
});

test("init gitignores the search index", async () => {
  const root = tmpVault();
  await init(root, ModeDedicated, "");
  const content = fs.readFileSync(path.join(root, ".gitignore"), "utf8");
  for (const line of [
    "*.rsls",
    ".claude/wiki-knowledge/sessions/",
    ".opencode/wiki-knowledge/sessions/",
    ".wiki-knowledge/",
  ]) {
    assert.ok(
      content.split("\n").includes(line),
      `.gitignore is missing "${line}"; got:\n${content}`,
    );
  }
});

test("init commits the scaffold", async () => {
  const root = tmpVault();
  await init(root, ModeDedicated, "");
  const repo = new VaultGit(root);
  assert.ok(await repo.isWorkTree(), "Init left no git work tree behind");
  // The scaffold commit is what makes the vault's git history complete from
  // page one.
  await assert.rejects(repo.commit("should fail: nothing left to commit"));
});

test("init query-from-anywhere registers the plugin", async () => {
  const root = tmpVault();
  const pluginRoot = "/somewhere/wiki-plugin";
  await init(root, ModeQueryFromAnywhere, pluginRoot);
  const settings = JSON.parse(
    fs.readFileSync(path.join(root, ".claude", "settings.json"), "utf8"),
  ) as {
    extraKnownMarketplaces: Record<
      string,
      { source: { source: string; path: string } }
    >;
    enabledPlugins: Record<string, boolean>;
  };
  const marketplace = settings.extraKnownMarketplaces["wiki-knowledge-plugin"];
  assert.ok(marketplace, "settings.json registers no marketplace");
  assert.equal(marketplace.source.source, "directory");
  assert.equal(marketplace.source.path, pluginRoot);
  assert.equal(
    settings.enabledPlugins["wiki-knowledge@wiki-knowledge-plugin"],
    true,
  );
});

test("init dedicated writes no settings", async () => {
  const root = tmpVault();
  await init(root, ModeDedicated, "");
  assert.ok(
    !fs.existsSync(path.join(root, ".claude", "settings.json")),
    "dedicated mode wrote a settings.json",
  );
});

test("init refuses to run twice", async () => {
  const root = tmpVault();
  await init(root, ModeDedicated, "");
  await assert.rejects(init(root, ModeDedicated, ""));
});

test("init refuses a directory with a wiki-root sentinel", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-initwiki-"));
  fs.writeFileSync(path.join(root, ".wiki-root"), "");
  await assert.rejects(init(root, ModeDedicated, ""));
});

test("init validates its arguments", async () => {
  const cases: Array<[string, string]> = [
    ["sideways", ""],
    [ModeQueryFromAnywhere, ""],
  ];
  for (const [mode, pluginRoot] of cases) {
    const root = tmpVault();
    await assert.rejects(init(root, mode, pluginRoot));
    assert.ok(
      !fs.existsSync(root),
      "validation failed but the vault directory was still created",
    );
  }
});

test("isVault reports markers", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "enchiridion-initwiki-"));
  assert.equal(isVault(root), false);
  fs.mkdirSync(path.join(root, "wiki"));
  assert.equal(isVault(root), true);
});
