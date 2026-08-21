/**
 * Read and append to `.ingestignore` — the human-authored policy file that
 * permanently withdraws a raw/ file from the ingestion sweep.
 *
 * A `.ingestignore` is read from a raw file's own folder only, with **no
 * ancestor walk** — what keeps a hand-written policy file from drifting into
 * a machine-written done-list.
 */

import fs from "node:fs";
import path from "node:path";

/** The policy file's fixed name, in the folder whose files it governs. */
export const Filename = ".ingestignore";

/** Read `.ingestignore` text into its patterns, in order.
 *
 * Strips `#` comments (full-line and trailing) and blank lines; the rest is a
 * filename glob. `/`, `!` and `**` are rejected outright, so a bare filename
 * (`literal.md`) and a simple glob (`*.tmp`) are the only supported shapes —
 * deliberately, since anything richer would raise precedence questions a
 * per-folder policy file has no way to answer. */
export function parse(text: string): string[] {
  const patterns: string[] = [];
  for (let line of text.split("\n")) {
    const hash = line.indexOf("#");
    if (hash !== -1) line = line.slice(0, hash);
    line = line.replace(/[ \t\r]+$/, "");
    if (line.trim() === "") continue;
    if (/[/!]/.test(line) || line.includes("**")) {
      throw new Error(
        `${Filename} patterns must be bare filename globs (no '/', no '!', no '**'): "${line}"`,
      );
    }
    patterns.push(line);
  }
  return patterns;
}

/** Add pattern to folder's `.ingestignore`, creating the file if absent.
 *
 * Idempotent — a pattern already present isn't re-added, so a sweep run twice
 * doesn't double-list. comment, when non-empty, goes on the same line after
 * the `#`. Backs the sweep's `never` answer. */
export function append(folder: string, pattern: string, comment: string): void {
  const filePath = path.join(folder, Filename);

  let existing: string | null = null;
  try {
    existing = fs.readFileSync(filePath, "utf8");
  } catch (err) {
    if (!isENOENT(err)) throw err;
  }
  if (existing !== null) {
    const patterns = parse(existing);
    if (patterns.includes(pattern)) return;
  }

  let line = pattern;
  if (comment !== "") line += "  # " + comment;
  // The folder is deliberately not created: a raw file being withdrawn from
  // the sweep lives in it already, so a missing folder means a mistyped path,
  // and silently minting `raw/emials/.ingestignore` would bury that.
  const fd = fs.openSync(filePath, "a", 0o644);
  try {
    fs.writeSync(fd, line + "\n");
  } finally {
    fs.closeSync(fd);
  }
}

function isENOENT(err: unknown): boolean {
  return (err as NodeJS.ErrnoException).code === "ENOENT";
}
