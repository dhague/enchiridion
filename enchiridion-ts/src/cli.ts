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
import path from "node:path";
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
import { decodePlan, resolve, type Plan } from "./ingest.js";
import { append as appendIngestignore } from "./ingestignore.js";
import { Index, type Hit, type Query } from "./searchindex.js";
import {
  check as checkDiscover,
  discover as discoverCandidates,
  tagsContaining,
  tagCounts,
  DefaultLimit as DiscoverDefaultLimit,
  DuplicateThreshold as DiscoverDuplicateThreshold,
  RelatedThreshold as DiscoverRelatedThreshold,
} from "./discover.js";
import {
  DefaultDebounceSeconds,
  DefaultPollIntervalSeconds,
  acquireLock,
  forRoot,
  removeFromQueue,
  runWatch,
} from "./watch.js";
import { canonicalSourceDate } from "./sourcedate.js";

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

const FLAT_SUBCOMMANDS = [] as const;

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

/** Execute an IngestPlan from a plan file (or stdin when planPath is '-'),
 * printing the commit SHA first, then the tool-call summary if a log exists. */
async function runPlan(
  planPath: string,
  root: string,
  dryRun: boolean,
): Promise<void> {
  let text: string;
  if (planPath === "-") {
    text = fs.readFileSync(0, "utf8");
  } else {
    text = fs.readFileSync(planPath, "utf8");
  }
  const plan: Plan = decodePlan(text);
  const resolved = resolve(plan, root);
  resolved.validate();
  if (dryRun) {
    console.log(resolved.describe());
    return;
  }

  const sha = await resolved.execute(new VaultGit(root));
  console.log(sha);
  printToolCallSummary();
}

/** Reports what this run cost, after the SHA, when the PostToolUse hook has
 * been logging calls for the session.
 *
 * Best-effort and silent on failure: a missing or unreadable log just means
 * the run happened outside a hooked session, which is not an ingest error.
 * The SHA stays the first line either way, so callers can still capture it. */
function printToolCallSummary(): void {
  const sessionID = process.env.CLAUDE_CODE_SESSION_ID;
  if (!sessionID) return;
  const events = readLog(sessionID, "");
  if (events.length === 0) return;
  console.log(formatSummary(summarize(events)));
}

/** The JSON shape discover --plan emits: one entry per planned page with its
 * classified candidates. */
interface PagesPayload {
  pages: { title: string; candidates: unknown[] }[];
}

/** The full plan payload: the pages plus the tag vocabulary, the no-flag form. */
interface PlanPayload {
  pages: { title: string; candidates: unknown[] }[];
  vocabulary: { tag: string; count: number }[];
}

/** Write value as two-space-indented JSON, the shape discover emits. */
function printIndentedJSON(value: unknown): void {
  console.log(JSON.stringify(value, null, 2));
}

/** Render a []string as `['a', 'b']` — the plain-text form --tags-containing
 * emits. */
function bracketListRepr(items: string[]): string {
  if (items.length === 0) return "[]";
  return "[" + items.map((item) => `'${item}'`).join(", ") + "]";
}

/** Split a comma-separated flag value into its parts, trimming whitespace and
 * dropping empties. */
function splitCommaList(value: string): string[] {
  return value
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s !== "");
}

/** Accumulate a repeatable flag (--tag/--tag-any) into an array, in order.
 * Commander's processor signature is (value, previous). */
function collectFlag(value: string, previous: string[]): string[] {
  return [...previous, value];
}

/** Render a possibly-empty string as "-", parity with Go's orDash. */
function orDash(s: string): string {
  if (s === "") return "-";
  return s;
}

/** Render a nullable string as "-" when null, else orDash. Parity with Go's
 * orDashPtr. */
function orDashPtr(s: string | null): string {
  if (s === null) return "-";
  return orDash(s);
}

/** Render one Hit as a JSON object line, matching Go's json tags. */
function hitJSON(hit: Hit): string {
  return JSON.stringify({
    page_ref: hit.pageRef,
    score: hit.score,
    title: hit.title,
    summary: hit.summary,
    tags: hit.tags,
    kind: hit.kind,
    source_date: hit.sourceDate,
    git_date: hit.gitDate,
    volatility: hit.volatility,
    superseded_by: hit.supersededBy,
    snippet: hit.snippet,
  });
}

/** Render hits: JSON Lines when asJSON, else the compact one-per-hit table
 * (parity with Go's renderHits). */
function renderHits(hits: Hit[], asJSON: boolean): void {
  if (asJSON) {
    for (const hit of hits) console.log(hitJSON(hit));
    return;
  }
  let width = 0;
  for (const hit of hits) {
    if (hit.pageRef.length > width) width = hit.pageRef.length;
  }
  for (const hit of hits) {
    console.log(
      `${hit.pageRef.padEnd(width)}  ${hit.score.toFixed(2).padStart(7)}  ${orDash(hit.title)}  [${orDash(hit.volatility)}]  src=${orDash(hit.sourceDate)}  git=${orDashPtr(hit.gitDate)}`,
    );
  }
}

