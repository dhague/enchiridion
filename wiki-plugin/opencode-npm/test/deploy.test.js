"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const { deploy, DeployError, DEFAULT_MODELS } = require("../bin/deploy.js");

const SKILLS = [
  "wiki-conventions",
  "wiki-ingest",
  "wiki-init",
  "wiki-retrieval",
  "wiki-watch",
  "save-conversation",
];

const GITIGNORE_ENTRIES = [
  ".agents/skills/",
  ".opencode/agents/",
  ".opencode/commands/",
  ".opencode/plugins/session-tracker.ts",
  ".opencode/wiki-knowledge/",
];

function makeFixturePackage({ templates = true } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "wk-pkg-"));
  for (const name of ["wiki-ingest", "wiki-researcher"]) {
    fs.mkdirSync(path.join(root, "agents"), { recursive: true });
    fs.writeFileSync(path.join(root, "agents", `${name}.md`), `# ${name}\n`);
  }
  for (const s of SKILLS) {
    fs.mkdirSync(path.join(root, "commands"), { recursive: true });
    fs.writeFileSync(path.join(root, "commands", `${s}.md`), `# ${s}\n`);
  }
  for (const s of SKILLS) {
    fs.mkdirSync(path.join(root, "skills", s), { recursive: true });
    fs.writeFileSync(path.join(root, "skills", s, "SKILL.md"), `# ${s}\n`);
  }
  fs.mkdirSync(path.join(root, "plugins"), { recursive: true });
  fs.writeFileSync(path.join(root, "plugins", "session-tracker.ts"), "export default {};\n");
  fs.mkdirSync(path.join(root, "wiki-knowledge"), { recursive: true });
  fs.writeFileSync(path.join(root, "wiki-knowledge", "cli.cjs"), "module.exports = {};\n");
  fs.writeFileSync(path.join(root, "wiki-knowledge", "node-sqlite3-wasm.wasm"), "WASM");
  if (templates) {
    fs.mkdirSync(path.join(root, "templates"), { recursive: true });
    fs.writeFileSync(path.join(root, "templates", "config.json"), JSON.stringify({ plugin_root: null }));
    fs.writeFileSync(path.join(root, "templates", "model-config.json"), JSON.stringify(DEFAULT_MODELS));
  }
  return root;
}

function makeVault() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "wk-vault-"));
}

function readJson(p) {
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

test("dedicated mode targets cwd/.opencode and uses cwd as vault", () => {
  const vault = makeVault();
  const res = deploy({ packageRoot: makeFixturePackage(), cwd: vault, home: os.tmpdir(), stdin: {} });
  assert.equal(res.target, path.join(vault, ".opencode"));
  assert.equal(res.vault, vault);
  assert.equal(res.global, false);
});

test("global mode targets home/.config/opencode and has no vault", () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "wk-home-"));
  const res = deploy({ packageRoot: makeFixturePackage(), cwd: os.tmpdir(), home, global: true, stdin: {} });
  assert.equal(res.target, path.join(home, ".config", "opencode"));
  assert.equal(res.vault, null);
  assert.equal(res.global, true);
});

test("full deploy lands the whole surface in the vault", () => {
  const vault = makeVault();
  const res = deploy({ packageRoot: makeFixturePackage(), cwd: vault, home: os.tmpdir(), stdin: {} });
  const t = res.target;
  assert.ok(fs.existsSync(path.join(t, "agents", "wiki-ingest.md")));
  assert.ok(fs.existsSync(path.join(t, "agents", "wiki-researcher.md")));
  assert.ok(fs.existsSync(path.join(t, "commands", "wiki-ingest.md")));
  for (const s of SKILLS) {
    assert.ok(fs.existsSync(path.join(vault, ".agents", "skills", s, "SKILL.md")), `missing skill ${s}`);
  }
  assert.ok(fs.existsSync(path.join(t, "plugins", "session-tracker.ts")));
  assert.ok(fs.existsSync(path.join(t, "wiki-knowledge", "cli.cjs")));
  assert.ok(fs.existsSync(path.join(t, "wiki-knowledge", "node-sqlite3-wasm.wasm")));
  const marker = readJson(path.join(t, "wiki-knowledge", "config.json"));
  assert.equal(marker.plugin_root, path.resolve(t));
  const models = readJson(path.join(t, "wiki-knowledge", "model-config.json"));
  assert.deepEqual(models, DEFAULT_MODELS);
});

