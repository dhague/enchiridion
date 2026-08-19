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

const FLAT_SUBCOMMANDS = [
  "search",
  "init",
  "ingest",
  "discover",
  "ingest-scan",
  "watch",
  "save-session",
  "tool-call-stats",
  "commit",
  "superseded-by",
  "place",
] as const;

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

  // vault — bare or `vault root` prints the resolved root; `vault move
  // <old_ref> <new_ref>` moves a page and fixes every link. The one
  // subcommand that resolves a vault root (CLAUDE.md).
  const vault = program.command("vault").description("not yet implemented");
  stub(vault, "vault");
  const vaultRoot = vault.command("root").description("not yet implemented");
  stub(vaultRoot, "vault root");
  const vaultMove = vault
    .command("move [args...]")
    .description("not yet implemented");
  stub(vaultMove, "vault move");

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

  // hook session-start|post-tool-use — read their payload on stdin, fail
  // open (CLAUDE.md). Stub still exits non-zero for now; the real
  // handlers will swallow errors and exit 0 once implemented.
  const hook = program.command("hook").description("not yet implemented");
  for (const action of ["session-start", "post-tool-use"] as const) {
    const sub = hook.command(action).description("not yet implemented");
    stub(sub, `hook ${action}`);
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
