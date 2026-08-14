package vault

// This file is the I/O half of the vault, ported from
// `wiki-plugin/scripts/vault.py` in #151: every read and write inside the
// vault, plus the cross-page operations ([Vault.MovePage],
// [Vault.RewriteInboundLinks]) that need every other page's text to fix the
// links pointing at a moved one. Its counterpart [wikipage.Page] is
// pure-functional and does no I/O at all.
//
// **The Python class's search-index facade is deliberately not ported.**
// `Vault.search`/`reindex`/`index_status` proxy to `search_index.py`, and
// `Vault.write` inline-updates the index — but in Go, `searchindex` imports
// this package, so the facade would be an import cycle. It costs nothing:
// the inline update was only ever a latency optimisation, since index
// correctness lives in the unconditional staleness scan every search runs
// (ADR-0006). Callers that need to search open a `searchindex.Index`
// directly, as `enchiridion search` does.

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"

	"github.com/dhague/enchiridion/enchiridion-go/internal/pagerecord"
	"github.com/dhague/enchiridion/enchiridion-go/internal/place"
	"github.com/dhague/enchiridion/enchiridion-go/internal/wikipage"
)

// Vault owns all vault I/O and cross-page operations over the pages at Root.
//
// Root is an absolute filesystem path; every page reference this type takes
// or returns is vault-relative with `/` separators (ADR-0009), so a ref can
// be handed straight from one method to another, or to an ingest plan.
type Vault struct {
	Root string
}

// New returns a Vault over the vault rooted at root.
//
// This runs no kind-folder migration — nothing does any more, the #114
// singular→plural migration script having been retired once every known
// vault was migrated. Callers that *write* pages must still ask
// [Vault.LegacyKindFolders] first — see there for why silence isn't an
// option.
func New(root string) *Vault { return &Vault{Root: root} }

// LegacyKindFolders returns any singular kind-folders left over from before
// ADR-0008, sorted — `wiki/concept/` where the vault should now hold
// `wiki/concepts/`.
//
// The migration script that used to fix these is gone (every known vault is
// migrated), but the check stays, because staying quiet is the one thing
// that would be genuinely bad: [place.Path] resolves canonical kinds from
// [place.KindFolders], so an unmigrated vault — one restored from an old
// backup, say — would take new pages into `wiki/concepts/` while the old
// ones sit in `wiki/concept/`, one vault silently split across two
// spellings of the same kind. A writer asks this and refuses instead; the
// remedy is now a `git mv` the error spells out.
func (v *Vault) LegacyKindFolders() ([]string, error) {
	entries, err := os.ReadDir(filepath.Join(v.Root, "wiki"))
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	var legacy []string
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		// A folder is legacy when it is the singular of a canonical kind but
		// not itself canonical: `concept` (→ `concepts`), never `synthesis`,
		// whose folder and kind are the same word.
		if _, canonical := place.FolderKinds[entry.Name()]; canonical {
			continue
		}
		if folder, isKind := place.KindFolders[entry.Name()]; isKind && folder != entry.Name() {
			legacy = append(legacy, entry.Name())
		}
	}
	sort.Strings(legacy)
	return legacy, nil
}

// Path is the absolute filesystem path for a vault-relative page ref.
//
// The one place a page ref crosses from ADR-0009's `/`-separated vault
// spelling into an OS path; every caller that needs to open, stat, or size a
// page goes through here rather than joining by hand.
func (v *Vault) Path(pageRef string) string {
	return filepath.Join(v.Root, filepath.FromSlash(pageRef))
}

// Load reads the page at pageRef (vault-relative) into a [wikipage.Page].
func (v *Vault) Load(pageRef string) (wikipage.Page, error) {
	text, err := os.ReadFile(v.Path(pageRef))
	if err != nil {
		return wikipage.Page{}, err
	}
	return wikipage.Page{Text: string(text)}, nil
}

// Exists reports whether pageRef names an existing *file* in the vault — a
// page that could be loaded. A directory sitting at that path is not a page,
// so this is false.
func (v *Vault) Exists(pageRef string) bool {
	info, err := os.Stat(v.Path(pageRef))
	return err == nil && !info.IsDir()
}

// Occupied reports whether anything at all sits at pageRef, directory
// included.
//
// The counterpart to [Vault.Exists], for the one question where a directory
// still counts: whether a create may claim this path. A slug colliding with a
// directory has to fail validation, not mid-write with a bare OS error after
// earlier pages are already on disk.
func (v *Vault) Occupied(pageRef string) bool {
	_, err := os.Stat(v.Path(pageRef))
	return err == nil
}

// Write writes page to pageRef (vault-relative), creating parent directories
// as needed.
func (v *Vault) Write(pageRef string, page wikipage.Page) error {
	path := v.Path(pageRef)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(page.Text), 0o644)
}

// DiscoveredKinds returns {kind: folder} for every subdirectory of `wiki/`
// that is not already a canonical kind-folder.
//
// This is the kind-list surface for callers (`wiki-ingest`, the ingest
// executor) that need to accept vault-specific kinds beyond the four
// canonical ones. The folder must pre-exist; the plugin never auto-creates
// custom kind-folders on its own.
func (v *Vault) DiscoveredKinds() (map[string]string, error) {
	entries, err := os.ReadDir(filepath.Join(v.Root, "wiki"))
	if err != nil {
		if os.IsNotExist(err) {
			return map[string]string{}, nil
		}
		return nil, err
	}
	out := map[string]string{}
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		if _, canonical := place.FolderKinds[entry.Name()]; canonical {
			continue
		}
		out[place.FolderToKind(entry.Name())] = entry.Name()
	}
	return out, nil
}

