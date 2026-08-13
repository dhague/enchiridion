// Package supersededby resolves a candidate set's supersession chains to
// their current heads. Ported from `wiki-plugin/scripts/superseded_by.py`.
//
// [pagerecord.LoadRecords] already inverts every page's `supersedes` edge
// into `superseded_by`; this package is the retrieval-facing entrypoint into
// that derivation. It takes the retrieval frontier's candidate set of page
// refs and returns, for each, its *active* page: the same ref if it's
// current, or the page at the end of its supersession chain otherwise. A
// chain head is returned even when it falls outside the given candidate set —
// `supersedes` is a recorded fact, so the head is surfaced rather than left
// for the reader to notice a bare page ref is stale.
package supersededby

import "github.com/dhague/enchiridion/enchiridion-go/internal/pagerecord"

// Resolution is one seed's supersession chain, walked to its current head.
//
// Chain lists the intermediate/final pages between Seed and Active
// (excluding Seed, ending with Active); empty when Seed is already current.
type Resolution struct {
	Seed   string   `json:"seed"`
	Active string   `json:"active"`
	Chain  []string `json:"chain"`
}

// Resolve walks each seed's superseded_by pointers to its current head.
//
// A page missing from records (outside the vault) resolves to itself with an
// empty chain. When a page's superseded_by lists more than one successor, the
// first is followed — the same first-write-wins convention the search index
// uses, since the schema doesn't model forked supersession.
func Resolve(seeds []string, records map[string]pagerecord.PageRecord) []Resolution {
	resolutions := make([]Resolution, 0, len(seeds))
	for _, seed := range seeds {
		chain := []string{}
		current := seed
		seen := map[string]bool{current: true}
		for {
			rec, ok := records[current]
			successors := []string{}
			if ok {
				successors = rec.SupersededBy
			}
			if len(successors) == 0 {
				break
			}
			next := successors[0]
			if seen[next] {
				break // a supersedes cycle would spin forever otherwise
			}
			chain = append(chain, next)
			seen[next] = true
			current = next
		}
		resolutions = append(resolutions, Resolution{Seed: seed, Active: current, Chain: chain})
	}
	return resolutions
}
