// Package chainofevidence holds the page -> stub -> raw file chain every raw
// ingestion must leave. Ported from
// `wiki-plugin/scripts/chain_of_evidence.py`.
//
// **The rule** (stated here once; the ingest and commit packages only point
// at it): a raw file that produces pages at all must also produce a
// `wiki/sources/` stand-in for itself — a stub whose `raw_source` points back
// at the file — and every other page produced from it must carry a `source`
// edge back to that stub. So a reader can always walk from a claim to the
// artifact it came from.
//
// Two callers, one function, so the two checks cannot diverge: `ingest`
// validates a plan before any write (staged projected from the plan, merged
// with on-disk state for updates); `commit` is the hard gate (staged read
// straight from disk). Neither knows which one this is serving.
package chainofevidence

import (
	"fmt"
	gopath "path"
	"sort"

	"github.com/dhague/enchiridion/enchiridion-go/internal/place"
	"github.com/dhague/enchiridion/enchiridion-go/internal/wikipage"
)

// sourceDir is the `source` kind's folder — the one hardcoded folder string
// this package needs, kept in sync with place rather than duplicated.
var sourceDir = "wiki/" + place.KindFolders["source"]

// Check reports whether staged leaves a valid page -> stub -> raw chain.
//
// staged is every page one ingestion/commit touches, keyed by its
// (post-write) vault-relative path. Returns human-readable error strings,
// empty when the chain holds. Both loops iterate the staged refs in sorted
// order, so the result never depends on map order.
//
// A page whose frontmatter cannot be parsed is an error in its own right,
// returned rather than silently treated as edge-less.
func Check(staged map[string]wikipage.Page, raw string) ([]string, error) {
	raw = gopath.Clean(raw)
	refs := sortedRefs(staged)

	stubRef := ""
	for _, pageRef := range refs {
		if gopath.Dir(pageRef) != sourceDir {
			continue
		}
		link, err := staged[pageRef].GetString("raw_source")
		if err != nil {
			return nil, fmt.Errorf("%s: %w", pageRef, err)
		}
		if link == "" {
			continue
		}
		dest, ok := wikipage.LinkDest(link)
		if ok && wikipage.ResolveLinkDest(dest, gopath.Dir(pageRef)) == raw {
			stubRef = pageRef
			break
		}
	}

	if stubRef == "" {
		return []string{fmt.Sprintf(
			"%s needs a %s/ page whose raw_source points at it "+
				"— every ingested raw file gets a stand-in, even a thin stub",
			raw, place.KindFolders["source"])}, nil
	}

	var problems []string
	for _, pageRef := range refs {
		if pageRef == stubRef {
			continue
		}
		links, err := staged[pageRef].GetStringList("source")
		if err != nil {
			return nil, fmt.Errorf("%s: %w", pageRef, err)
		}
		pageDir := gopath.Dir(pageRef)
		found := false
		for _, link := range links {
			dest, ok := wikipage.LinkDest(link)
			if ok && wikipage.ResolveLinkDest(dest, pageDir) == stubRef {
				found = true
				break
			}
		}
		if !found {
			problems = append(problems,
				fmt.Sprintf("%s needs a source edge to the stub %s", pageRef, stubRef))
		}
	}
	return problems, nil
}

func sortedRefs(staged map[string]wikipage.Page) []string {
	refs := make([]string, 0, len(staged))
	for ref := range staged {
		refs = append(refs, ref)
	}
	sort.Strings(refs)
	return refs
}
