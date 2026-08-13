package wikipage

import (
	"fmt"
	gopath "path"
	"strings"
	"testing"

	"pgregory.net/rapid"
)

func TestSetMintsFrontmatterBlock(t *testing.T) {
	page, err := Page{Text: "body text\n"}.Set("title", "A Page")
	if err != nil {
		t.Fatalf("Set: %v", err)
	}
	want := "---\ntitle: A Page\n---\nbody text\n"
	if page.Text != want {
		t.Errorf("Set on a page with no frontmatter =\n%q\nwant\n%q", page.Text, want)
	}
}

func TestSetPreservesKeyOrderAndBody(t *testing.T) {
	src := "---\ntitle: A\nsummary: s\nvolatility: stable\n---\n\n# Heading\n\nbody\n"
	page, err := Page{Text: src}.Set("summary", "new summary")
	if err != nil {
		t.Fatalf("Set: %v", err)
	}
	want := "---\ntitle: A\nsummary: new summary\nvolatility: stable\n---\n\n# Heading\n\nbody\n"
	if page.Text != want {
		t.Errorf("Set =\n%q\nwant\n%q", page.Text, want)
	}
}

func TestSetAppendsNewKeyAtEnd(t *testing.T) {
	page, err := Page{Text: "---\ntitle: A\n---\nbody\n"}.Set("volatility", "stable")
	if err != nil {
		t.Fatalf("Set: %v", err)
	}
	if !strings.HasPrefix(page.Text, "---\ntitle: A\nvolatility: stable\n---\n") {
		t.Errorf("new key did not append after existing keys: %q", page.Text)
	}
}

// A block sequence must render as `  - "…"` — the conventions-spec
// indentation ruamel.yaml is pinned to on the Python side, so a vault edited
// by either implementation looks the same.
func TestSetRendersLinkListAtSpecIndentation(t *testing.T) {
	page, err := Page{Text: "---\ntitle: A\n---\nbody\n"}.Set(
		"source", []string{"[Stub](../sources/stub.md)"})
	if err != nil {
		t.Fatalf("Set: %v", err)
	}
	want := "---\ntitle: A\nsource:\n  - \"[Stub](../sources/stub.md)\"\n---\nbody\n"
	if page.Text != want {
		t.Errorf("Set =\n%q\nwant\n%q", page.Text, want)
	}
}

func TestSetLeavesNonLinkScalarsUnquoted(t *testing.T) {
	page, err := Page{Text: ""}.Set("tags", []string{"deploy", "ci"})
	if err != nil {
		t.Fatalf("Set: %v", err)
	}
	if strings.Contains(page.Text, `"`) {
		t.Errorf("non-link scalars should not be force-quoted: %q", page.Text)
	}
}

func TestMergeUnionsPreservingOrder(t *testing.T) {
	src := "---\ntags:\n  - a\n  - b\n---\nbody\n"
	page, err := Page{Text: src}.MergeStrings("tags", []string{"b", "c"})
	if err != nil {
		t.Fatalf("Merge: %v", err)
	}
	got, err := page.GetStringList("tags")
	if err != nil {
		t.Fatalf("GetStringList: %v", err)
	}
	want := []string{"a", "b", "c"}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("Merge = %v, want %v", got, want)
	}
}

func TestMergeOnAbsentKeyBehavesLikeSet(t *testing.T) {
	page, err := Page{Text: "---\ntitle: A\n---\nbody\n"}.MergeStrings("tags", []string{"x"})
	if err != nil {
		t.Fatalf("Merge: %v", err)
	}
	got, err := page.GetStringList("tags")
	if err != nil {
		t.Fatalf("GetStringList: %v", err)
	}
	if len(got) != 1 || got[0] != "x" {
		t.Errorf("Merge on absent key = %v, want [x]", got)
	}
}

