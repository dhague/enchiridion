/**
 * sourcedate — the one owner of the source-date rule.
 *
 * A page's `source_date` is valid time and has exactly one canonical spelling,
 * YYYY-MM-DD (#192). That rule — which spellings count, and how a clock
 * truncates to its date — once lived in four private implementations that
 * drifted: `page set` accepted an invalid calendar date that ingest refused,
 * and the search index validated nothing at all (#309). This module is the
 * rule, implemented once.
 *
 * [parseSourceDate] is the single shared fact: parse any accepted spelling,
 * validate the calendar date (leap years, month/day ranges), and return the
 * canonical YYYY-MM-DD, or null when the value isn't a valid date. The two
 * postures on top of it — [canonicalSourceDate] refuses (throws on a
 * non-date), [truncateSourceDate] tolerates (passes the value through) — are
 * the thin choices the consumers make: the read path (pagerecord) tolerates
 * and stores verbatim (it renders its own fallback from [parseSourceDate],
 * not via [truncateSourceDate]), while the write paths (`page set`, ingest's
 * validation) refuse.
 */

/**
 * The accepted spellings a hand-written `source_date` might carry, in
 * precedence order: date-only first, then the timestamp forms the codebase
 * has emitted over its history (RFC3339 with or without a zone, and the
 * zone-less space/T-separated forms). Any clock is truncated to its date.
 * Returns the canonical YYYY-MM-DD, or null when value is not a valid date at
 * all (a free-text "summer 2026", a malformed scalar, a non-string/non-Date).
 */
export function parseSourceDate(value: unknown): string | null {
  if (value instanceof Date) {
    return formatDateOnly(
      value.getUTCFullYear(),
      value.getUTCMonth() + 1,
      value.getUTCDate(),
    );
  }
  if (typeof value !== "string") return null;
  const s = value.trim();
  const dateOnly = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (dateOnly) {
    const [y, mo, d] = [
      Number(dateOnly[1]),
      Number(dateOnly[2]),
      Number(dateOnly[3]),
    ];
    return validDate(y, mo, d) ? formatDateOnly(y, mo, d) : null;
  }
  const stamp = s.match(
    /^(\d{4})-(\d{2})-(\d{2})[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[Zz]|[+-]\d{2}:?\d{2})?$/,
  );
  if (stamp) {
    const [y, mo, d] = [Number(stamp[1]), Number(stamp[2]), Number(stamp[3])];
    return validDate(y, mo, d) ? formatDateOnly(y, mo, d) : null;
  }
  return null;
}

/**
 * The refuse posture: canonicalise a `source_date` to YYYY-MM-DD, truncating
 * a clock, and throw on a value that isn't a valid date at all. Null and
 * undefined read as absent and pass through. The `page set` write path uses
 * this so it rejects exactly the spellings ingest's validation rejects.
 */
export function canonicalSourceDate(value: unknown): string | null | undefined {
  if (value === null || value === undefined) return value;
  const date = parseSourceDate(value);
  if (date === null) {
    throw new Error(
      `source_date must be a valid date (YYYY-MM-DD), got ${String(value)}`,
    );
  }
  return date;
}

/**
 * The tolerate posture: canonicalise a `source_date` to YYYY-MM-DD when it's
 * a valid date, otherwise return the value unchanged. The read paths use this
 * so a legacy or hand-written non-date is stored verbatim rather than being
 * an error.
 */
export function truncateSourceDate(value: unknown): unknown {
  const date = parseSourceDate(value);
  return date !== null ? date : value;
}

/** Report whether y/m/d is a real calendar date. */
function validDate(y: number, mo: number, d: number): boolean {
  if (mo < 1 || mo > 12 || d < 1) return false;
  const leap = (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return d <= days[mo - 1];
}

function formatDateOnly(y: number, mo: number, d: number): string {
  return `${String(y).padStart(4, "0")}-${String(mo).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}
