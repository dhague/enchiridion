#!/usr/bin/env node
/**
 * enchiridion CLI entry point.
 *
 * One subcommand per capability, mirroring enchiridion-go/internal/cli (the
 * TypeScript port target — see ADR-0017 and issue #252/#254). This ticket
 * wires the commander scaffold only: every subcommand below is a stub that
 * exits non-zero with "not yet implemented" until its own module ticket
 * lands (see the port sequence in #252's Implementation Decisions).
 *
 * `vault`, `page`, and `hook` are deliberately spelled with the nested
 * sub-subcommands CLAUDE.md documents (`vault root|move`, `page
 * get|set|merge`, `hook session-start|post-tool-use`) so later tickets wire
 * logic into an already-correct surface instead of reshaping the CLI.
 */

import { Command } from "commander";
import fs from "node:fs";
import { Page } from "./wikipage.js";
import { captureSession } from "./transcriptcapture.js";
import { formatSummary, logPath, readLog, summarize } from "./toolcallstats.js";
import { Kinds, path as placePath } from "./place.js";
import { Vault, resolveRoot } from "./vault.js";
import { VaultGit } from "./vaultgit.js";
import { resolve as resolveSuperseded } from "./supersededby.js";
import { scan as scanIngest } from "./ingestscan.js";
import { commit as commitManifest, type Manifest } from "./commit.js";
import { init as initWiki, Modes } from "./initwiki.js";
import { sessionStart, postToolUse } from "./hooks.js";

/** Prints the standard stub message and marks the process failed. */
function stub(command: Command, label: string): void {
  command.action(() => {
    console.error(`enchiridion ${label}: not yet implemented`);
    process.exitCode = 1;
  });
}

function loadPage(file: string): Page {
  return new Page(fs.readFileSync(file, "utf8"));
}

function writePageFile(file: string, page: Page): void {
  fs.writeFileSync(file, page.text, { mode: 0o644 });
}

/**
 * Render a frontmatter value as plain text — notably a list as `['a', 'b']`,
 * the form callers of `page get` have always parsed.
 */
function formatFrontmatterValue(value: unknown): string {
  if (!Array.isArray(value)) return formatScalar(value);
  return "[" + value.map((v) => `'${formatScalar(v)}'`).join(", ") + "]";
}

function formatScalar(value: unknown): string {
  if (typeof value === "boolean") return value ? "True" : "False";
  return String(value);
}

/**
 * Canonicalise a `source_date` value to YYYY-MM-DD, truncating a clock. A
 * value that isn't a valid date at all is refused — the same rejection
 * ingest's validation applies.
 */
function canonicalSourceDate(value: unknown): unknown {
  if (value === null || value === undefined) return value;
  const m = String(value).match(/^(\d{4}-\d{2}-\d{2})/);
  if (!m) {
    throw new Error(
      `source_date must be a valid date (YYYY-MM-DD), got ${String(value)}`,
    );
  }
  return m[1];
}

const FLAT_SUBCOMMANDS = ["search", "ingest", "discover", "watch"] as const;

/** Normalise a CLI folder argument: "" and "raw/" both mean all of raw/; a
 * "raw/" prefix is stripped, so "notes" and "raw/notes" are interchangeable.
 */
function normalizeFolderArg(arg: string): string {
  if (arg === "" || arg === "raw/") return "";
  return arg.startsWith("raw/") ? arg.slice("raw/".length) : arg;
}

/** Render the scan result's tabular form (parity with the Go renderScanTable):
 * right-aligned raw/ paths followed by their reason, then the ignored block. */
function renderScanTable(result: {
  eligible: { rawRel: string; reason: string }[];
  ignored: string[];
}): void {
  if (result.eligible.length === 0 && result.ignored.length === 0) {
    console.log("no eligible files; 0 ignored");
    return;
  }
  let width = 10;
  for (const c of result.eligible) {
    if (c.rawRel.length > width) width = c.rawRel.length;
  }
  for (const rawRel of result.ignored) {
    if (rawRel.length > width) width = rawRel.length;
  }
  for (const c of result.eligible) {
    console.log(`${c.rawRel.padEnd(width)}  ${c.reason}`);
  }
  if (result.ignored.length > 0) {
    console.log(`\n${result.ignored.length} ignored by .ingestignore:`);
    for (const rawRel of result.ignored) console.log(`  ${rawRel}`);
  }
}

