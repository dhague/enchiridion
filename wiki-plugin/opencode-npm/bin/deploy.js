#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const DEFAULT_MODELS = Object.freeze({
  sonnet: "anthropic/claude-sonnet-4-5",
  haiku: "anthropic/claude-haiku-4-5",
});

const CANONICAL_MODELS = Object.freeze(["sonnet", "haiku"]);

const GITIGNORE_ENTRIES = Object.freeze([
  ".agents/skills/",
  ".opencode/agents/",
  ".opencode/commands/",
  ".opencode/plugins/session-tracker.ts",
  ".opencode/wiki-knowledge/",
]);

const GITIGNORE_HEADER = "# @dhague/wiki-knowledge OpenCode deploy (re-run: npx @dhague/wiki-knowledge)";

const REQUIRED_SOURCES = Object.freeze([
  "agents",
  "commands",
  "skills",
  "plugins/session-tracker.ts",
  "wiki-knowledge/cli.cjs",
  "wiki-knowledge/node-sqlite3-wasm.wasm",
]);

const USAGE = `Usage: wiki-knowledge [options]

Deploy the @dhague/wiki-knowledge plugin into an OpenCode vault.
Skills go to the vault's .agents/skills/ (or ~/.config/opencode/skills/ with --global);
agents, commands, session-tracker, marker, model config and runtime go to .opencode/.

Options:
  --global               install into ~/.config/opencode (query-from-anywhere)
  --model-config <path>  JSON mapping canonical model names to provider/model-id
  --help, -h             show this help
`;

class DeployError extends Error {}

function copyFile(src, dest) {
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(src, dest);
}

function copyDir(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, entry.name);
    const d = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      copyDir(s, d);
    } else if (entry.isFile()) {
      copyFile(s, d);
    }
  }
}

function readJsonFile(file, what) {
  let data;
  try {
    data = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (err) {
    throw new DeployError(`${what} ${file} is not valid JSON: ${err.message}`);
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new DeployError(`${what} ${file} must be a JSON object`);
  }
  return data;
}

function requireSources(pkg) {
  const missing = REQUIRED_SOURCES.filter((rel) => !fs.existsSync(path.join(pkg, rel)));
  if (missing.length > 0) {
    throw new DeployError(`package ${pkg} is missing required sources: ${missing.join(", ")}`);
  }
}

function readModelDefaults(pkg) {
  const template = path.join(pkg, "templates", "model-config.json");
  if (fs.existsSync(template)) {
    return { ...DEFAULT_MODELS, ...readJsonFile(template, "model-config template") };
  }
  return { ...DEFAULT_MODELS };
}

function readLine() {
  const buf = Buffer.alloc(1);
  let line = "";
  for (;;) {
    let n;
    try {
      n = fs.readSync(0, buf, 0, 1, null);
    } catch {
      break;
    }
    if (n <= 0) break;
    const ch = buf.toString("utf8");
    if (ch === "\n") break;
    line += ch;
  }
  return line.replace(/\r$/, "");
}

function defaultPrompt(message) {
  process.stdout.write(message);
  const answer = readLine();
  process.stdout.write("\n");
  return answer;
}

function resolveModels({ pkg, modelConfig, stdin, prompt }) {
  const defaults = readModelDefaults(pkg);
  if (modelConfig) {
    return { ...defaults, ...readJsonFile(modelConfig, "model config") };
  }
  if (stdin && stdin.isTTY) {
    const ask = prompt || defaultPrompt;
    const mapping = {};
    for (const model of CANONICAL_MODELS) {
      const def = defaults[model] || "";
      const message = def
        ? `OpenCode model id for '${model}' (default '${def}'): `
        : `OpenCode model id for '${model}': `;
      mapping[model] = ask(message).trim() || def;
    }
    return mapping;
  }
  return defaults;
}

function writeJson(file, obj) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(obj, null, 2) + "\n", "utf8");
}

function writeMarker(target, pkg, pluginRoot) {
  const template = path.join(pkg, "templates", "config.json");
  const marker = fs.existsSync(template) ? readJsonFile(template, "marker template") : {};
  marker.plugin_root = pluginRoot;
  writeJson(path.join(target, "wiki-knowledge", "config.json"), marker);
}