test("skills land in .agents/skills (dedicated) vs target/skills (global)", () => {
  const pkg = makeFixturePackage();
  const vault = makeVault();
  deploy({ packageRoot: pkg, cwd: vault, home: os.tmpdir(), stdin: {} });
  assert.ok(fs.existsSync(path.join(vault, ".agents", "skills", "wiki-ingest", "SKILL.md")));
  assert.ok(!fs.existsSync(path.join(vault, ".opencode", "skills")));

  const home = fs.mkdtempSync(path.join(os.tmpdir(), "wk-home-"));
  const vault2 = makeVault();
  deploy({ packageRoot: pkg, cwd: vault2, home, global: true, stdin: {} });
  const gtarget = path.join(home, ".config", "opencode");
  assert.ok(fs.existsSync(path.join(gtarget, "skills", "wiki-ingest", "SKILL.md")));
  assert.ok(!fs.existsSync(path.join(vault2, ".agents")));
});

test("--model-config override wins for provided keys, defaults fill the rest", () => {
  const vault = makeVault();
  const mc = path.join(vault, "models.json");
  fs.writeFileSync(mc, JSON.stringify({ sonnet: "anthropic/claude-sonnet-4-5-custom" }));
  const res = deploy({ packageRoot: makeFixturePackage(), cwd: vault, home: os.tmpdir(), modelConfig: mc, stdin: {} });
  const models = readJson(path.join(res.target, "wiki-knowledge", "model-config.json"));
  assert.equal(models.sonnet, "anthropic/claude-sonnet-4-5-custom");
  assert.equal(models.haiku, DEFAULT_MODELS.haiku);
});

test("non-TTY stdin writes defaults without prompting", () => {
  const vault = makeVault();
  let prompted = false;
  const res = deploy({
    packageRoot: makeFixturePackage({ templates: false }),
    cwd: vault,
    home: os.tmpdir(),
    stdin: {},
    prompt: () => {
      prompted = true;
      return "x";
    },
  });
  assert.equal(prompted, false);
  const models = readJson(path.join(res.target, "wiki-knowledge", "model-config.json"));
  assert.deepEqual(models, DEFAULT_MODELS);
});

test("interactive prompt maps each canonical model, blank falls back to default", () => {
  const vault = makeVault();
  const answers = ["custom/sonnet", ""];
  const res = deploy({
    packageRoot: makeFixturePackage({ templates: false }),
    cwd: vault,
    home: os.tmpdir(),
    stdin: { isTTY: true },
    prompt: () => answers.shift(),
  });
  const models = readJson(path.join(res.target, "wiki-knowledge", "model-config.json"));
  assert.equal(models.sonnet, "custom/sonnet");
  assert.equal(models.haiku, DEFAULT_MODELS.haiku);
});

test("gitignore entries are appended and not duplicated on re-run", () => {
  const pkg = makeFixturePackage();
  const vault = makeVault();
  fs.writeFileSync(path.join(vault, ".gitignore"), "# pre-existing\n");
  deploy({ packageRoot: pkg, cwd: vault, home: os.tmpdir(), stdin: {} });
  const gi = fs.readFileSync(path.join(vault, ".gitignore"), "utf8");
  for (const e of GITIGNORE_ENTRIES) {
    assert.ok(gi.includes(e), `missing gitignore entry ${e}`);
  }
  deploy({ packageRoot: pkg, cwd: vault, home: os.tmpdir(), stdin: {} });
  const gi2 = fs.readFileSync(path.join(vault, ".gitignore"), "utf8");
  assert.ok(gi2.startsWith("# pre-existing\n"), "pre-existing content preserved");
  for (const e of GITIGNORE_ENTRIES) {
    const count = gi2.split("\n").filter((l) => l === e).length;
    assert.equal(count, 1, `${e} duplicated on re-run`);
  }
});