func TestGetAbsentAndNoFrontmatter(t *testing.T) {
	if _, ok, err := (Page{Text: "body\n"}).Get("title"); err != nil || ok {
		t.Errorf("Get on a page with no frontmatter = ok %v, err %v; want false, nil", ok, err)
	}
	if _, ok, err := (Page{Text: "---\ntitle: A\n---\n"}).Get("summary"); err != nil || ok {
		t.Errorf("Get of an absent key = ok %v, err %v; want false, nil", ok, err)
	}
}

func TestGetInvalidYAMLIsAnError(t *testing.T) {
	if _, _, err := (Page{Text: "---\ntitle: [unclosed\n---\nbody\n"}).Get("title"); err == nil {
		t.Error("Get on invalid frontmatter YAML: want an error, got nil")
	}
}

func TestFrontmatterNilWithoutBlock(t *testing.T) {
	data, err := Page{Text: "body\n"}.Frontmatter()
	if err != nil || data != nil {
		t.Errorf("Frontmatter() = %v, %v; want nil, nil", data, err)
	}
}

func TestComposeLink(t *testing.T) {
	tests := []struct {
		title, target, pageDir, want string
	}{
		{"Foo", "wiki/concepts/foo.md", "wiki/synthesis", "[Foo](../concepts/foo.md)"},
		{"Foo", "wiki/concepts/foo.md", "wiki/concepts", "[Foo](foo.md)"},
		{"raw doc.md", "raw/raw doc.md", "wiki/sources", "[raw doc.md](../../raw/raw%20doc.md)"},
		{"Foo", "wiki/concepts/foo.md", "", "[Foo](wiki/concepts/foo.md)"},
	}
	for _, tc := range tests {
		if got := ComposeLink(tc.title, tc.target, tc.pageDir); got != tc.want {
			t.Errorf("ComposeLink(%q, %q, %q) = %q, want %q",
				tc.title, tc.target, tc.pageDir, got, tc.want)
		}
	}
}

func TestNormalizeBodyLinksIsIdempotent(t *testing.T) {
	// A destination is whitespace-free by CommonMark, so the unencoded shapes
	// an author actually produces are the balanced parens and `#` that
	// PercentEncode covers.
	src := "See [raw](../../raw/spec(v2).md) and [ext](https://example.com/x(1)) and [a](#anchor).\n"
	once := NormalizeBodyLinks(src)
	if !strings.Contains(once, "spec%28v2%29.md") {
		t.Errorf("relative destination not encoded: %q", once)
	}
	if !strings.Contains(once, "https://example.com/x(1)") {
		t.Errorf("scheme-qualified URL should be left alone: %q", once)
	}
	if !strings.Contains(once, "](#anchor)") {
		t.Errorf("bare anchor should be left alone: %q", once)
	}
	if twice := NormalizeBodyLinks(once); twice != once {
		t.Errorf("NormalizeBodyLinks not idempotent:\n%q\n%q", once, twice)
	}
}

func TestRetargetFixesInboundAndOutbound(t *testing.T) {
	pages := map[string]string{
		"wiki/concepts/a.md": "---\nrelated:\n  - \"[B](b.md)\"\n---\nSee [B](b.md).\n",
		"wiki/concepts/b.md": "Back to [A](a.md).\n",
	}
	moved := PlanMove(pages, "wiki/concepts/b.md", "wiki/entities/b.md")

	if _, ok := moved["wiki/concepts/b.md"]; ok {
		t.Error("moved page still keyed under its old ref")
	}
	if got := moved["wiki/concepts/a.md"]; !strings.Contains(got, "(../entities/b.md)") {
		t.Errorf("inbound link not retargeted, including in frontmatter: %q", got)
	}
	if got := moved["wiki/entities/b.md"]; !strings.Contains(got, "(../concepts/a.md)") {
		t.Errorf("moved page's outbound link not rebased: %q", got)
	}
}

