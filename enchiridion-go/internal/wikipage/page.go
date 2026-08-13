package wikipage

// This file is the mutating half of the page model, ported from
// `wiki-plugin/scripts/wikipage.py` in #151: frontmatter get/set/merge, link
// composition, and outbound-link move-planning. Still no I/O —
// [vault.Vault] owns every read and write, and the dependency runs one way,
// `vault -> wikipage`.
//
// **One byte-preservation contract survives the port, and one does not.**
//
//   - **Kept:** link rewriting never round-trips the document through a
//     stringifier. Destinations are spliced into the raw text back-to-front
//     by exact source offset, so every untouched byte survives — including
//     frontmatter links, which the same whole-document scan finds. Property
//     tested in page_test.go; do not break it.
//   - **Dropped, deliberately:** the Python guarantee that a no-op
//     frontmatter [Page.Set] round-trips byte-identical. No Go YAML library
//     matches ruamel.yaml's round-trip fidelity, and text-splicing YAML was
//     rejected as more bug-prone than the formatting churn it avoids. See
//     docs/adr/0012-go-frontmatter-round-trip-relaxed.md. Key *order* is
//     still preserved (frontmatter is edited as a [yaml.Node] mapping, so
//     existing keys keep their position and new ones append), because a
//     reordering edit would make every ingest diff unreadable — only
//     incidental formatting may change.

import (
	"bytes"
	"fmt"
	gopath "path"
	"reflect"
	"slices"
	"sort"
	"strings"

	"gopkg.in/yaml.v3"
)

// Page is one page's frontmatter plus body. Pure-functional — no I/O, no
// mutation: [Page.Set], [Page.Merge] and [Page.Retarget] each return a *new*
// Page.
type Page struct {
	Text string
}

// yamlIndent matches the conventions-spec indentation, so a block sequence
// under a mapping key renders as `  - "[t](p.md)"` — the same shape
// ruamel.yaml's `indent(mapping=2, sequence=4, offset=2)` produces on the
// Python side.
const yamlIndent = 2

// frontmatterNode returns p's frontmatter as a YAML mapping node, minting an
// empty one when the page has no frontmatter block (or an empty one).
//
// A node rather than a map because a mapping node preserves key order — see
// this file's package comment for why that matters.
func (p Page) frontmatterNode() (*yaml.Node, error) {
	fm, _, _, _ := SplitFrontmatter(p.Text)
	empty := &yaml.Node{Kind: yaml.MappingNode, Tag: "!!map"}
	if strings.TrimSpace(fm) == "" {
		return empty, nil
	}
	var doc yaml.Node
	if err := yaml.Unmarshal([]byte(fm), &doc); err != nil {
		return nil, fmt.Errorf("invalid frontmatter YAML: %w", err)
	}
	if len(doc.Content) == 0 {
		return empty, nil
	}
	node := doc.Content[0]
	if node.Kind != yaml.MappingNode {
		return nil, fmt.Errorf("frontmatter is not a YAML mapping")
	}
	return node, nil
}

// Frontmatter returns the full frontmatter mapping, decoded to plain values,
// or nil when this page has no frontmatter block.
func (p Page) Frontmatter() (map[string]any, error) {
	if _, _, _, ok := SplitFrontmatter(p.Text); !ok {
		return nil, nil
	}
	node, err := p.frontmatterNode()
	if err != nil {
		return nil, err
	}
	data := map[string]any{}
	if err := node.Decode(&data); err != nil {
		return nil, fmt.Errorf("invalid frontmatter YAML: %w", err)
	}
	return data, nil
}

// Get returns the value of key in this page's frontmatter. ok is false when
// the page has no frontmatter or the key is absent.
func (p Page) Get(key string) (value any, ok bool, err error) {
	data, err := p.Frontmatter()
	if err != nil || data == nil {
		return nil, false, err
	}
	value, ok = data[key]
	return value, ok, nil
}

// GetString returns a string-valued frontmatter key, or "" when it is
// absent, null, or not a string.
func (p Page) GetString(key string) (string, error) {
	value, _, err := p.Get(key)
	if err != nil {
		return "", err
	}
	s, _ := value.(string)
	return s, nil
}

// GetStringList returns a list-valued frontmatter key's string entries.
// A key that is absent, null, or not a list yields nil; non-string entries
// within a list are skipped.
func (p Page) GetStringList(key string) ([]string, error) {
	value, _, err := p.Get(key)
	if err != nil {
		return nil, err
	}
	items, ok := value.([]any)
	if !ok {
		return nil, nil
	}
	out := make([]string, 0, len(items))
	for _, item := range items {
		if s, ok := item.(string); ok {
			out = append(out, s)
		}
	}
	return out, nil
}