test("gitignore file is created when missing", () => {
  const vault = makeVault();
  deploy({ packageRoot: makeFixturePackage(), cwd: vault, home: os.tmpdir(), stdin: {} });
  const gi = fs.readFileSync(path.join(vault, ".gitignore"), "utf8");
  for (const e of GITIGNORE_ENTRIES) {
    assert.ok(gi.includes(e), `missing gitignore entry ${e}`);
  }
});

test("global mode skips the gitignore duty (no vault)", () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "wk-home-"));
  const vault = makeVault();
  deploy({ packageRoot: makeFixturePackage(), cwd: vault, home, global: true, stdin: {} });
  assert.ok(!fs.existsSync(path.join(vault, ".gitignore")));
});

test("missing source dir errors loudly", () => {
  const pkg = makeFixturePackage();
  fs.rmSync(path.join(pkg, "skills"), { recursive: true, force: true });
  const vault = makeVault();
  assert.throws(
    () => deploy({ packageRoot: pkg, cwd: vault, home: os.tmpdir(), stdin: {} }),
    (e) => e instanceof DeployError && /skills/.test(e.message),
  );
});

test("marker is written with plugin_root = target even without a template", () => {
  const vault = makeVault();
  const res = deploy({ packageRoot: makeFixturePackage({ templates: false }), cwd: vault, home: os.tmpdir(), stdin: {} });
  const marker = readJson(path.join(res.target, "wiki-knowledge", "config.json"));
  assert.deepEqual(marker, { plugin_root: path.resolve(res.target) });
});

test("marker template is honoured with plugin_root injected", () => {
  const pkg = makeFixturePackage();
  fs.writeFileSync(path.join(pkg, "templates", "config.json"), JSON.stringify({ plugin_root: null, extra: "kept" }));
  const vault = makeVault();
  const res = deploy({ packageRoot: pkg, cwd: vault, home: os.tmpdir(), stdin: {} });
  const marker = readJson(path.join(res.target, "wiki-knowledge", "config.json"));
  assert.equal(marker.plugin_root, path.resolve(res.target));
  assert.equal(marker.extra, "kept");
});

test("CLI --help exits 0 and prints usage", () => {
  const res = spawnSync(process.execPath, [path.join(__dirname, "..", "bin", "deploy.js"), "--help"], { encoding: "utf8" });
  assert.equal(res.status, 0, res.stderr);
  assert.match(res.stdout, /wiki-knowledge/);
});

test("CLI deploys a fixture package end-to-end in a vault", () => {
  const pkg = makeFixturePackage();
  fs.mkdirSync(path.join(pkg, "bin"), { recursive: true });
  fs.copyFileSync(path.join(__dirname, "..", "bin", "deploy.js"), path.join(pkg, "bin", "deploy.js"));
  const vault = makeVault();
  const res = spawnSync(process.execPath, [path.join(pkg, "bin", "deploy.js")], {
    cwd: vault,
    encoding: "utf8",
    input: "",
  });
  assert.equal(res.status, 0, res.stderr);
  assert.ok(fs.existsSync(path.join(vault, ".opencode", "wiki-knowledge", "cli.cjs")));
  assert.match(res.stdout, /Installed wiki-knowledge into/);
});

test("CLI exits non-zero when a source is missing", () => {
  const pkg = makeFixturePackage();
  fs.rmSync(path.join(pkg, "agents"), { recursive: true, force: true });
  fs.mkdirSync(path.join(pkg, "bin"), { recursive: true });
  fs.copyFileSync(path.join(__dirname, "..", "bin", "deploy.js"), path.join(pkg, "bin", "deploy.js"));
  const vault = makeVault();
  const res = spawnSync(process.execPath, [path.join(pkg, "bin", "deploy.js")], {
    cwd: vault,
    encoding: "utf8",
    input: "",
  });
  assert.notEqual(res.status, 0);
  assert.match(res.stderr, /missing required sources/);
});