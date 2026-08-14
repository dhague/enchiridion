// Package discover is single-call discovery for ingestion: overlap candidates
// plus the tag vocabulary, driven off a draft IngestPlan. Ported from
// `wiki-plugin/scripts/discover.py`.
//
// It fronts [searchindex.Index] with hint classification, so wiki-ingest's
// duplicate-detection step — the one where a miss creates a duplicate page —
// is deterministic mechanics rather than an agent re-deriving "too similar"
// from prose each run. Each candidate carries back everything the agent would
// otherwise open the page to read (summary, tags, volatility, supersession),
// so the cite / edge-type / tag-reuse steps collapse into one call instead of
// N page reads.
//
// **The query is OR of the candidate's own words** (Raw=true), never the
// default AND-across-terms. An AND query built from a whole
// title+summary+body demands every one of those words be present in the
// candidate page — so the planned page's necessarily-novel summary and body
// text silently zero out real duplicates that title alone would have found.
// Precision comes instead from BM25's IDF weighting, where common words
// contribute almost nothing to the score.
package discover

import (
	"regexp"
	"strings"

	"github.com/dhague/enchiridion/enchiridion-go/internal/ingest"
	"github.com/dhague/enchiridion/enchiridion-go/internal/searchindex"
)

// Hint is the relationship a discovery hit has to the planned page.
type Hint string

// The four hint classifications a candidate can carry.
const (
	HintDuplicate Hint = "duplicate"
	HintRefines   Hint = "refines"
	HintRelated   Hint = "related"
	HintDistinct  Hint = "distinct"
)

// Thresholds calibrated against the dogfooding vault (#63). They are
// parameters on [Check] rather than hard-coded, so the eval harness can tune
// them without editing this package.
const (
	// DuplicateThreshold is the score at or above which a hit sharing a title
	// token is a duplicate.
	DuplicateThreshold = 15.0
	// RelatedThreshold is the score at or above which a hit is related.
	RelatedThreshold = 5.0
	// DefaultLimit is deliberately generous: a narrow cap hides exactly the
	// near-duplicates this call exists to surface.
	DefaultLimit = 200
)

// wordRE matches the tokens that build a query and are compared for shared
// title tokens — `[a-z0-9]+` over lowercased text, exactly as discover.py.
var wordRE = regexp.MustCompile(`[a-z0-9]+`)

// Searcher is the search surface [Check] and [Discover] need, named as an
// interface so tests can drive classification against a fake rather than a
// real index. [searchindex.Index] implements it.
type Searcher interface {
	Search(q searchindex.Query) ([]searchindex.Hit, error)
}

// Candidate is one overlapping page, classified by relationship to the
// planned page.
type Candidate struct {
	PageRef      string   `json:"page_ref"`
	Title        string   `json:"title"`
	Score        float64  `json:"score"`
	Hint         Hint     `json:"hint"`
	Summary      string   `json:"summary"`
	Tags         []string `json:"tags"`
	Volatility   string   `json:"volatility"`
	SupersededBy *string  `json:"superseded_by"`
}

// Options tunes [Check] and [Discover].
type Options struct {
	Limit              int
	DuplicateThreshold float64
	RelatedThreshold   float64
}

// withDefaults fills the calibrated defaults for zero-valued options.
func (o Options) withDefaults() Options {
	if o.Limit <= 0 {
		o.Limit = DefaultLimit
	}
	if o.DuplicateThreshold == 0 {
		o.DuplicateThreshold = DuplicateThreshold
	}
	if o.RelatedThreshold == 0 {
		o.RelatedThreshold = RelatedThreshold
	}
	return o
}

// titleTokens returns the set of `[a-z0-9]+` tokens in a lowercased title.
func titleTokens(title string) map[string]bool {
	tokens := map[string]bool{}
	for _, word := range wordRE.FindAllString(strings.ToLower(title), -1) {
		tokens[word] = true
	}
	return tokens
}

// OrQuery returns the unique words across texts, phrase-quoted and OR-joined —
// an FTS5 Raw expression. OR, not AND; see the package comment.
func OrQuery(texts ...string) string {
	seen := map[string]bool{}
	words := []string{}
	for _, text := range texts {
		for _, word := range wordRE.FindAllString(strings.ToLower(text), -1) {
			if !seen[word] {
				seen[word] = true
				words = append(words, word)
			}
		}
	}
	quoted := make([]string, 0, len(words))
	for _, word := range words {
		quoted = append(quoted, `"`+word+`"`)
	}
	return strings.Join(quoted, " OR ")
}