// Set returns a new page with frontmatter key set to value.
//
// Mints a frontmatter block when the page has none. Only the block is
// re-serialised; the body is spliced back verbatim.
func (p Page) Set(key string, value any) (Page, error) {
	node, err := p.frontmatterNode()
	if err != nil {
		return Page{}, err
	}
	valueNode, err := newValueNode(value)
	if err != nil {
		return Page{}, fmt.Errorf("frontmatter %s: %w", key, err)
	}
	setKey(node, key, valueNode)

	rendered, err := renderFrontmatter(node)
	if err != nil {
		return Page{}, err
	}
	// With no frontmatter yet, body is the whole text — so the same
	// expression prepends a fresh block to the untouched document.
	_, body, _, _ := SplitFrontmatter(p.Text)
	return Page{Text: "---\n" + rendered + "---\n" + body}, nil
}

// Merge returns a new page with values unioned into key's existing list.
//
// Order-preserving: existing entries hold their position, new ones append,
// duplicates drop. Equivalent to [Page.Set] when key is absent. Use this, not
// get-union-set by hand, for any list-valued key (`tags`, the typed-edge
// keys) that may already have entries.
func (p Page) Merge(key string, values []any) (Page, error) {
	existing, _, err := p.Get(key)
	if err != nil {
		return Page{}, err
	}
	var merged []any
	if items, ok := existing.([]any); ok {
		merged = append(merged, items...)
	}
	for _, value := range values {
		if !containsValue(merged, value) {
			merged = append(merged, value)
		}
	}
	return p.Set(key, merged)
}

// MergeStrings is [Page.Merge] over a string list — the shape every caller
// with typed-edge links or tags already has.
func (p Page) MergeStrings(key string, values []string) (Page, error) {
	boxed := make([]any, len(values))
	for i, value := range values {
		boxed[i] = value
	}
	return p.Merge(key, boxed)
}

// containsValue reports whether values already holds value.
//
// Structural comparison, matching Python's `in` — and unlike `==` on two
// `any`, it cannot panic when a plan hands us a nested list or map under a
// list-valued frontmatter key.
func containsValue(values []any, value any) bool {
	return slices.ContainsFunc(values, func(existing any) bool {
		return reflect.DeepEqual(existing, value)
	})
}

// setKey replaces key's value node in the mapping, or appends the pair when
// key is absent — so existing keys keep their position and new keys land at
// the end, matching the Python dict assignment this ports.
func setKey(mapping *yaml.Node, key string, value *yaml.Node) {
	for i := 0; i+1 < len(mapping.Content); i += 2 {
		if mapping.Content[i].Value == key {
			mapping.Content[i+1] = value
			return
		}
	}
	mapping.Content = append(mapping.Content,
		&yaml.Node{Kind: yaml.ScalarNode, Tag: "!!str", Value: key},
		value,
	)
}

// newValueNode encodes a plain Go value to a YAML node, double-quoting any
// fresh markdown-link scalar.
//
// A first-time value has no prior style to round-trip from, and yaml.v3 would
// otherwise reach for single quotes — against the conventions spec, which
// pins `"[…](…)"`. Only strings starting `[` are touched; image embeds
// (`![…]`) never appear in frontmatter, so that form isn't handled.
func newValueNode(value any) (*yaml.Node, error) {
	node := &yaml.Node{}
	if err := node.Encode(value); err != nil {
		return nil, err
	}
	quoteLinks(node)
	return node, nil
}

func quoteLinks(node *yaml.Node) {
	switch node.Kind {
	case yaml.ScalarNode:
		if node.Tag == "!!str" && strings.HasPrefix(node.Value, "[") {
			node.Style = yaml.DoubleQuotedStyle
		}
	case yaml.SequenceNode:
		for _, child := range node.Content {
			quoteLinks(child)
		}
	}
}

func renderFrontmatter(node *yaml.Node) (string, error) {
	var buf bytes.Buffer
	enc := yaml.NewEncoder(&buf)
	enc.SetIndent(yamlIndent)
	if err := enc.Encode(node); err != nil {
		return "", err
	}
	if err := enc.Close(); err != nil {
		return "", err
	}
	return buf.String(), nil
}

// Body returns the document body — everything after the frontmatter block.
func (p Page) Body() string {
	_, body, _, _ := SplitFrontmatter(p.Text)
	return body
}

// Links returns every link/image in this page, body and frontmatter alike,
// in order.
func (p Page) Links() []LinkMatch { return IterLinks(p.Text) }

// Retarget returns a new page with links fixed for the vault-wide move
// oldRel -> newRel.
//
// fileRel is where *this* page sits before the move; pass fileRel == oldRel
// when this page is the one being moved, so its own outbound links are
// rebased onto newRel's folder too.
func (p Page) Retarget(fileRel, oldRel, newRel string) Page {
	return Page{Text: rewriteText(p.Text, fileRel, oldRel, newRel)}
}

// PlanMove computes the post-move vault from pages (a {pageRef: text} map).
//
// Pure. The moved page appears under newRel; every other page keeps its key.
// Inbound and outbound links are both fixed.
//
// oldRel need not be a key of pages: a caller retargeting links at a non-page
// file (a `raw/` artifact, say) passes only the markdown pages whose
// *inbound* links should follow the rename.
func PlanMove(pages map[string]string, oldRel, newRel string) map[string]string {
	out := make(map[string]string, len(pages))
	for rel, text := range pages {
		key := rel
		if rel == oldRel {
			key = newRel
		}
		out[key] = Page{Text: text}.Retarget(rel, oldRel, newRel).Text
	}
	return out
}

