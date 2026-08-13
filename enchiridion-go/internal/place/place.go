// Package place computes a new page's vault-relative path: kind-folder plus
// kebab-slug of title. Ported from `wiki-plugin/scripts/place.py`.
//
// *Which* kind a page belongs to is judgment (wiki-conventions' placement
// algorithm) and stays with the ingesting agent. Turning a chosen kind +
// title into `wiki/<kind-folder>/<slug>.md` is mechanics, and lives here so
// filenames are consistent regardless of who — or which model — is ingesting.
// Kind *values* stay singular (`concept`); kind *folders* pluralize
// (`concepts/`), except `synthesis` — see [KindFolders] (ADR-0008).
package place

import (
	"fmt"
	"regexp"
	"strings"
)

// KindFolders maps a kind value to its `wiki/` folder name (ADR-0008:
// folders pluralize, values stay singular — `synthesis` has no distinct
// plural, so it's unchanged). The single source of truth for the mapping; no
// other package may hardcode a kind-folder string.
var KindFolders = map[string]string{
	"source":    "sources",
	"synthesis": "synthesis",
	"entity":    "entities",
	"concept":   "concepts",
}

// FolderKinds maps a folder name to its kind value, for readers going the
// other direction (pagerecord deriving a page's kind from its path).
var FolderKinds = func() map[string]string {
	out := make(map[string]string, len(KindFolders))
	for kind, folder := range KindFolders {
		out[folder] = kind
	}
	return out
}()

// Kinds is the fixed kind-value set (wiki-conventions, "Vault structure"),
// in the canonical order the CLI presents them.
var Kinds = []string{"concept", "entity", "source", "synthesis"}

// MaxSlugLength caps generated kebab-slug filenames — readability, plus
// headroom under the Windows 255-char path limit (#70).
const MaxSlugLength = 64

// minWordCut is the shortest prefix worth keeping when truncating at a
// hyphen boundary; below it, a hard cut reads better.
const minWordCut = 8

var (
	apostrophe = regexp.MustCompile(`['’]`)
	nonAlnum   = regexp.MustCompile(`[^a-z0-9]+`)
)

// truncateSlug truncates slug to maxLength at the last hyphen boundary, when
// that leaves at least minWordCut chars. Otherwise a hard cut.
func truncateSlug(slug string, maxLength int) string {
	if len(slug) <= maxLength {
		return slug
	}
	if cut := strings.LastIndex(slug[:maxLength], "-"); cut >= minWordCut {
		return strings.TrimRight(slug[:cut], "-")
	}
	return strings.TrimRight(slug[:maxLength], "-")
}

// Slugify returns title as a lowercase kebab-slug. Apostrophes are dropped
// rather than hyphenated ("What's" -> "whats", not "what-s"); every other run
// of non-alphanumerics collapses to one hyphen; ends are stripped.
// maxLength, when positive, truncates via truncateSlug.
func Slugify(title string, maxLength int) string {
	slug := apostrophe.ReplaceAllString(strings.ToLower(title), "")
	slug = nonAlnum.ReplaceAllString(slug, "-")
	slug = strings.Trim(slug, "-")
	if maxLength > 0 {
		slug = truncateSlug(slug, maxLength)
	}
	return slug
}

// FolderToKind derives a kind value from a folder name using the ADR-0008
// rule: strip a trailing `s` if present (`decisions` → `decision`);
// otherwise return the folder name verbatim (`people` → `people`).
//
// Intended for custom kind-folders not already in [FolderKinds] — canonical
// folders should be looked up there directly.
func FolderToKind(folder string) string {
	return strings.TrimSuffix(folder, "s")
}

// Path returns the vault-relative path for a new page of kind titled title.
//
// Canonical kinds are resolved from [KindFolders]. Custom (discovered) kinds
// are resolved from extraKindFolders (a {kind: folder} map). Returns an error
// when kind is unknown in both.
func Path(kind, title string, extraKindFolders map[string]string) (string, error) {
	folder, ok := KindFolders[kind]
	if !ok {
		folder, ok = extraKindFolders[kind]
	}
	if !ok {
		return "", fmt.Errorf("unknown kind %q; must be one of %v", kind, Kinds)
	}
	return fmt.Sprintf("wiki/%s/%s.md", folder, Slugify(title, MaxSlugLength)), nil
}
