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

/** Prints the standard stub message and marks the process failed. */
function stub(command: Command, label: string): void {
  command.action(() => {
    console.error(`enchiridion ${label}: not yet implemented`);
    process.exitCode = 1;
  });
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
  const page = program.command("page").description("not yet implemented");
  for (const action of ["get", "set", "merge"] as const) {
    const sub = page
      .command(`${action} [args...]`)
      .description("not yet implemented");
    stub(sub, `page ${action}`);
  }

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