// classify maps a score plus whether the hit shares a title token to its
// hint.
func classify(score float64, sharesTitleToken bool, duplicateThreshold, relatedThreshold float64) Hint {
	if score >= duplicateThreshold {
		if sharesTitleToken {
			return HintDuplicate
		}
		return HintRefines
	}
	if score >= relatedThreshold {
		return HintRelated
	}
	return HintDistinct
}

// Check searches for pages overlapping a planned page, classifying each hit's
// relationship to it. title/summary/body must be the planned page's own
// drafted text.
//
// The query is built from exactly what the candidate page says — never a
// paraphrase — which is why retrieval's vocabulary-mismatch problem doesn't
// bite here.
//
// The searcher is passed in, never opened here: [searchindex.Index] is
// one-per-vault-at-a-time (ADR-0010), and that is enforced by the command
// owning the only handle rather than by this package guessing whether one is
// already live.
func Check(searcher Searcher, title, summary, body string, opts Options) ([]Candidate, error) {
	opts = opts.withDefaults()
	query := OrQuery(title, summary, body)
	if query == "" {
		return []Candidate{}, nil
	}

	hits, err := searcher.Search(searchindex.Query{
		Text:  query,
		Raw:   true,
		Limit: opts.Limit,
	})
	if err != nil {
		return nil, err
	}

	planned := titleTokens(title)
	candidates := make([]Candidate, 0, len(hits))
	for _, hit := range hits {
		shares := false
		for token := range titleTokens(hit.Title) {
			if planned[token] {
				shares = true
				break
			}
		}
		candidates = append(candidates, Candidate{
			PageRef:      hit.PageRef,
			Title:        hit.Title,
			Score:        hit.Score,
			Hint:         classify(hit.Score, shares, opts.DuplicateThreshold, opts.RelatedThreshold),
			Summary:      hit.Summary,
			Tags:         hit.Tags,
			Volatility:   hit.Volatility,
			SupersededBy: hit.SupersededBy,
		})
	}
	return candidates, nil
}

// PageResult pairs one planned page's title with its discovered candidates.
type PageResult struct {
	Title      string
	Candidates []Candidate
}

// summary returns a plan page's frontmatter summary as a string, "" when
// absent — the same `frontmatter.get("summary", "")` discover.py reads.
func pageSummary(page ingest.PagePlan) string {
	value, ok := page.Frontmatter.Get("summary")
	if !ok {
		return ""
	}
	s, _ := value.(string)
	return s
}

// pageBody returns a plan page's body, "" when nil — the same `page.body or
// ""` discover.py reads.
func pageBody(page ingest.PagePlan) string {
	if page.Body == nil {
		return ""
	}
	return *page.Body
}

// Discover runs [Check] for every page a draft plan proposes — one call
// however many chunks the plan carries, against the one searcher the caller
// owns, per ADR-0010.
func Discover(searcher Searcher, pages []ingest.PagePlan, opts Options) ([]PageResult, error) {
	results := make([]PageResult, 0, len(pages))
	for _, page := range pages {
		candidates, err := Check(searcher, page.Title, pageSummary(page), pageBody(page), opts)
		if err != nil {
			return nil, err
		}
		results = append(results, PageResult{Title: page.Title, Candidates: candidates})
	}
	return results, nil
}

// TagsContaining returns the vocabulary tags whose name contains any of
// substrings, case-insensitive OR match. Order follows vocabulary
// (most-used first).
func TagsContaining(vocabulary []searchindex.TagCount, substrings []string) []string {
	needles := make([]string, 0, len(substrings))
	for _, s := range substrings {
		needles = append(needles, strings.ToLower(s))
	}
	var out []string
	for _, tc := range vocabulary {
		name := strings.ToLower(tc.Tag)
		for _, needle := range needles {
			if strings.Contains(name, needle) {
				out = append(out, tc.Tag)
				break
			}
		}
	}
	return out
}

// TagCounts returns the exact-match page count per requested tag, 0 if absent
// (safe to mint). Order follows the requested tags.
func TagCounts(vocabulary []searchindex.TagCount, tags []string) []searchindex.TagCount {
	counts := map[string]int{}
	for _, tc := range vocabulary {
		counts[tc.Tag] = tc.Count
	}
	out := make([]searchindex.TagCount, 0, len(tags))
	for _, tag := range tags {
		out = append(out, searchindex.TagCount{Tag: tag, Count: counts[tag]})
	}
	return out
}