func TestRetargetLeavesAbsoluteAndExternalDestinations(t *testing.T) {
	src := "[x](/abs/b.md) [y](https://example.com/b.md) [z](#anchor)\n"
	got := Page{Text: src}.Retarget("wiki/concepts/a.md", "wiki/concepts/b.md", "wiki/entities/b.md")
	if got.Text != src {
		t.Errorf("Retarget touched a non-relative destination:\n%q\n%q", got.Text, src)
	}
}

func TestRetargetPreservesAnchors(t *testing.T) {
	src := "[B](b.md#a%20section)\n"
	got := Page{Text: src}.Retarget("wiki/concepts/a.md", "wiki/concepts/b.md", "wiki/entities/b.md")
	want := "[B](../entities/b.md#a%20section)\n"
	if got.Text != want {
		t.Errorf("Retarget = %q, want %q", got.Text, want)
	}
}

func TestRetargetSkipsLinksInCodeBlocks(t *testing.T) {
	src := "```\n[B](b.md)\n```\n"
	got := Page{Text: src}.Retarget("wiki/concepts/a.md", "wiki/concepts/b.md", "wiki/entities/b.md")
	if got.Text != src {
		t.Errorf("Retarget rewrote a link inside a code block: %q", got.Text)
	}
}

// --- the move contract, property-tested -------------------------------------
//
// ADR-0011/0012: *a page move touches only link lines, and every link still
// resolves to the same file it did before*. This is the contract the Go port
// keeps (the byte-identical frontmatter round-trip is the one it drops), so
// it is checked over generated vaults rather than hand-picked examples.

// vaultDirs are the directories a generated page may live in — enough shape
// variation (sibling, cousin, vault root) to exercise every `../` case
// relativisation can produce.
var vaultDirs = []string{"wiki/concepts", "wiki/entities", "wiki/sources", "wiki/synthesis", ""}

// genVault draws a small vault of pages that link to each other, plus the
// old/new ref of a move to plan over it.
func genVault(t *rapid.T) (pages map[string]string, oldRel, newRel string) {
	names := rapid.SliceOfNDistinct(
		rapid.SampledFrom([]string{"a", "b", "c", "d"}), 2, 4,
		func(s string) string { return s },
	).Draw(t, "names")

	refs := make([]string, len(names))
	for i, name := range names {
		dir := rapid.SampledFrom(vaultDirs).Draw(t, "dir")
		refs[i] = gopath.Join(dir, name+".md")
	}

	pages = make(map[string]string, len(refs))
	for _, ref := range refs {
		var b strings.Builder
		fmt.Fprintf(&b, "---\ntitle: %s\nrelated:\n", gopath.Base(ref))
		for _, target := range refs {
			fmt.Fprintf(&b, "  - \"[t](%s)\"\n",
				PercentEncode(relPath(target, gopath.Dir(ref))))
		}
		b.WriteString("---\n\n")
		for _, target := range refs {
			fmt.Fprintf(&b, "Body link [t](%s)\n",
				PercentEncode(relPath(target, gopath.Dir(ref))))
		}
		b.WriteString("\n```\n[frozen](never-touched.md)\n```\n")
		pages[ref] = b.String()
	}

	oldRel = rapid.SampledFrom(refs).Draw(t, "oldRel")
	newDir := rapid.SampledFrom(vaultDirs).Draw(t, "newDir")
	newRel = gopath.Join(newDir, gopath.Base(oldRel))
	return pages, oldRel, newRel
}

// resolvedTargets is every link in text resolved from pageDir — the "where
// does this page point" fact a move must leave unchanged.
func resolvedTargets(text, pageDir string) []string {
	var out []string
	for _, link := range IterLinks(text) {
		if isRelativeDest(link.DecodedPath) {
			out = append(out, ResolveLinkDest(link.DecodedPath, pageDir))
		}
	}
	return out
}

