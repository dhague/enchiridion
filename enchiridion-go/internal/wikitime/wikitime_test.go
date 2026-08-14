package wikitime

import (
	"testing"
	"time"
)

func TestParseDateCanonicalisesEveryHistoricalSpelling(t *testing.T) {
	tests := []struct {
		name  string
		value any
		want  string
		ok    bool
	}{
		// A YAML-decoded timestamp — the unquoted `source_date: 2026-07-20`
		// case. Date-only, zone-less datetime, and zoned datetime all decode
		// to a time.Time and truncate to the date.
		{"bare date", time.Date(2026, 7, 20, 0, 0, 0, 0, time.UTC), "2026-07-20", true},
		{"zone-less datetime", time.Date(2026, 7, 20, 14, 30, 0, 0, time.UTC), "2026-07-20", true},
		{"zoned datetime", time.Date(2026, 7, 20, 14, 30, 0, 0, time.FixedZone("", 5*3600)), "2026-07-20", true},
		// Quoted scalars stay strings; each spelling parses and truncates.
		{"string date", "2026-07-20", "2026-07-20", true},
		{"string rfc3339", "2026-07-20T14:30:00Z", "2026-07-20", true},
		{"string rfc3339 offset", "2026-07-20T14:30:00-08:00", "2026-07-20", true},
		{"string rfc3339 nano", "2026-07-20T14:30:00.5Z", "2026-07-20", true},
		{"string space form", "2026-07-20 10:30:00", "2026-07-20", true},
		{"string zone-less T form", "2026-07-20T10:30:00", "2026-07-20", true},
		// Not dates at all — ok is false, and a string keeps its spelling.
		{"free text", "summer 2026", "summer 2026", false},
		{"malformed scalar", "2026-07-99", "2026-07-99", false},
		{"empty string", "", "", false},
		{"non-string scalar", 20260720, "", false},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, ok := ParseDate(tc.value)
			if ok != tc.ok || got != tc.want {
				t.Errorf("ParseDate(%v) = (%q, %v), want (%q, %v)",
					tc.value, got, ok, tc.want, tc.ok)
			}
		})
	}
}