function appendGitignore(vault) {
  const file = path.join(vault, ".gitignore");
  const existing = fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "";
  const lines = existing.split(/\r?\n/);
  const toAdd = GITIGNORE_ENTRIES.filter((e) => !lines.includes(e));
  if (toAdd.length === 0) return file;
  const trimmed = existing.replace(/\n+$/, "");
  const body =
    trimmed.length > 0
      ? `${trimmed}\n\n${GITIGNORE_HEADER}\n${toAdd.join("\n")}\n`
      : `${GITIGNORE_HEADER}\n${toAdd.join("\n")}\n`;
  fs.writeFileSync(file, body, "utf8");
  return file;
}

function patchAgentModels(agentsDir, models) {
  if (!fs.existsSync(agentsDir)) return;
  for (const entry of fs.readdirSync(agentsDir, { withFileTypes: true })) {
    if (!entry.isFile() || !entry.name.endsWith(".md")) continue;
    const file = path.join(agentsDir, entry.name);
    let content = fs.readFileSync(file, "utf8");
    for (const [tier, modelId] of Object.entries(models)) {
      const defaultId = DEFAULT_MODELS[tier];
      if (defaultId && modelId !== defaultId) {
        content = content.replace(
          new RegExp(`^(model:\\s*)${escapeRegex(defaultId)}\\s*$`, "m"),
          `$1${modelId}`,
        );
      }
    }
    fs.writeFileSync(file, content, "utf8");
  }
}

function escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function deploy(opts = {}) {
  const pkg = path.resolve(opts.packageRoot || path.join(__dirname, ".."));
  const cwd = path.resolve(opts.cwd || process.cwd());
  const home = path.resolve(opts.home || os.homedir());
  const global = Boolean(opts.global);
  const stdin = opts.stdin || process.stdin;

  requireSources(pkg);

  const target = global ? path.join(home, ".config", "opencode") : path.join(cwd, ".opencode");
  const vault = global ? null : cwd;
  const pluginRoot = path.resolve(target);

  const skillsDest = global ? path.join(target, "skills") : path.join(cwd, ".agents", "skills");
  copyDir(path.join(pkg, "skills"), skillsDest);
  copyDir(path.join(pkg, "agents"), path.join(target, "agents"));
  copyDir(path.join(pkg, "commands"), path.join(target, "commands"));
  copyFile(
    path.join(pkg, "plugins", "session-tracker.ts"),
    path.join(target, "plugins", "session-tracker.ts"),
  );
  copyDir(path.join(pkg, "wiki-knowledge"), path.join(target, "wiki-knowledge"));

  const models = resolveModels({ pkg, modelConfig: opts.modelConfig, stdin, prompt: opts.prompt });
  writeJson(path.join(target, "wiki-knowledge", "model-config.json"), models);
  patchAgentModels(path.join(target, "agents"), models);
  writeMarker(target, pkg, pluginRoot);

  const gitignore = global ? null : appendGitignore(vault);

  return { target, vault, global, pluginRoot, models, gitignore };
}

function main(argv = process.argv.slice(2), io = {}) {
  const stdout = io.stdout || process.stdout;
  const stderr = io.stderr || process.stderr;
  let global = false;
  let modelConfig = null;
  let help = false;
  try {
    for (let i = 0; i < argv.length; i++) {
      const arg = argv[i];
      if (arg === "--global") {
        global = true;
      } else if (arg === "--model-config") {
        const value = argv[i + 1];
        if (!value || value.startsWith("--")) throw new DeployError("--model-config requires a path");
        modelConfig = value;
        i++;
      } else if (arg.startsWith("--model-config=")) {
        modelConfig = arg.slice("--model-config=".length);
      } else if (arg === "--help" || arg === "-h") {
        help = true;
      } else {
        throw new DeployError(`unknown option: ${arg}`);
      }
    }
  } catch (err) {
    stderr.write(`error: ${err.message}\n`);
    return 1;
  }
  if (help) {
    stdout.write(USAGE);
    return 0;
  }
  try {
    const result = deploy({ global, modelConfig, cwd: io.cwd, home: io.home, stdin: io.stdin, prompt: io.prompt });
    stdout.write(`Installed wiki-knowledge into ${result.target}\n`);
    stdout.write(
      result.global
        ? "Re-run npx @dhague/wiki-knowledge to update (query-from-anywhere).\n"
        : "Re-run npx @dhague/wiki-knowledge to update; the deployed surface is gitignored.\n",
    );
    return 0;
  } catch (err) {
    stderr.write(`error: ${err.message}\n`);
    return 1;
  }
}

module.exports = {
  deploy,
  main,
  DeployError,
  DEFAULT_MODELS,
  CANONICAL_MODELS,
  GITIGNORE_ENTRIES,
};

if (require.main === module) {
  process.exitCode = main();
}