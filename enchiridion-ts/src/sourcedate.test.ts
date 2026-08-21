import { test } from "node:test";
import assert from "node:assert/strict";
import {
  parseSourceDate,
  canonicalSourceDate,
  truncateSourceDate,
} from "./sourcedate.js";

// The accepted spellings of a valid source date, each mapping to its canonical
// YYYY-MM-DD: date-only, the timestamp forms the codebase has emitted over its
// history (RFC3339 with or without a zone, the zone-less space/T-separated
// forms), leap years, and a Date object.
const valid: Array<[unknown, string]> = [
  ["2026-07-20", "2026-07-20"],
  ["2026-01-02", "2026-01-02"],
  [" 2026-07-20  ", "2026-07-20"],
  ["2026-07-20T14:30:00Z", "2026-07-20"],
  ["2026-07-20T14:30:00z", "2026-07-20"],
  ["2026-07-20T14:30:00+05:00", "2026-07-20"],
  ["2026-07-20T14:30:00+0530", "2026-07-20"],
  ["2026-07-20T14:30:00-05:00", "2026-07-20"],
  ["2026-07-20T14:30", "2026-07-20"],
  ["2026-07-20T14:30:00", "2026-07-20"],
  ["2026-07-20T14:30:00.123Z", "2026-07-20"],
  ["2026-07-20 10:30:00", "2026-07-20"],
  ["2026-07-20 10:30:00Z", "2026-07-20"],
  ["2026-07-20 10:30:00+05:00", "2026-07-20"],
  ["2024-02-29", "2024-02-29"],
  ["2026-02-28", "2026-02-28"],
  ["2000-02-29", "2000-02-29"],
  ["2024-12-31", "2024-12-31"],
  [new Date(Date.UTC(2026, 6, 20, 14, 30)), "2026-07-20"],
];

test("parseSourceDate canonicalises every accepted spelling", () => {
  for (const [value, want] of valid) {
    assert.equal(parseSourceDate(value), want, `for ${String(value)}`);
  }
});

// Invalid calendar dates (leap-year and month/day-range failures), malformed
// spellings, and non-dates — all "not a date".
const invalid: unknown[] = [
  "2026-13-40",
  "2026-13-01",
  "2026-02-30",
  "2026-04-31",
  "2026-06-31",
  "2025-02-29",
  "1900-02-29",
  "2026-00-10",
  "2026-01-00",
  "2026-7-20",
  "2026-07-2",
  "2026-07-20T14",
  "2026-07-20T14:30:00.",
  "20260720",
  "2026/07/20",
  "summer 2026",
  "not a date",
  "",
  "   ",
  null,
  undefined,
  20260720,
  2026,
  true,
  {},
  [],
];

test("parseSourceDate rejects invalid calendar dates, malformed spellings, and non-dates", () => {
  for (const value of invalid) {
    assert.equal(parseSourceDate(value), null, `for ${String(value)}`);
  }
});

test("canonicalSourceDate returns the canonical date and refuses a non-date", () => {
  assert.equal(canonicalSourceDate("2026-07-20"), "2026-07-20");
  assert.equal(canonicalSourceDate("2026-07-20T14:30:00Z"), "2026-07-20");
  assert.equal(canonicalSourceDate("2024-02-29"), "2024-02-29");
  assert.equal(canonicalSourceDate(null), null);
  assert.equal(canonicalSourceDate(undefined), undefined);
  for (const value of [
    "2026-13-40",
    "2026-02-30",
    "2025-02-29",
    "summer 2026",
    "",
    20260720,
  ]) {
    assert.throws(
      () => canonicalSourceDate(value),
      /source_date must be a valid date \(YYYY-MM-DD\)/,
      `for ${String(value)}`,
    );
  }
});

test("truncateSourceDate canonicalises a date and passes a non-date through", () => {
  assert.equal(truncateSourceDate("2026-07-20"), "2026-07-20");
  assert.equal(truncateSourceDate("2026-07-20T14:30:00Z"), "2026-07-20");
  const freeText = "summer 2026";
  assert.equal(truncateSourceDate(freeText), freeText);
  const bogusCalendar = "2026-02-30";
  assert.equal(truncateSourceDate(bogusCalendar), bogusCalendar);
  assert.equal(truncateSourceDate(null), null);
});