/** Render index status (parity with Go's runStatus). */
function renderStatus(
  st: {
    pages: number;
    dbSizeBytes: number;
    backend: string;
    schemaVersion: string;
    gitHead: string;
    uncommittedPages: number;
  },
  asJSON: boolean,
): void {
  if (asJSON) {
    console.log(
      JSON.stringify({
        pages: st.pages,
        db_size_bytes: st.dbSizeBytes,
        backend: st.backend,
        schema_version: st.schemaVersion,
        git_head: st.gitHead,
        uncommitted_pages: st.uncommittedPages,
      }),
    );
    return;
  }
  console.log(`pages:             ${st.pages}`);
  console.log(`db_size_bytes:     ${st.dbSizeBytes}`);
  console.log(`backend:           ${st.backend}`);
  console.log(`schema_version:    ${st.schemaVersion}`);
  console.log(`git_head:          ${orDash(st.gitHead)}`);
  if (st.uncommittedPages > 0) {
    console.log(
      `uncommitted_pages: ${st.uncommittedPages} page(s) on disk not yet committed — not searchable.`,
    );
  } else {
    console.log(`uncommitted_pages: 0`);
  }
}

/** Render a reindex's stats (parity with Go's runReindex). */
function renderReindex(
  stats: {
    pages: number;
    inserted: number;
    updated: number;
    removed: number;
    durationMs: number;
  },
  full: boolean,
  asJSON: boolean,
): void {
  if (asJSON) {
    console.log(
      JSON.stringify({
        pages: stats.pages,
        inserted: stats.inserted,
        updated: stats.updated,
        removed: stats.removed,
        duration_ms: stats.durationMs,
      }),
    );
    return;
  }
  const action = full ? "full reindex" : "reindex";
  console.log(
    `${action}: ${stats.pages} pages (+${stats.inserted} ~${stats.updated} -${stats.removed}) in ${stats.durationMs.toFixed(1)} ms`,
  );
}

/** Execute the --plan mode of discover: classify every planned page and emit
 * the pages payload, optionally replacing the full vocabulary dump with the
 * plain-text tag results. */
async function runDiscoverPlan(
  index: Index,
  planPath: string,
  opts: {
    limit: number;
    duplicateThreshold: number;
    relatedThreshold: number;
  },
  tagsContain: string,
  tagCount: string,
): Promise<void> {
  let text: string;
  if (planPath === "-") {
    text = fs.readFileSync(0, "utf8");
  } else {
    text = fs.readFileSync(planPath, "utf8");
  }
  const plan: Plan = decodePlan(text);
  const results = await discoverCandidates(index, plan.pages, opts);

  const pages = results.map((r) => ({
    title: r.title,
    candidates: r.candidates,
  }));

  const vocab = await index.tagCounts();

  if (tagsContain === "" && tagCount === "") {
    printIndentedJSON({ pages, vocabulary: vocab } satisfies PlanPayload);
    return;
  }

  printIndentedJSON({ pages } satisfies PagesPayload);
  if (tagsContain !== "") {
    const matches = tagsContaining(vocab, splitCommaList(tagsContain));
    console.log(bracketListRepr(matches));
  }
  if (tagCount !== "") {
    const counts = tagCounts(vocab, splitCommaList(tagCount));
    for (const tc of counts) {
      console.log(`${tc.tag} count: ${tc.count}`);
    }
  }
}

/** Appends rawRel to its own folder's `.ingestignore`.
 *
 * rawRel is vault-relative, exactly as the sweep prints it
 * (`raw/emails/foo.eml`). */
