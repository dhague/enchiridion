package cli

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The last three script CLIs ported in #186: `place`, the `page` get/set/merge
// trio, and the `vault` root/move pair. Each is a flag-for-flag port of
// `place.py`, `wikipage.py`, and `vault.py` respectively.

// --- place -------------------------------------------------------------------

func TestPlacePrintsKindFolderAndSlug(t *testing.T) {
	for _, tc := range []struct {
		kind, title, want string
	}{
		{"concept", "Connection Pooling", "wiki/concepts/connection-pooling.md"},
		{"entity", "Acme Corp", "wiki/entities/acme-corp.md"},
		{"source", "What's New", "wiki/sources/whats-new.md"},
		{"synthesis", "Why It Broke", "wiki/synthesis/why-it-broke.md"},
	} {
		out, err := runSubcommand(t, "place", tc.kind, tc.title)
		if err != nil {
			t.Fatalf("place %s %q: %v\n%s", tc.kind, tc.title, err, out)
		}
		if got := strings.TrimSpace(out); got != tc.want {
			t.Errorf("place %s %q = %q, want %q", tc.kind, tc.title, got, tc.want)
		}
	}
}

func TestPlaceRejectsUnknownKind(t *testing.T) {
	if _, err := runSubcommand(t, "place", "decision", "Some Title"); err == nil {
		t.Error("place with an unknown kind: want an error, got nil")
	}
}

func TestPlaceRequiresBothArguments(t *testing.T) {
	if _, err := runSubcommand(t, "place", "concept"); err == nil {
		t.Error("place with no title: want an error, got nil")
	}
}

// --- page --------------------------------------------------------------------

