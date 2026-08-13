// Package wikipage is the pure half of the vault library — frontmatter
// splitting and the markdown-link machinery — ported from
// `wiki-plugin/scripts/wikipage.py`. No I/O lives here.
//
// This ticket (#150) ports only what `search`/`init` need: the frontmatter
// split, the single percent-encoding decode boundary, and link discovery.
// The mutating half (`set`/`merge`/`retarget`/`plan_move`) lands with
// `ingest` in #151, along with the property tests for the move contract.
//
// Encoding lives at a single decode boundary here: [SplitDest] splits on the
// literal `#` first and decodes each half after, so an encoded `#` in a raw
// filename can never be mistaken for an anchor separator.
package wikipage

import (
	"fmt"
	gopath "path"
	"regexp"
	"strconv"
	"strings"

	"github.com/yuin/goldmark"
	"github.com/yuin/goldmark/ast"
	"github.com/yuin/goldmark/text"
)

// encodeChars is the minimal charset that makes a raw/ filename linkable.
// Everything else — unicode, &, ', comma, + — stays literal, deliberately.
const encodeChars = " #%()<>"

// frontmatterRe matches a `---` fence on the VERY FIRST line, closed by the
// next `---` line. Anything else (a `---` mid-document) is a thematic break,
// not metadata. `(?s)` makes `.` span newlines, matching Python's re.DOTALL.
var frontmatterRe = regexp.MustCompile(`(?s)\A---[ \t]*\n(.*?\n)?---[ \t]*(?:\n|\z)`)

// linkRe matches a markdown inline link or image: `[label](dest ...)` /
// `![label](dest ...)`. `label` tolerates one level of nested brackets (e.g.
// an image inside a link). `dest` is either `<...>` or a whitespace-free run
// that may contain balanced parens (see nestedParenDest); an optional title
// after the dest is matched but excluded from `dest`.
var linkRe = regexp.MustCompile(
	`(?P<img>!?)` +
		`\[(?P<label>(?:[^\[\]]|\[[^\[\]]*\])*)\]` +
		`\(` +
		`[ \t]*` +
		`(?P<dest><[^<>\n]*>|` + nestedParenDest(4) + `)` +
		`(?:[ \t]+(?:"[^"]*"|'[^']*'|\([^)]*\)))?` +
		`[ \t]*` +
		`\)`,
)

var (
	linkImgGroup  = linkRe.SubexpIndex("img")
	linkDestGroup = linkRe.SubexpIndex("dest")
)

// nestedParenDest builds a regex fragment for an unbracketed link
// destination. Per CommonMark, a destination without `<>` ends at the first
// *unbalanced* `)` — `(draft)` inside one doesn't terminate it. RE2 has no
// recursion, so nesting is bounded at depth levels: plenty for a real
// filename or URL.
func nestedParenDest(depth int) string {
	frag := `[^()\s]*`
	for range depth {
		frag = `(?:[^()\s]|\(` + frag + `\))*`
	}
	return frag
}

// PercentEncode percent-encodes [encodeChars] in path; all else stays
// literal.
func PercentEncode(path string) string {
	var b strings.Builder
	for _, r := range path {
		if r < 128 && strings.ContainsRune(encodeChars, r) {
			fmt.Fprintf(&b, "%%%02X", r)
			continue
		}
		b.WriteRune(r)
	}
	return b.String()
}

// PercentDecode reverses [PercentEncode]. Like Python's urllib `unquote`, an
// invalid or truncated escape is left verbatim rather than being an error —
// decoding a link destination must never fail on hand-written markdown.
func PercentDecode(path string) string {
	var b strings.Builder
	for i := 0; i < len(path); i++ {
		if path[i] == '%' && i+2 < len(path) {
			if v, err := strconv.ParseUint(path[i+1:i+3], 16, 8); err == nil {
				b.WriteByte(byte(v))
				i += 2
				continue
			}
		}
		b.WriteByte(path[i])
	}
	return b.String()
}

// SplitDest splits an encoded link destination into its decoded path and
// decoded anchor.
//
// **Order matters:** split on the literal `#` first, decode each half after.
// Decoding up front would turn an encoded `#` in a filename (`%23`) into a
// false anchor separator. This is the single decode boundary — callers get
// decoded strings and never redo the split.
func SplitDest(dest string) (path, anchor string) {
	encodedPath, encodedAnchor, found := strings.Cut(dest, "#")
	path = PercentDecode(encodedPath)
	if found {
		anchor = PercentDecode(encodedAnchor)
	}
	return path, anchor
}