export function buildProgram(): Command {
  const program = new Command();
  program
    .name("enchiridion")
    .description(
      "Wiki-knowledge plugin script layer (TypeScript port — ADR-0017)",
    )
    .allowExcessArguments(true)
    .allowUnknownOption(true);

  for (const name of FLAT_SUBCOMMANDS) {
    const sub = program
      .command(`${name} [args...]`)
      .description("not yet implemented");
    stub(sub, name);
  }

  // init <path> — scaffold a brand-new wiki vault (parity with
  // enchiridion-go/internal/cli/init.go). Takes an explicit path argument, not
  // a resolved root; prints the resolved vault root on success — the only
  // thing on stdout, so a caller can capture it.
  program
    .command("init <path>")
    .description(
      `Scaffold a brand-new wiki vault; --mode is one of: ${Modes.join(", ")}`,
    )
    .requiredOption(
      "--mode <mode>",
      `deployment mode: one of ${Modes.join(", ")}`,
    )
    .option(
      "--plugin-root <dir>",
      "this plugin's install dir (required for query-from-anywhere)",
    )
    .action(
      async (
        vaultPath: string,
        opts: { mode: string; pluginRoot?: string },
      ) => {
        const root = await initWiki(
          vaultPath,
          opts.mode,
          opts.pluginRoot ?? "",
        );
        console.log(root);
      },
    );

  // place <kind> <title> — compute a new page's vault-relative path from its
  // kind and title. Resolves no vault root and reads nothing from disk: only
  // the four canonical kinds are accepted, never a vault's discovered custom
  // kind-folders. placePath rejects anything else.
  program
    .command("place <kind> <title>")
    .description(
      `Compute a new page's vault-relative path from its kind and title; kind is one of: ${Kinds.join(", ")}`,
    )
    .action((kind: string, title: string) => {
      const rel = placePath(kind, title, undefined);
      console.log(rel);
    });

  // save-session — find, render, and write this session's transcript,
  // printing the vault-relative path of the raw file written (parity with
  // enchiridion-go/internal/cli/savesession.go).
  program
    .command("save-session")
    .description("Save this session's transcript as a raw file in the vault")
    .option(
      "--slug <phrase>",
      "phrase naming what this session covered; sanitized, first-save only",
    )
    .action(async (opts: { slug?: string }) => {
      const { root } = resolveRoot();
      const rel = await captureSession(
        root,
        opts.slug ?? "",
        "",
        undefined,
        new Date(),
      );
      console.log(rel);
    });

  // tool-call-stats — summarise the tool-call log for one session (parity
  // with enchiridion-go/internal/cli/toolcallstats.go).
  program
    .command("tool-call-stats")
    .description("Summarise a session's tool-call log")
    .option(
      "--session-id <id>",
      "session to summarise (default: $CLAUDE_CODE_SESSION_ID)",
    )
    .action((opts: { sessionId?: string }) => {
      let id = opts.sessionId ?? "";
      if (id === "") id = process.env.CLAUDE_CODE_SESSION_ID ?? "";
      if (id === "") {
        throw new Error(
          "no session_id — pass --session-id or set $CLAUDE_CODE_SESSION_ID",
        );
      }
      const events = readLog(id, "");
      if (events.length === 0) {
        throw new Error(`no log found at ${logPath(id, "")}`);
      }
      console.log(formatSummary(summarize(events)));
    });

  // vault — bare or `vault root` prints the resolved root; `vault move
  // <old_ref> <new_ref>` moves a page and fixes every link. The one
  // subcommand that resolves a vault root (CLAUDE.md). The parent's action
  // runs for bare `vault`, and is inherited by `vault root` and `vault move`
  // (commander runs a parent's action when a subcommand has no handler of its
  // own; the subcommand's own args are parsed before it).
  const vault = program
    .command("vault")
    .description(
      "Resolve the vault root, or move a page within it (moves need exactly two page refs)",
    )
    .action(() => {
      const { root } = resolveRoot();
      console.log(root);
    });
  vault
    .command("root")
    .description("Print the resolved vault root (the no-argument default)")
    .action(() => {
      const { root } = resolveRoot();
      console.log(root);
    });
  vault
    .command("move")
    .description(
      "Move a page within the vault and fix every link, inbound and outbound",
    )
    .argument("<old_ref>", "vault-relative path of the page to move")
    .argument("<new_ref>", "vault-relative destination path")
    .action((oldRef: string, newRef: string) => {
      const { root } = resolveRoot();
      const changed = new Vault(root).movePage(oldRef, newRef);
      for (const pageRef of changed) console.log(pageRef);
    });

  // page get|set|merge <file> <key> ... — the frontmatter trio. Resolves no
  // vault root (CLAUDE.md).
  const page = program
    .command("page")
    .description("Read and edit one page's frontmatter");

  page
    .command("get")
    .argument("<file>", "markdown file")
    .argument("<key>", "frontmatter key")
    .description("Print a frontmatter value")
    .action((file: string, key: string) => {
      const p = loadPage(file);
      const { value, ok } = p.get(key);
      if (!ok || value === null || value === undefined) {
        console.error(`no frontmatter key "${key}" in ${file}`);
        process.exitCode = 1;
        return;
      }
      console.log(formatFrontmatterValue(value));
    });

  page
    .command("set")
    .argument("<file>", "markdown file")
    .argument("<key>", "frontmatter key")
    .argument("<value>", "frontmatter value")
    .option("--json", "parse value as JSON")
    .description("Set a frontmatter value in place")
    .action(
      (file: string, key: string, raw: string, opts: { json?: boolean }) => {
        const p = loadPage(file);
        let value: unknown = raw;
        if (opts.json) {
          try {
            value = JSON.parse(raw);
          } catch {
            throw new Error(`parsing ${key} as JSON: invalid JSON`);
          }
        }
        if (key === "source_date") value = canonicalSourceDate(value);
        const updated = p.set(key, value);
        writePageFile(file, updated);
      },
    );

  page
    .command("merge")
    .argument("<file>", "markdown file")
    .argument("<key>", "frontmatter key")
    .argument("<json-list>", "JSON list of values to union in")
    .description("Union a JSON list into an existing list-valued key")
    .action((file: string, key: string, raw: string) => {
      const p = loadPage(file);
      let values: unknown[];
      try {
        values = JSON.parse(raw);
      } catch {
        throw new Error(`merge expects a JSON list for ${key}`);
      }
      if (!Array.isArray(values)) {
        throw new Error(`merge expects a JSON list for ${key}`);
      }
      const updated = p.merge(key, values);
      writePageFile(file, updated);
    });

  // superseded-by <page_ref>... — resolve a candidate set's supersession
  // chains to current heads (parity with
  // enchiridion-go/internal/cli/supersededby.go).
  program
    .command("superseded-by <page_ref...>")
    .description("Resolve page refs to their current supersession heads")
    .option("--json", "emit results as JSON Lines (one object per line)")
    .action(async (pageRefs: string[], opts: { json?: boolean }) => {
      const { root } = resolveRoot();
      const records = new Vault(root).pages();
      const resolutions = resolveSuperseded(pageRefs, records);

      if (opts.json) {
        for (const res of resolutions) console.log(JSON.stringify(res));
        return;
      }
      for (const res of resolutions) {
        if (res.chain.length === 0) {
          console.log(`${res.seed}  (current)`);
          continue;
        }
        let via = "";
        if (res.chain.length > 1) {
          via = ` via ${res.chain.slice(0, -1).join(" -> ")}`;
        }
        console.log(`${res.seed}  ->  ${res.active}${via}`);
      }
    });

  // ingest-scan [folder] — scan raw/ for files that need ingestion (parity
  // with enchiridion-go/internal/cli/ingestscan.go).
  program
    .command("ingest-scan [folder]")
    .description("Scan raw/ for files that need ingestion")
    .option(
      "--json",
      "emit JSON Lines (one eligible or ignored record per line)",
    )
    .action(async (folderArg: string | undefined, opts: { json?: boolean }) => {
      const { root } = resolveRoot();
      const folder =
        folderArg === undefined ? "" : normalizeFolderArg(folderArg);
      const result = await scanIngest(root, folder, new VaultGit(root));
      if (opts.json) {
        for (const c of result.eligible) {
          console.log(
            JSON.stringify({
              kind: "eligible",
              raw_rel: c.rawRel,
              reason: c.reason,
              back_pointers: c.backPointers,
            }),
          );
        }
        for (const rawRel of result.ignored) {
          console.log(JSON.stringify({ kind: "ignored", raw_rel: rawRel }));
        }
        return;
      }
      renderScanTable(result);
    });

  // commit — write one structured git commit per manifest (parity with
  // enchiridion-go/internal/cli/commit.go): a hand-built manifest in, one
  // structured commit out. `enchiridion ingest` commits its own plan; this is
  // for a manifest an agent assembles directly.
  program
    .command("commit")
    .description("Write one structured git commit per manifest")
    .option(
      "--manifest <file>",
      "path to a manifest JSON file ('-' reads stdin)",
    )
    .action(async (opts: { manifest?: string }) => {
      if (!opts.manifest) {
        throw new Error("required option '--manifest <file>' not specified");
      }
      const { root } = resolveRoot();
      let text: string;
      if (opts.manifest === "-") {
        text = fs.readFileSync(0, "utf8");
      } else {
        text = fs.readFileSync(opts.manifest, "utf8");
      }
      const manifest = JSON.parse(text) as Manifest;
      const sha = await commitManifest(root, manifest, new VaultGit(root));
      console.log(sha);
    });

  // hook session-start|post-tool-use — read their payload on stdin, fail
  // open (CLAUDE.md). Hooks fire automatically rather than being
  // agent-invoked, so every handler error is swallowed and the command exits
  // 0, and the session continues with that hook's side effect missing for
  // this run.
  const hook = program
    .command("hook")
    .description("Handle a Claude Code hook payload read from stdin")
    .action(() => {
      // A bare `hook`, or an unrecognised event name, is an error rather than
      // commander's default "print help, exit 0" — a hooks.json typo must not
      // look like it worked.
      throw new Error(
        `hook: name the event, one of ${["session-start", "post-tool-use"].join(", ")}`,
      );
    });
  for (const action of ["session-start", "post-tool-use"] as const) {
    hook
      .command(action)
      .description("Handle the " + action + " hook event")
      .action(() => {
        // Fail open: read the payload, run the handler, and swallow every
        // error so a hook failure can never interrupt the session it
        // triggered. Malformed JSON on stdin is one such failure, not a
        // reason to exit non-zero.
        try {
          const payload = JSON.parse(fs.readFileSync(0, "utf8"));
          if (action === "session-start") sessionStart(payload);
          else postToolUse(payload);
        } catch {
          // The error is deliberately dropped, not reported: stderr from a
          // hook is surfaced to the user mid-session, and there is nothing
          // they can act on.
        }
      });
  }

  return program;
}

function main(): void {
  const program = buildProgram();
  if (process.argv.slice(2).length === 0) {
    // Commander's own default for "subcommands registered, none given, no
    // root action handler" treats bare invocation as probably-missing-
    // subcommand: help to stderr, exit 1. Match the Go/cobra binary's
    // bare-invocation behaviour instead — usage to stdout, exit 0 — by
    // calling help() directly rather than going through parse().
    program.help();
    return;
  }
  program.parse(process.argv);
}

main();