// ComposeLink composes a markdown link to targetRel from a page in pageDir.
//
// Both are vault-relative (`wiki/concepts/foo.md` / `wiki/synthesis`);
// pageDir may be "" for a page at the vault root. Relativises the target and
// percent-encodes the destination — never the label. YAML quoting is not done
// here: [Page.Set]/[Page.Merge] already double-quote a fresh `[…]` scalar.
func ComposeLink(title, targetRel, pageDir string) string {
	dest := relPath(gopath.Clean(targetRel), pageDir)
	return "[" + title + "](" + PercentEncode(dest) + ")"
}

// NormalizeBodyLinks re-encodes every relative link/image destination in src.
//
// An author (human or agent) may write a destination unencoded — a raw
// filename with a space or paren, taken verbatim. This normalises each one,
// via the same offset-based splice [Page.Retarget] uses, so untouched bytes
// survive. Idempotent. Absolute paths, scheme-qualified URLs, and bare
// anchors are left alone.
func NormalizeBodyLinks(src string) string {
	var edits []edit
	for _, link := range IterLinks(src) {
		if !isRelativeDest(link.DecodedPath) {
			continue
		}
		if dest := encodeDest(link.DecodedPath, link.DecodedAnchor); dest != link.Dest {
			edits = append(edits, edit{link.Start, link.End, dest})
		}
	}
	return applyEdits(src, edits)
}

// edit is one destination splice: replace src[start:end] with dest.
type edit struct {
	start, end int
	dest       string
}

// applyEdits splices edits into src back-to-front by source offset, so every
// untouched byte survives and earlier offsets stay valid as later ones are
// replaced.
func applyEdits(src string, edits []edit) string {
	sort.Slice(edits, func(i, j int) bool { return edits[i].start > edits[j].start })
	for _, e := range edits {
		src = src[:e.start] + e.dest + src[e.end:]
	}
	return src
}

// encodeDest re-encodes a decoded path and anchor back into a link
// destination.
func encodeDest(path, anchor string) string {
	dest := PercentEncode(path)
	if anchor != "" {
		dest += "#" + PercentEncode(anchor)
	}
	return dest
}

// isRelativeDest reports whether path (the pre-anchor part of a destination)
// is a vault-relative reference. Excludes the empty destination, absolute
// paths, bare anchors, and any scheme-qualified URL.
func isRelativeDest(path string) bool {
	return path != "" &&
		!strings.HasPrefix(path, "/") &&
		!strings.HasPrefix(path, "#") &&
		!strings.Contains(path, "://")
}

// rewriteText returns text with its links fixed for the move oldRel ->
// newRel.
//
// fileRel is where *this* file sits before the move; only the moved file
// itself (fileRel == oldRel) also changes its own location, so where it ends
// up is derived rather than passed in.
func rewriteText(text, fileRel, oldRel, newRel string) string {
	isMovedFile := fileRel == oldRel
	oldDir := gopath.Dir(fileRel)
	newDir := gopath.Dir(fileRel)
	if isMovedFile {
		newDir = gopath.Dir(newRel)
	}

	var edits []edit
	for _, link := range IterLinks(text) {
		if !isRelativeDest(link.DecodedPath) {
			continue
		}
		// Where this link pointed, resolved from the file's original location.
		target := ResolveLinkDest(link.DecodedPath, oldDir)
		// For pages other than the moved one, only links at the moved page change.
		if !isMovedFile && target != oldRel {
			continue
		}
		// The moved page itself relocates the target of a self-link.
		movedTarget := target
		if target == oldRel {
			movedTarget = newRel
		}
		if dest := encodeDest(relPath(movedTarget, newDir), link.DecodedAnchor); dest != link.Dest {
			edits = append(edits, edit{link.Start, link.End, dest})
		}
	}
	return applyEdits(text, edits)
}

// relPath is `posixpath.relpath` over two vault-relative slash paths: the
// route from base to target, spelled with `../` segments.
//
// Go's stdlib has only the OS-specific `filepath.Rel`, which would emit
// backslashes on Windows and put them in a markdown destination. Both
// arguments are vault-relative by construction, so the general
// absolute/relative-mismatch case `filepath.Rel` guards against cannot arise.
func relPath(target, base string) string {
	targetParts := pathParts(target)
	baseParts := pathParts(base)

	common := 0
	for common < len(targetParts) && common < len(baseParts) &&
		targetParts[common] == baseParts[common] {
		common++
	}

	parts := make([]string, 0, len(baseParts)-common+len(targetParts)-common)
	for range baseParts[common:] {
		parts = append(parts, "..")
	}
	parts = append(parts, targetParts[common:]...)
	if len(parts) == 0 {
		return "."
	}
	return strings.Join(parts, "/")
}

func pathParts(path string) []string {
	cleaned := gopath.Clean(path)
	if cleaned == "." || cleaned == "" {
		return nil
	}
	return strings.Split(cleaned, "/")
}
