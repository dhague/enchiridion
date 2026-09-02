/**
 * Filesystem utilities shared across the enchiridion script layer.
 */
import fs from "node:fs";

/**
 * Like `fs.mkdirSync(path, { recursive: true })` but tolerates EEXIST even
 * when the target already exists as a directory — which Windows + OneDrive
 * triggers on directories carrying the ReparsePoint attribute (cloud-first
 * sync stubs). Re-throws EEXIST only when the path is a plain file.
 *
 * On Linux/macOS `recursive: true` never throws on existing directories, so
 * this is a no-op on those platforms.
 */
export function mkdirSafe(dir: string, mode?: number): void {
  try {
    fs.mkdirSync(dir, { recursive: true, ...(mode !== undefined && { mode }) });
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code !== "EEXIST") throw err;
    let stat: fs.Stats;
    try {
      stat = fs.statSync(dir);
    } catch {
      throw err;
    }
    if (!stat.isDirectory()) {
      throw new Error(
        `${dir} exists as a file, not a directory — delete it so it can be created as a directory`,
      );
    }
    // directory already exists — treat as success (Windows+OneDrive ReparsePoint)
  }
}