// LoadWikiPages returns every `wiki/**` page as a {pageRef: text} map. Never
// walks `raw/`.
func (v *Vault) LoadWikiPages() (map[string]string, error) {
	refs, err := PageRefs(v.Root)
	if err != nil {
		return nil, err
	}
	pages := make(map[string]string, len(refs))
	for _, ref := range refs {
		text, err := os.ReadFile(v.Path(ref))
		if err != nil {
			return nil, err
		}
		pages[ref] = string(text)
	}
	return pages, nil
}

// PageWithText pairs a decoded record with the page text it was decoded
// from, so a caller needing both doesn't re-read the file.
type PageWithText struct {
	Record pagerecord.PageRecord
	Text   string
}

// PagesWithText returns every `wiki/**` page as a {pageRef: record + text}
// map. Same pageRef convention as [Vault.Pages].
func (v *Vault) PagesWithText() (map[string]PageWithText, error) {
	pages, err := v.LoadWikiPages()
	if err != nil {
		return nil, err
	}
	records, err := pagerecord.LoadRecords(pages)
	if err != nil {
		return nil, err
	}
	out := make(map[string]PageWithText, len(records))
	for ref, record := range records {
		out[ref] = PageWithText{Record: record, Text: pages[ref]}
	}
	return out, nil
}

// Pages returns every `wiki/**` page as a {pageRef: record} map.
//
// pageRef is always vault-relative (e.g. `wiki/concepts/a.md`) — the one
// convention every Vault enumeration method uses. `raw/` is never walked.
func (v *Vault) Pages() (map[string]pagerecord.PageRecord, error) {
	pages, err := v.PagesWithText()
	if err != nil {
		return nil, err
	}
	out := make(map[string]pagerecord.PageRecord, len(pages))
	for ref, page := range pages {
		out[ref] = page.Record
	}
	return out, nil
}

// Set loads, [wikipage.Page.Set]s, and writes back the page at pageRef.
func (v *Vault) Set(pageRef, key string, value any) (wikipage.Page, error) {
	page, err := v.Load(pageRef)
	if err != nil {
		return wikipage.Page{}, err
	}
	if page, err = page.Set(key, value); err != nil {
		return wikipage.Page{}, err
	}
	return page, v.Write(pageRef, page)
}

// Merge loads, [wikipage.Page.Merge]s, and writes back the page at pageRef.
func (v *Vault) Merge(pageRef, key string, values []any) (wikipage.Page, error) {
	page, err := v.Load(pageRef)
	if err != nil {
		return wikipage.Page{}, err
	}
	if page, err = page.Merge(key, values); err != nil {
		return wikipage.Page{}, err
	}
	return page, v.Write(pageRef, page)
}

// writeChanged writes every page in planned whose text differs from before,
// returning the changed vault-relative paths, sorted.
func (v *Vault) writeChanged(planned, before map[string]string) ([]string, error) {
	var changed []string
	for pageRef, text := range planned {
		// A page absent from before is always written, even when the planned
		// text is empty — "unchanged" means the file already held this text,
		// not that the text is falsy.
		if prev, existed := before[pageRef]; existed && text == prev {
			continue
		}
		if err := v.Write(pageRef, wikipage.Page{Text: text}); err != nil {
			return nil, err
		}
		changed = append(changed, pageRef)
	}
	sort.Strings(changed)
	return changed, nil
}

// MovePage rewrites links across the vault's wiki pages and moves the page on
// disk.
//
// Reads every `wiki/**` page (never `raw/` — its files aren't rewritten by a
// page move), plans the move, writes back only the pages whose text changed,
// then removes the original. Returns the changed vault-relative paths,
// sorted; empty for oldRef == newRef.
func (v *Vault) MovePage(oldRef, newRef string) ([]string, error) {
	files, err := v.LoadWikiPages()
	if err != nil {
		return nil, err
	}
	if _, ok := files[oldRef]; !ok {
		return nil, fmt.Errorf("%s not found under %s", oldRef, v.Root)
	}

	// planned keys the moved page under newRef, so writing every changed page
	// also lays down the moved file (with its outbound links fixed) — all
	// that's left is to drop the original.
	changed, err := v.writeChanged(wikipage.PlanMove(files, oldRef, newRef), files)
	if err != nil {
		return nil, err
	}
	if v.Path(oldRef) != v.Path(newRef) {
		if err := os.Remove(v.Path(oldRef)); err != nil {
			return nil, err
		}
	}
	return changed, nil
}

// RewriteInboundLinks rewrites `wiki/**` pages' links pointing at oldRel to
// newRel.
//
// For a target that is not itself a wiki page — e.g. a `raw/` artifact
// renamed externally — oldRel/newRel are never read, parsed, or written; only
// *other* pages' inbound links are fixed. Returns the changed vault-relative
// paths, sorted.
func (v *Vault) RewriteInboundLinks(oldRel, newRel string) ([]string, error) {
	pages, err := v.LoadWikiPages()
	if err != nil {
		return nil, err
	}
	return v.writeChanged(wikipage.PlanMove(pages, oldRel, newRel), pages)
}
