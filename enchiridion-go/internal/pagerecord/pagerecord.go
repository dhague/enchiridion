// Package pagerecord is the one package that reads the frontmatter schema.
// Ported from `wiki-plugin/scripts/page_record.py`.
//
// Frontmatter text in, one typed record out. Every caller that needs a page's
// frontmatter goes through here rather than re-parsing keys, so the schema
// changes in exactly one place.
//
// Every path this package touches is vault-relative — a page reference
// (`wiki/concepts/a.md`), ADR-0009. Kind is derived from the page's folder
// via [place.FolderToKind] (ADR-0008 singularization rule): canonical folders
// resolve from [place.FolderKinds]; custom folders are singularized and used
// verbatim. Edges recovers each of [EdgeKeys]' targets, resolved from the
// page's own directory to true vault-relative by construction; SupersededBy
// is derived by inverting every other page's `supersedes` edge, never read
// from frontmatter.
package pagerecord

import (
	"fmt"
	"path"
	"time"

	"gopkg.in/yaml.v3"

	"github.com/dhague/enchiridion/enchiridion-go/internal/place"
	"github.com/dhague/enchiridion/enchiridion-go/internal/wikipage"
)

// EdgeKeys lists the frontmatter keys that hold markdown links to other
// pages. Order mirrors the frontmatter schema block in the conventions spec.
// `raw_source` holds a single link; every other key holds a list.
var EdgeKeys = []string{
	"raw_source",
	"supersedes",
	"refines",
	"contradicts",
	"example-of",
	"source",
	"related",
}

// singleLinkKeys are the [EdgeKeys] whose YAML value is one scalar link
// rather than a list of them.
var singleLinkKeys = map[string]bool{"raw_source": true}

// Edge is one frontmatter edge key with its resolved, vault-relative targets.
type Edge struct {
	Key     string
	Targets []string
}

// PageRecord is one page's frontmatter, decoded to plain values.
type PageRecord struct {
	PageRef      string
	Kind         string
	Title        string
	Summary      string
	Tags         []string
	SourceDate   string
	Volatility   string
	Edges        []Edge
	SupersededBy []string
}

// Supersedes returns the targets of this record's `supersedes` edge, or nil.
func (r PageRecord) Supersedes() []string {
	for _, e := range r.Edges {
		if e.Key == "supersedes" {
			return e.Targets
		}
	}
	return nil
}

func linkTarget(markdownLink, pageDir string) (string, error) {
	dest, ok := wikipage.LinkDest(markdownLink)
	if !ok {
		return "", fmt.Errorf("not a markdown link: %q", markdownLink)
	}
	return wikipage.ResolveLinkDest(dest, pageDir), nil
}

// New decodes one page's frontmatter. SupersededBy is always empty here — it
// needs every other page, so only [LoadRecords] fills it in.
func New(pageRef, text string) (PageRecord, error) {
	// The kind-folder is the directory directly under `wiki/` that holds
	// this page (`wiki/concepts/a.md` → folder `concepts`). A page not at
	// that exact depth (e.g. `wiki/foo.md` or `wiki/concepts/nested/deep.md`)
	// is a structural error.
	pageDir := path.Dir(pageRef)
	if pageDir == "." {
		pageDir = ""
	}
	folder := path.Base(pageDir)
	if path.Dir(pageDir) != "wiki" {
		return PageRecord{}, fmt.Errorf("%q: not directly under a wiki kind-folder", pageRef)
	}
	kind, ok := place.FolderKinds[folder]
	if !ok {
		kind = place.FolderToKind(folder)
	}

	data, err := frontmatterMap(text)
	if err != nil {
		return PageRecord{}, fmt.Errorf("%s: %w", pageRef, err)
	}

	var edges []Edge
	for _, key := range EdgeKeys {
		raw, present := data[key]
		if !present || raw == nil {
			continue
		}
		var links []string
		if singleLinkKeys[key] {
			s, ok := raw.(string)
			if !ok || s == "" {
				continue
			}
			links = []string{s}
		} else {
			items, ok := raw.([]any)
			if !ok || len(items) == 0 {
				continue
			}
			for _, item := range items {
				s, ok := item.(string)
				if !ok {
					return PageRecord{}, fmt.Errorf("%s: %s entry is not a markdown link: %v", pageRef, key, item)
				}
				links = append(links, s)
			}
		}
		targets := make([]string, 0, len(links))
		for _, link := range links {
			target, err := linkTarget(link, pageDir)
			if err != nil {
				return PageRecord{}, fmt.Errorf("%s: %s: %w", pageRef, key, err)
			}
			targets = append(targets, target)
		}
		edges = append(edges, Edge{Key: key, Targets: targets})
	}

	return PageRecord{
		PageRef:    pageRef,
		Kind:       kind,
		Title:      scalar(data["title"]),
		Summary:    scalar(data["summary"]),
		Tags:       stringList(data["tags"]),
		SourceDate: scalar(data["source_date"]),
		Volatility: scalar(data["volatility"]),
		Edges:      edges,
	}, nil
}