// TestMovePreservesEveryLinkTarget is the "all links still resolve" half:
// after the move, every link resolves to what it pointed at before, with the
// moved page's own ref substituted.
func TestMovePreservesEveryLinkTarget(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		pages, oldRel, newRel := genVault(t)
		if _, taken := pages[newRel]; taken && newRel != oldRel {
			t.Skip("move onto an occupied ref is not a legal move")
		}
		moved := PlanMove(pages, oldRel, newRel)

		for ref, before := range pages {
			afterRef := ref
			if ref == oldRel {
				afterRef = newRel
			}
			after, ok := moved[afterRef]
			if !ok {
				t.Fatalf("page %q missing after the move", afterRef)
			}

			wantTargets := resolvedTargets(before, gopath.Dir(ref))
			gotTargets := resolvedTargets(after, gopath.Dir(afterRef))
			if len(wantTargets) != len(gotTargets) {
				t.Fatalf("%s: link count changed: %d -> %d", ref, len(wantTargets), len(gotTargets))
			}
			for i, want := range wantTargets {
				if want == oldRel {
					want = newRel
				}
				if gotTargets[i] != want {
					t.Fatalf("%s: link %d resolved to %q, want %q",
						ref, i, gotTargets[i], want)
				}
			}
		}
	})
}

// TestMoveTouchesOnlyLinkLines is the "touches only link lines" half: every
// line that changed must have held a link whose destination the move
// relocated. Frontmatter lines count — typed edges are spliced by the same
// whole-document rule as body links.
func TestMoveTouchesOnlyLinkLines(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		pages, oldRel, newRel := genVault(t)
		if _, taken := pages[newRel]; taken && newRel != oldRel {
			t.Skip("move onto an occupied ref is not a legal move")
		}
		moved := PlanMove(pages, oldRel, newRel)

		for ref, before := range pages {
			afterRef := ref
			if ref == oldRel {
				afterRef = newRel
			}
			after := moved[afterRef]

			beforeLines := strings.Split(before, "\n")
			afterLines := strings.Split(after, "\n")
			if len(beforeLines) != len(afterLines) {
				t.Fatalf("%s: line count changed: %d -> %d",
					ref, len(beforeLines), len(afterLines))
			}
			linkLines := map[int]bool{}
			for _, link := range IterLinks(before) {
				linkLines[link.Line] = true
			}
			for i := range beforeLines {
				if beforeLines[i] != afterLines[i] && !linkLines[i] {
					t.Fatalf("%s: line %d changed but held no link:\n%q\n%q",
						ref, i, beforeLines[i], afterLines[i])
				}
			}
		}
	})
}

func TestRelPath(t *testing.T) {
	tests := []struct{ target, base, want string }{
		{"wiki/concepts/a.md", "wiki/concepts", "a.md"},
		{"wiki/concepts/a.md", "wiki/synthesis", "../concepts/a.md"},
		{"wiki/concepts/a.md", "", "wiki/concepts/a.md"},
		{"wiki/concepts/a.md", ".", "wiki/concepts/a.md"},
		{"raw/doc.md", "wiki/sources", "../../raw/doc.md"},
		{"wiki/concepts", "wiki/concepts", "."},
	}
	for _, tc := range tests {
		if got := relPath(tc.target, tc.base); got != tc.want {
			t.Errorf("relPath(%q, %q) = %q, want %q", tc.target, tc.base, got, tc.want)
		}
	}
}

// relPath must always be the inverse of ResolveLinkDest: resolving the route
// it computes, from the directory it computed it for, lands back on the
// target. Every composed link in the vault depends on that.
func TestRelPathRoundTripsThroughResolve(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		target := gopath.Join(
			rapid.SampledFrom(vaultDirs).Draw(t, "targetDir"),
			rapid.SampledFrom([]string{"a.md", "b.md"}).Draw(t, "name"),
		)
		base := rapid.SampledFrom(vaultDirs).Draw(t, "base")
		if got := ResolveLinkDest(relPath(target, base), base); got != target {
			t.Fatalf("ResolveLinkDest(relPath(%q, %q), %q) = %q", target, base, base, got)
		}
	})
}
