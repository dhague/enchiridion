// Package ingest holds the IngestPlan schema and its single-call executor.
// Plan in, commit SHA out. Ported from `wiki-plugin/scripts/ingest.py`.
//
// An [Plan] is the decided outcome of an ingestion: which pages to
// create/update, with what frontmatter and typed edges. Semantic chunking and
// overlap classification are judgment and stay with the ingesting agent;
// everything downstream of that decision is mechanics and lives here.
//
// **A plan names link targets by vault-relative page reference only** —
// `edges` and `supersedes` hold paths like `wiki/concepts/foo.md`, never
// composed `[Title](../dest.md)` strings. Composing the link (title lookup,
// `../` relativisation, percent-encoding, YAML quoting) is this package's
// job. `raw_source` uses a boolean sentinel for the same reason:
// `frontmatter: {"raw_source": true}` marks the page as the stub for
// [Plan.Raw], and the link is composed from that. Body links are re-encoded
// on write by [wikipage.NormalizeBodyLinks].
//
// Pipeline: [Resolve] -> [Resolved.Validate] -> [Resolved.Execute] -> derive
// a [commit.Manifest] -> commit.
//
// [Resolve] is the single place placement ([place.Path]), frontmatter
// projection and edge/`raw_source` link composition happen: it turns a plan
// into the exact (pageRef, page) pairs the vault will end up holding.
// Validation then reads only resolved facts, and execution writes only
// resolved pages — so the plan that was checked and the plan that gets
// written cannot diverge. Resolve is pure apart from vault reads.
//
// Validation runs entirely before any write, shape (required fields, valid
// op) then semantic (an update's pageRef exists, a create's target doesn't
// yet, every edge target resolves to a page already on disk *or* created by
// this same plan, and [chainofevidence.Check] holds). That last check is a
// courtesy to the agent — [commit.Commit] re-runs it as the hard gate, so a
// hand-built manifest can't route around validation into history.
//
// Ingestion isn't the only caller: wiki-retrieval's confirmed synthesis-page
// save is the same shape (one `create` of kind `synthesis`, `source` edges,
// no raw artifact) and passes `action: "synthesize"` so the history
// distinguishes the two without reading the diff.
//
// [Plan.Raw] is never renamed or moved — a file with external identity keeps
// its name forever. Ingestion reads it and stages it; `raw_source` links
// point at it where it sits, percent-encoded by the link machinery rather
// than sanitized on disk.
//
// **No rollback on failure, deliberately.** A page written before a later
// step fails stays on disk, uncommitted. Every write here is idempotent, so
// re-running the plan after fixing the cause is always safe.
package ingest

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
)

// PagePlan is one page this plan creates or updates.
type PagePlan struct {
	Op      string `json:"op"`
	Title   string `json:"title"`
	Kind    string `json:"kind"`
	PageRef string `json:"page_ref"`
	// Body is nil when the plan leaves the existing body alone; an update
	// omitting it keeps what's on disk, whereas `"body": ""` blanks it. That
	// distinction is why this is a pointer and not a string.
	Body *string `json:"body"`
	// Frontmatter is the projected frontmatter, in plan order — see
	// [OrderedMap] for why order is preserved.
	Frontmatter OrderedMap[any] `json:"frontmatter"`
	// Edges maps each typed-edge key to its targets, named by vault-relative
	// page reference only.
	Edges OrderedMap[[]string] `json:"edges"`
}

// Plan is the deterministic description of one ingestion's decided outcome.
type Plan struct {
	Title string `json:"title"`
	// Action is the structured commit's verb ([commit.Manifest].Action):
	// `ingest`, or `synthesize` for a wiki-retrieval synthesis save.
	Action     string     `json:"action"`
	SourceDate string     `json:"source_date"`
	Raw        string     `json:"raw"`
	Pages      []PagePlan `json:"pages"`
}

// DecodePlan reads one plan from JSON.
//
// Action defaults to `ingest`, matching the Python dataclass default, so a
// plan that omits it still commits under a verb.
func DecodePlan(r io.Reader) (Plan, error) {
	var plan Plan
	if err := json.NewDecoder(r).Decode(&plan); err != nil {
		return Plan{}, fmt.Errorf("invalid plan JSON: %w", err)
	}
	if plan.Action == "" {
		plan.Action = "ingest"
	}
	return plan, nil
}

// OrderedMap is a JSON object that remembers its key order.
//
// Frontmatter keys and edge keys are applied to a page in the order the plan
// lists them, and Go's map iteration is deliberately randomised — so
// decoding into a plain map would make the frontmatter key order of an
// ingested page vary run to run. ADR-0012 relaxes the *byte-identical*
// round-trip contract; it does not license nondeterministic output.
type OrderedMap[V any] struct {
	Keys   []string
	Values map[string]V
}

// Get returns the value for key, and whether it was present.
func (m OrderedMap[V]) Get(key string) (V, bool) {
	value, ok := m.Values[key]
	return value, ok
}

// Len returns the number of entries.
func (m OrderedMap[V]) Len() int { return len(m.Keys) }

// All iterates the entries in plan order.
func (m OrderedMap[V]) All(yield func(key string, value V) bool) {
	for _, key := range m.Keys {
		if !yield(key, m.Values[key]) {
			return
		}
	}
}

// UnmarshalJSON decodes a JSON object, recording key order as it goes. A
// duplicate key keeps its first position and takes the last value, matching
// how a Python dict literal built from the same JSON behaves.
func (m *OrderedMap[V]) UnmarshalJSON(data []byte) error {
	m.Keys = nil
	m.Values = map[string]V{}

	dec := json.NewDecoder(bytes.NewReader(data))
	tok, err := dec.Token()
	if err != nil {
		return err
	}
	if tok == nil {
		return nil // an explicit null is an absent object, not an error
	}
	if delim, ok := tok.(json.Delim); !ok || delim != '{' {
		return fmt.Errorf("expected a JSON object, got %v", tok)
	}
	for dec.More() {
		keyTok, err := dec.Token()
		if err != nil {
			return err
		}
		key, ok := keyTok.(string)
		if !ok {
			return fmt.Errorf("expected an object key, got %v", keyTok)
		}
		var value V
		if err := dec.Decode(&value); err != nil {
			return err
		}
		if _, seen := m.Values[key]; !seen {
			m.Keys = append(m.Keys, key)
		}
		m.Values[key] = value
	}
	_, err = dec.Token() // consume the closing '}'
	return err
}

// MarshalJSON writes the object back in its recorded key order.
func (m OrderedMap[V]) MarshalJSON() ([]byte, error) {
	var buf bytes.Buffer
	buf.WriteByte('{')
	for i, key := range m.Keys {
		if i > 0 {
			buf.WriteByte(',')
		}
		encodedKey, err := json.Marshal(key)
		if err != nil {
			return nil, err
		}
		buf.Write(encodedKey)
		buf.WriteByte(':')
		encodedValue, err := json.Marshal(m.Values[key])
		if err != nil {
			return nil, err
		}
		buf.Write(encodedValue)
	}
	buf.WriteByte('}')
	return buf.Bytes(), nil
}