function ignoreRawFile(root: string, rawRel: string, comment: string): void {
  const rel = path.posix.normalize(rawRel);
  if (!rel.startsWith("raw/") || rel.length <= "raw/".length) {
    throw new Error(
      `--ignore takes a vault-relative path under raw/, got ${JSON.stringify(rawRel)}`,
    );
  }
  const folder = path.join(
    root,
    "raw",
    path.posix.dirname(rel.slice("raw/".length)),
  );
  appendIngestignore(folder, path.posix.basename(rel), comment);
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

  // search [text] — query the lexical index, or manage it with --reindex /
  // --status (parity with enchiridion-go/internal/cli/search.go). Default
  // mode is a query: positional text plus any metadata filter; --json emits
  // one Hit per line, else the compact one-line-per-hit table.
  program
    .command("search [text]")
    .description("Search the wiki vault via the lexical index")
    .option(
      "--tag <tag>",
      "filter by tag; repeat for tags_all (AND) and combine with --tag-any for OR",
      collectFlag,
      [] as string[],
    )
    .option(
      "--tag-any <tag>",
      "filter by tag (OR semantics across the listed tags)",
      collectFlag,
      [] as string[],
    )
    .option(
      "--kind <kinds>",
      "filter by kind (concept|entity|source|synthesis); comma-separated for multiple",
      splitCommaList,
      [] as string[],
    )
    .option("--since <date>", "ISO date; inclusive lower bound on date_field")
    .option("--until <date>", "ISO date; inclusive upper bound on date_field")
    .option(
      "--date-field <field>",
      "which date the --since/--until bounds apply to (source_date|git_date)",
      (value: string) => {
        if (value !== "source_date" && value !== "git_date") {
          throw new Error(
            `must be 'source_date' or 'git_date', got "${value}"`,
          );
        }
        return value;
      },
      "source_date",
    )
    .option(
      "--volatility <vols>",
      "filter by volatility (stable|evolving|volatile); comma-separated for multiple",
      splitCommaList,
      [] as string[],
    )
    .option("--limit <n>", "max hits", (v: string) => Number(v), 20)
    .option(
      "--include-superseded",
      "include pages that have been superseded (default: filter them out)",
    )
    .option(
      "--raw",
      "pass the text through as a literal FTS5 expression (escape hatch)",
    )
    .option("--json", "emit results as JSON Lines (one object per line)")
    .option("--reindex", "rebuild the index")
    .option("--full", "with --reindex: wipe the index and rebuild from scratch")
    .option("--status", "print index status and exit")
    .action(
      async (
        text: string | undefined,
        opts: {
          tag: string[];
          tagAny: string[];
          kind: string[];
          since?: string;
          until?: string;
          dateField: string;
          volatility: string[];
          limit: number;
          includeSuperseded?: boolean;
          raw?: boolean;
          json?: boolean;
          reindex?: boolean;
          full?: boolean;
          status?: boolean;
        },
      ) => {
        const { root } = resolveRoot();
        const index = await Index.open(root);
        try {
          if (opts.status) {
            renderStatus(await index.status(), opts.json ?? false);
            return;
          }
          if (opts.reindex) {
            renderReindex(
              await index.reindex(opts.full ?? false),
              opts.full ?? false,
              opts.json ?? false,
            );
            return;
          }
          const query: Query = {
            text: text ?? "",
            raw: opts.raw ?? false,
            tagsAll: opts.tag,
            tagsAny: opts.tagAny,
            kinds: opts.kind,
            since: opts.since ?? "",
            until: opts.until ?? "",
            dateField: opts.dateField,
            volatility: opts.volatility,
            includeSuperseded: opts.includeSuperseded ?? false,
            limit: opts.limit,
          };
          renderHits(await index.search(query), opts.json ?? false);
        } finally {
          index.close();
        }
      },
    );

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

  // watch — a long-running filesystem watcher over raw/ with per-file
  // debounce, an exclusive lock, and a queue file (parity with
  // enchiridion-go/internal/cli/watch.go). `--dequeue <raw_rel>` removes one
  // queue entry and exits.
  program
    .command("watch")
    .description("Watch raw/ for new files and enqueue eligible ones")
    .option("--vault <root>", "vault root; defaults to resolve_vault_root()")
    .option(
      "--debounce <seconds>",
      `per-file debounce, seconds (default ${DefaultDebounceSeconds})`,
      (v: string) => Number(v),
      DefaultDebounceSeconds,
    )
    .option(
      "--poll-interval <seconds>",
      `how often to check for settled files, seconds (default ${DefaultPollIntervalSeconds})`,
      (v: string) => Number(v),
      DefaultPollIntervalSeconds,
    )
    .option(
      "--dequeue <rel>",
      "remove this vault-relative path from the watch queue and exit, instead of watching",
    )
    .action(
      async (opts: {
        vault?: string;
        debounce: number;
        pollInterval: number;
        dequeue?: string;
      }) => {
        let root = opts.vault ?? "";
        if (root === "") {
          ({ root } = resolveRoot());
        } else {
          try {
            root = fs.realpathSync(root);
          } catch {
            // A root that doesn't exist yet resolves as-is.
          }
        }
        const paths = forRoot(root);

        if (opts.dequeue) {
          removeFromQueue(paths.queue, opts.dequeue);
          return;
        }

        const { acquired, stalePID } = acquireLock(paths.lock);
        if (!acquired) {
          throw new Error(
            `another watcher is already running (lock at ${paths.lock})`,
          );
        }
        if (stalePID !== null) {
          console.log(
            `previous watcher exited without cleanup, removing stale lock (pid=${stalePID})`,
          );
        }
        await runWatch(paths, {
          debounceSeconds: opts.debounce,
          pollIntervalSeconds: opts.pollInterval,
        });
      },
    );

  // ingest — execute an IngestPlan against the resolved vault (parity with
  // enchiridion-go/internal/cli/ingest.go). Validates the whole plan up front
  // (shape, then the vault-dependent checks) then writes every page and
  // commits in one pass, printing the commit SHA as the first stdout line.
  program
    .command("ingest")
    .description("Execute an IngestPlan against the resolved vault")
    .option(
      "--plan <file>",
      "path to an IngestPlan JSON file ('-' reads stdin)",
    )
    .option(
      "--ignore <rawRel>",
      "never offer this raw/ file again for a sweep (appends it to its folder's .ingestignore)",
    )
    .option(
      "--ignore-comment <comment>",
      "optional trailing comment for the --ignore entry",
    )
    .option(
      "--dry-run",
      "resolve and validate the plan, print what would be written, write nothing",
    )
    .action(
      async (opts: {
        plan?: string;
        ignore?: string;
        ignoreComment?: string;
        dryRun?: boolean;
      }) => {
        const planPath = opts.plan ?? "";
        const ignoreRel = opts.ignore ?? "";
        if (opts.dryRun && planPath === "") {
          throw new Error(
            "--dry-run only applies to --plan; --ignore always writes",
          );
        }
        if ((planPath === "") === (ignoreRel === "")) {
          throw new Error("exactly one of --plan or --ignore is required");
        }
        const { root } = resolveRoot();
        if (ignoreRel !== "") {
          ignoreRawFile(root, ignoreRel, opts.ignoreComment ?? "");
          return;
        }
        await runPlan(planPath, root, opts.dryRun ?? false);
      },
    );

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

  // discover — single-call discovery for ingestion (parity with
  // enchiridion-go/internal/cli/discover.go). Two modes: --plan <draft.json>
  // discovers candidates for every page in the draft plus the vault's tag
  // vocabulary; --title/--summary/--body-file is single-page mode, emitting
  // one candidate per line.
  program
    .command("discover")
    .description(
      "Find pages overlapping a planned page, plus the tag vocabulary",
    )
    .option(
      "--plan <file>",
      "path to a draft IngestPlan JSON ('-' reads stdin); discovers candidates for every page in it, plus the vault's tag vocabulary",
    )
    .option("--title <text>", "the planned page's own title (single-page mode)")
    .option(
      "--summary <text>",
      "the planned page's own summary (single-page mode)",
    )
    .option(
      "--body-file <file>",
      "path to the planned page's own body text (single-page mode)",
    )
    .option(
      "--limit <n>",
      `max candidates per page (default ${DiscoverDefaultLimit})`,
      (v: string) => Number(v),
      DiscoverDefaultLimit,
    )
    .option(
      "--duplicate-threshold <n>",
      "",
      (v: string) => Number(v),
      DiscoverDuplicateThreshold,
    )
    .option(
      "--related-threshold <n>",
      "",
      (v: string) => Number(v),
      DiscoverRelatedThreshold,
    )
    .option(
      "--tags-containing <substrings>",
      "comma-separated substrings (case-insensitive OR match); with --plan, replaces the full tag-vocabulary JSON dump with the plain-text list of matching vault tags",
    )
    .option(
      "--tag-count <tags>",
      "comma-separated exact tag names; with --plan, replaces the full tag-vocabulary JSON dump with plain-text per-tag page counts (0 if the tag doesn't exist yet)",
    )
    .action(
      async (opts: {
        plan?: string;
        title?: string;
        summary?: string;
        bodyFile?: string;
        limit: number;
        duplicateThreshold: number;
        relatedThreshold: number;
        tagsContaining?: string;
        tagCount?: string;
      }) => {
        const { root } = resolveRoot();
        const discoverOpts = {
          limit: opts.limit,
          duplicateThreshold: opts.duplicateThreshold,
          relatedThreshold: opts.relatedThreshold,
        };
        // The one index handle for this run — one per vault at a time
        // (ADR-0010), owned here because this command is the only thing that
        // needs one.
        const index = await Index.open(root);
        try {
          if (opts.plan) {
            await runDiscoverPlan(
              index,
              opts.plan,
              discoverOpts,
              opts.tagsContaining ?? "",
              opts.tagCount ?? "",
            );
            return;
          }
          let body = "";
          if (opts.bodyFile) {
            body = fs.readFileSync(opts.bodyFile, "utf8");
          }
          const candidates = await checkDiscover(
            index,
            opts.title ?? "",
            opts.summary ?? "",
            body,
            discoverOpts,
          );
          for (const c of candidates) {
            console.log(JSON.stringify(c));
          }
        } finally {
          index.close();
        }
      },
    );

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