// frontmatterMap parses a page's YAML frontmatter into a plain map. A page
// with no frontmatter, or with an empty block, decodes to an empty map
// rather than an error — a body-only file is indexable, just featureless.
func frontmatterMap(text string) (map[string]any, error) {
	fm, _, _, ok := wikipage.SplitFrontmatter(text)
	if !ok || fm == "" {
		return map[string]any{}, nil
	}
	var data map[string]any
	if err := yaml.Unmarshal([]byte(fm), &data); err != nil {
		return nil, fmt.Errorf("invalid frontmatter YAML: %w", err)
	}
	if data == nil {
		return map[string]any{}, nil
	}
	return data, nil
}

// scalar renders a frontmatter value as a string, mirroring Python's
// `str(data.get(key, ""))` — a missing key and an explicit null both give "".
//
// An unquoted `source_date: 2026-07-20` is resolved to a timestamp by both
// YAML implementations; Python's `str(date)` spells it back as the ISO date,
// so the date case is spelled out here rather than left to Go's default
// time formatting, which would put a non-comparable string in the index.
func scalar(v any) string {
	switch v := v.(type) {
	case nil:
		return ""
	case string:
		return v
	case time.Time:
		if v.Equal(v.Truncate(24 * time.Hour)) {
			return v.Format(time.DateOnly)
		}
		return v.Format(time.RFC3339)
	default:
		return fmt.Sprint(v)
	}
}

func stringList(v any) []string {
	items, ok := v.([]any)
	if !ok {
		return nil
	}
	out := make([]string, 0, len(items))
	for _, item := range items {
		out = append(out, scalar(item))
	}
	return out
}

// LoadRecords decodes every page in pages ({pageRef: text}, keys
// vault-relative), filling in SupersededBy by inverting the `supersedes`
// edges.
//
// Pages in any `wiki/<folder>/` are decoded and included; custom kind-folders
// are fully supported via [place.FolderToKind]. Pages at the wrong depth (not
// directly under a kind-folder) are an error.
func LoadRecords(pages map[string]string) (map[string]PageRecord, error) {
	records := make(map[string]PageRecord, len(pages))
	for pageRef, text := range pages {
		rec, err := New(pageRef, text)
		if err != nil {
			return nil, err
		}
		records[pageRef] = rec
	}

	supersededBy := map[string][]string{}
	for pageRef, rec := range records {
		for _, target := range rec.Supersedes() {
			supersededBy[target] = append(supersededBy[target], pageRef)
		}
	}
	for pageRef, targets := range supersededBy {
		if rec, ok := records[pageRef]; ok {
			rec.SupersededBy = targets
			records[pageRef] = rec
		}
	}
	return records, nil
}
