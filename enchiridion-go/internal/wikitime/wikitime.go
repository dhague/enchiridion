// Package wikitime is the one parse/render pair for the frontmatter's
// valid-time fields (#192). One canonical spelling — `YYYY-MM-DD` — produced
// by one function, used by every caller that reads or writes `source_date`.
//
// The frontmatter schema says `source_date` is valid time: when the knowledge
// is *from* — a date, not an instant. A time-of-day on it has no domain
// meaning, so a scalar carrying a clock is truncated to its date on the way
// in. Genuine instants keep RFC3339 and are deliberately out of scope here:
// `git_date` is derived already-canonical, and the `YYYY-MM-DD-hhmm-` raw-file
// prefix is a filename, not a field.
package wikitime

import (
	"fmt"
	"time"
)

// layouts are the non-canonical spellings a hand-written `source_date` might
// already carry, in precedence order. Date-only first, then the timestamp
// forms the codebase has emitted over its history: RFC3339 (with or without a
// fractional second) and the zone-less space/T-separated forms.
var layouts = []string{
	time.DateOnly,
	time.RFC3339,
	time.RFC3339Nano,
	"2006-01-02 15:04:05",
	"2006-01-02T15:04:05",
}

// ParseDate renders a frontmatter scalar for a valid-time field — a
// YAML-decoded time.Time, a plain string, or anything else — in the canonical
// YYYY-MM-DD spelling, truncating any clock it carries.
//
// ok is false when value is not a valid date at all (a free-text "summer
// 2026", a malformed scalar). Read paths tolerate that and store the value
// verbatim; the write paths (ingest, `page set`) reject it.
func ParseDate(value any) (date string, ok bool) {
	switch v := value.(type) {
	case time.Time:
		return v.Format(time.DateOnly), true
	case string:
		t, err := parseString(v)
		if err != nil {
			return v, false
		}
		return t.Format(time.DateOnly), true
	default:
		return "", false
	}
}

// parseString parses a date or datetime string into a time.Time, or an error
// when it is not a date in any accepted spelling.
func parseString(s string) (time.Time, error) {
	if s == "" {
		return time.Time{}, fmt.Errorf("empty date")
	}
	for _, layout := range layouts {
		if t, err := time.Parse(layout, s); err == nil {
			return t, nil
		}
	}
	return time.Time{}, fmt.Errorf("not a date: %q", s)
}