// pageFile writes a standalone markdown file (no vault) and returns its path —
// `page` resolves no vault root, operating only on the file handed to it.
func pageFile(t *testing.T, text string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "page.md")
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func readFile(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func TestPageGetPrintsAScalar(t *testing.T) {
	path := pageFile(t, "---\ntitle: Connection Pooling\nvolatility: stable\n---\nbody\n")

	out, err := runSubcommand(t, "page", "get", path, "title")
	if err != nil {
		t.Fatalf("page get: %v\n%s", err, out)
	}
	if got := strings.TrimSpace(out); got != "Connection Pooling" {
		t.Errorf("page get title = %q", got)
	}
}

func TestPageGetPrintsAListAsBracketRepr(t *testing.T) {
	path := pageFile(t, "---\ntitle: T\ntags:\n  - alpha\n  - beta\n---\nbody\n")

	out, err := runSubcommand(t, "page", "get", path, "tags")
	if err != nil {
		t.Fatalf("page get: %v\n%s", err, out)
	}
	if got := strings.TrimSpace(out); got != "['alpha', 'beta']" {
		t.Errorf("page get tags = %q, want bracket list repr", got)
	}
}

// A YAML timestamp decodes to a time.Time, so the default Go rendering
// would print "2026-01-15 00:00:00 +0000 UTC" where `page get created`
// prints "2026-01-15".
func TestPageGetPrintsABareDate(t *testing.T) {
	path := pageFile(t, "---\ntitle: T\ncreated: 2026-01-15\n---\nbody\n")

	out, err := runSubcommand(t, "page", "get", path, "created")
	if err != nil {
		t.Fatalf("page get: %v\n%s", err, out)
	}
	if got := strings.TrimSpace(out); got != "2026-01-15" {
		t.Errorf("page get created = %q, want %q", got, "2026-01-15")
	}
}

// A timestamp is not a date, so its time renders too — zone-less in the
// space-separated form, zoned in ISO form with the offset.
func TestPageGetKeepsATimestampsTime(t *testing.T) {
	for _, tc := range []struct{ scalar, want string }{
		{"2026-01-15 10:30:00", "2026-01-15 10:30:00"},
		{"2026-01-15T00:00:00+05:00", "2026-01-15T00:00:00+05:00"},
		{"2026-01-15T10:30:00-08:00", "2026-01-15T10:30:00-08:00"},
	} {
		path := pageFile(t, "---\ntitle: T\ncreated: "+tc.scalar+"\n---\nbody\n")

		out, err := runSubcommand(t, "page", "get", path, "created")
		if err != nil {
			t.Fatalf("page get %s: %v\n%s", tc.scalar, err, out)
		}
		if got := strings.TrimSpace(out); got != tc.want {
			t.Errorf("page get created (%s) = %q, want %q", tc.scalar, got, tc.want)
		}
	}
}

// `source_date` is the one field with a canonical spelling (#192): valid time,
// a date not an instant, so a clock on it truncates to YYYY-MM-DD on the way
// out. Other keys keep the rendering above.
func TestPageGetTruncatesSourceDateToItsDate(t *testing.T) {
	for _, tc := range []struct{ scalar, want string }{
		{"2026-01-15", "2026-01-15"},
		{"2026-01-15 10:30:00", "2026-01-15"},
		{"2026-01-15T10:30:00Z", "2026-01-15"},
		{"2026-01-15T10:30:00-08:00", "2026-01-15"},
	} {
		path := pageFile(t, "---\ntitle: T\nsource_date: "+tc.scalar+"\n---\nbody\n")

		out, err := runSubcommand(t, "page", "get", path, "source_date")
		if err != nil {
			t.Fatalf("page get source_date (%s): %v\n%s", tc.scalar, err, out)
		}
		if got := strings.TrimSpace(out); got != tc.want {
			t.Errorf("page get source_date (%s) = %q, want %q", tc.scalar, got, tc.want)
		}
	}
}

// A bool renders as `True`/`False`, not Go's `true`/`false`.
func TestPageGetPrintsABoolAsTrueFalse(t *testing.T) {
	path := pageFile(t, "---\ntitle: T\ndraft: true\npublished: false\n---\nbody\n")

	for key, want := range map[string]string{"draft": "True", "published": "False"} {
		out, err := runSubcommand(t, "page", "get", path, key)
		if err != nil {
			t.Fatalf("page get %s: %v\n%s", key, err, out)
		}
		if got := strings.TrimSpace(out); got != want {
			t.Errorf("page get %s = %q, want %q", key, got, want)
		}
	}
}

func TestPageGetFailsOnAbsentKey(t *testing.T) {
	path := pageFile(t, "---\ntitle: T\n---\nbody\n")

	out, err := runSubcommand(t, "page", "get", path, "summary")
	if err == nil {
		t.Error("page get on an absent key: want a non-zero exit, got nil")
	}
	if strings.TrimSpace(out) != "" && !strings.Contains(out, "summary") {
		t.Errorf("stdout should stay empty on an absent key, got %q", out)
	}
}

func TestPageSetWritesInPlace(t *testing.T) {
	path := pageFile(t, "---\ntitle: T\n---\nbody\n")

	if out, err := runSubcommand(t, "page", "set", path, "volatility", "stable"); err != nil {
		t.Fatalf("page set: %v\n%s", err, out)
	}
	text := readFile(t, path)
	if !strings.Contains(text, "volatility: stable") {
		t.Errorf("volatility not set:\n%s", text)
	}
	if !strings.HasSuffix(text, "body\n") {
		t.Errorf("body not preserved verbatim:\n%s", text)
	}
}

func TestPageSetJSONParsesTheValue(t *testing.T) {
	path := pageFile(t, "---\ntitle: T\n---\nbody\n")

	if out, err := runSubcommand(t, "page", "set", path, "tags", `["alpha","beta"]`, "--json"); err != nil {
		t.Fatalf("page set --json: %v\n%s", err, out)
	}

	out, err := runSubcommand(t, "page", "get", path, "tags")
	if err != nil {
		t.Fatalf("page get: %v\n%s", err, out)
	}
	if got := strings.TrimSpace(out); got != "['alpha', 'beta']" {
		t.Errorf("tags after --json set = %q", got)
	}
}

func TestPageSetWithoutJSONKeepsTheValueAString(t *testing.T) {
	path := pageFile(t, "---\ntitle: T\n---\nbody\n")

	if out, err := runSubcommand(t, "page", "set", path, "summary", "42"); err != nil {
		t.Fatalf("page set: %v\n%s", err, out)
	}
	if text := readFile(t, path); !strings.Contains(text, `summary: "42"`) &&
		!strings.Contains(text, "summary: '42'") {
		t.Errorf("a bare value should stay a string:\n%s", text)
	}
}

// The write half of #192: `page set source_date` writes the canonical
// YYYY-MM-DD spelling, truncating a clock — the value drifts into compliance
// on next touch, with no bulk rewrite.
func TestPageSetTruncatesSourceDateOnWrite(t *testing.T) {
	path := pageFile(t, "---\ntitle: T\n---\nbody\n")

	if out, err := runSubcommand(t, "page", "set", path, "source_date", "2026-07-20T14:30:00Z"); err != nil {
		t.Fatalf("page set source_date: %v\n%s", err, out)
	}
	text := readFile(t, path)
	// yaml.v3 double-quotes a date-looking scalar (ADR-0012 allows the quote
	// divergence), so match the value, not the quote style.
	if !strings.Contains(text, `source_date: "2026-07-20"`) && !strings.Contains(text, "source_date: 2026-07-20") {
		t.Errorf("source_date not truncated to its date:\n%s", text)
	}
	if strings.Contains(text, "14:30") {
		t.Errorf("source_date still carries its time:\n%s", text)
	}
}

// A `source_date` that isn't a valid date is refused on write — the same
// rejection ingest's validation applies, so no write path can store a value
// that `--since`/`--until` would silently mis-sort.
func TestPageSetRejectsANonDateSourceDate(t *testing.T) {
	path := pageFile(t, "---\ntitle: T\n---\nbody\n")

	out, err := runSubcommand(t, "page", "set", path, "source_date", "summer 2026")
	if err == nil {
		t.Fatalf("page set source_date 'summer 2026': want an error, got nil\n%s", out)
	}
	if !strings.Contains(err.Error(), "source_date must be a valid date") {
		t.Errorf("error = %q, want it to name the valid-date rule", err)
	}
}

// An explicit null passes through and reads back as absent, exactly as it
// does at ingest — it clears the field rather than tripping the date rule.
func TestPageSetJSONNullClearsSourceDate(t *testing.T) {
	path := pageFile(t, "---\ntitle: T\nsource_date: 2026-01-01\n---\nbody\n")

	if out, err := runSubcommand(t, "page", "set", path, "source_date", "null", "--json"); err != nil {
		t.Fatalf("page set source_date null: %v\n%s", err, out)
	}
	text := readFile(t, path)
	if strings.Contains(text, "2026-01-01") {
		t.Errorf("source_date should be cleared:\n%s", text)
	}
}

func TestPageMergeUnionsPreservingOrder(t *testing.T) {
	path := pageFile(t, "---\ntitle: T\ntags:\n  - alpha\n  - beta\n---\nbody\n")

	if out, err := runSubcommand(t, "page", "merge", path, "tags", `["beta","gamma"]`); err != nil {
		t.Fatalf("page merge: %v\n%s", err, out)
	}

	out, err := runSubcommand(t, "page", "get", path, "tags")
	if err != nil {
		t.Fatalf("page get: %v\n%s", err, out)
	}
	if got := strings.TrimSpace(out); got != "['alpha', 'beta', 'gamma']" {
		t.Errorf("tags after merge = %q, want order-preserving union", got)
	}
}

func TestPageMergeRejectsANonList(t *testing.T) {
	path := pageFile(t, "---\ntitle: T\n---\nbody\n")

	if _, err := runSubcommand(t, "page", "merge", path, "tags", `"alpha"`); err == nil {
		t.Error("page merge with a non-list value: want an error, got nil")
	}
}

func TestPageFailsOnAMissingFile(t *testing.T) {
	missing := filepath.Join(t.TempDir(), "nope.md")
	if _, err := runSubcommand(t, "page", "get", missing, "title"); err == nil {
		t.Error("page get on a missing file: want an error, got nil")
	}
}

// --- vault -------------------------------------------------------------------

// resolvedRoot is root with symlinks evaluated — what ResolveRoot prints, and
// what a macOS t.TempDir() under /var (a symlink to /private/var) needs.
func resolvedRoot(t *testing.T, root string) string {
	t.Helper()
	resolved, err := filepath.EvalSymlinks(root)
	if err != nil {
		t.Fatal(err)
	}
	return resolved
}

func TestVaultBareInvocationPrintsTheRoot(t *testing.T) {
	root := ingestVault(t, nil)

	out, err := runSubcommand(t, "vault")
	if err != nil {
		t.Fatalf("vault: %v\n%s", err, out)
	}
	if got, want := strings.TrimSpace(out), resolvedRoot(t, root); got != want {
		t.Errorf("vault = %q, want %q", got, want)
	}
}

func TestVaultRootPrintsTheRoot(t *testing.T) {
	root := ingestVault(t, nil)

	out, err := runSubcommand(t, "vault", "root")
	if err != nil {
		t.Fatalf("vault root: %v\n%s", err, out)
	}
	if got, want := strings.TrimSpace(out), resolvedRoot(t, root); got != want {
		t.Errorf("vault root = %q, want %q", got, want)
	}
}

func TestVaultMoveRewritesInboundAndOutboundLinks(t *testing.T) {
	root := ingestVault(t, map[string]string{
		"wiki/concepts/pooling.md": "---\ntitle: Pooling\n---\nSee [Acme](../entities/acme.md).\n",
		"wiki/entities/acme.md":    "---\ntitle: Acme\n---\nSee [Pooling](../concepts/pooling.md).\n",
	})

	out, err := runSubcommand(t, "vault", "move",
		"wiki/concepts/pooling.md", "wiki/concepts/connection-pooling.md")
	if err != nil {
		t.Fatalf("vault move: %v\n%s", err, out)
	}

	changed := strings.Fields(strings.TrimSpace(out))
	if len(changed) == 0 {
		t.Fatalf("vault move printed nothing; want the changed page refs")
	}
	for _, want := range []string{"wiki/concepts/connection-pooling.md", "wiki/entities/acme.md"} {
		if !strings.Contains(out, want) {
			t.Errorf("changed refs %q missing %q", out, want)
		}
	}

	if _, err := os.Stat(filepath.Join(root, "wiki", "concepts", "pooling.md")); !os.IsNotExist(err) {
		t.Error("the old page should be gone after a move")
	}
	moved := readFile(t, filepath.Join(root, "wiki", "concepts", "connection-pooling.md"))
	if !strings.Contains(moved, "../entities/acme.md") {
		t.Errorf("moved page's outbound link not preserved:\n%s", moved)
	}
	inbound := readFile(t, filepath.Join(root, "wiki", "entities", "acme.md"))
	if !strings.Contains(inbound, "../concepts/connection-pooling.md") {
		t.Errorf("inbound link not rewritten:\n%s", inbound)
	}
}

func TestVaultMoveRequiresBothRefs(t *testing.T) {
	ingestVault(t, nil)
	if _, err := runSubcommand(t, "vault", "move", "wiki/concepts/a.md"); err == nil {
		t.Error("vault move with one ref: want an error, got nil")
	}
}