// SplitFrontmatter splits a leading YAML frontmatter block off text.
//
// hasFrontmatter is false when there is none, in which case body is text
// unchanged and bodyOffset is 0. `text[bodyOffset:] == body` always holds.
func SplitFrontmatter(src string) (frontmatter, body string, bodyOffset int, hasFrontmatter bool) {
	m := frontmatterRe.FindStringSubmatchIndex(src)
	if m == nil {
		return "", src, 0, false
	}
	if m[2] >= 0 {
		frontmatter = src[m[2]:m[3]]
	}
	bodyOffset = m[1]
	return frontmatter, src[bodyOffset:], bodyOffset, true
}

// LinkMatch is one link/image occurrence, positioned in the source text.
//
// Start/End bracket the *encoded* destination only — angle brackets and any
// title excluded — so `src[Start:End] == Dest` always holds. Line is 0-based.
type LinkMatch struct {
	Start         int
	End           int
	Dest          string // the encoded destination
	DecodedPath   string // SplitDest(Dest) path — decoded, anchor-free, for logic
	DecodedAnchor string // SplitDest(Dest) anchor — decoded, "" if no anchor
	IsImage       bool
	Line          int // 0-based
}

var md = goldmark.New()

// codeLineRanges returns the set of 0-based line indices that fall inside
// code blocks.
//
// goldmark reports a fenced block's *content* lines, not its ``` delimiters
// (markdown-it-py's `token.map` includes them). That difference is
// immaterial here: a fence delimiter line is a fence marker plus an info
// string, which cannot contain a markdown link.
func codeLineRanges(src string) map[int]bool {
	source := []byte(src)
	doc := md.Parser().Parse(text.NewReader(source))
	lines := map[int]bool{}
	_ = ast.Walk(doc, func(n ast.Node, entering bool) (ast.WalkStatus, error) {
		if !entering {
			return ast.WalkContinue, nil
		}
		switch n.Kind() {
		case ast.KindFencedCodeBlock, ast.KindCodeBlock:
		default:
			return ast.WalkContinue, nil
		}
		segs := n.Lines()
		for i := range segs.Len() {
			seg := segs.At(i)
			for line := lineOf(src, seg.Start); line <= lineOf(src, max(seg.Stop-1, seg.Start)); line++ {
				lines[line] = true
			}
		}
		return ast.WalkContinue, nil
	})
	return lines
}

func lineOf(src string, offset int) int {
	if offset > len(src) {
		offset = len(src)
	}
	return strings.Count(src[:offset], "\n")
}

// IterLinks returns a LinkMatch for every link/image in src, in order.
//
// Occurrences inside fenced/indented code blocks are skipped. Offsets are
// absolute into src. Scans the *whole* document, frontmatter included, so
// typed edges, `supersedes` and `raw_source` are found by the same rule as
// body links.
func IterLinks(src string) []LinkMatch {
	codeLines := codeLineRanges(src)
	var out []LinkMatch
	for _, m := range linkRe.FindAllStringSubmatchIndex(src, -1) {
		start, end := m[2*linkDestGroup], m[2*linkDestGroup+1]
		dest := src[start:end]
		// Unwrap an angle-bracketed destination: `<path>` -> `path`.
		if strings.HasPrefix(dest, "<") && strings.HasSuffix(dest, ">") {
			start, end = start+1, end-1
			dest = dest[1 : len(dest)-1]
		}
		line := lineOf(src, start)
		if codeLines[line] {
			continue
		}
		path, anchor := SplitDest(dest)
		out = append(out, LinkMatch{
			Start:         start,
			End:           end,
			Dest:          dest,
			DecodedPath:   path,
			DecodedAnchor: anchor,
			IsImage:       m[2*linkImgGroup+1] > m[2*linkImgGroup],
			Line:          line,
		})
	}
	return out
}

// ResolveLinkDest resolves an already-decoded link destination to a
// normalized path.
//
// pageDir is the vault-relative directory the link lives in (e.g.
// `wiki/concepts`), so the result is vault-relative by construction —
// ADR-0009. The one place that owns these join/clean quirks — don't
// reimplement it at a call site.
func ResolveLinkDest(dest, pageDir string) string {
	base := pageDir
	if base == "" {
		base = "."
	}
	return gopath.Clean(gopath.Join(base, dest))
}

// LinkDest extracts a whole markdown-link scalar's destination, decoded.
//
// link is a full `[label](dest)` (or image) scalar, as stored in frontmatter
// or found in body text — not a bare destination. ok is false when link
// isn't a markdown link at all.
func LinkDest(link string) (dest string, ok bool) {
	matches := IterLinks(link)
	if len(matches) == 0 {
		return "", false
	}
	return matches[0].DecodedPath, true
}
