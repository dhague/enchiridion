package wikipage

import (
	"strings"
	"testing"

	"pgregory.net/rapid"
)

func TestSplitFrontmatter(t *testing.T) {
	tests := []struct {
		name        string
		text        string
		wantFM      string
		wantBody    string
		wantPresent bool
	}{
		{
			name:        "leading block",
			text:        "---\ntitle: A\n---\nbody\n",
			wantFM:      "title: A\n",
			wantBody:    "body\n",
			wantPresent: true,
		},
		{
			name:        "empty block",
			text:        "---\n---\nbody\n",
			wantFM:      "",
			wantBody:    "body\n",
			wantPresent: true,
		},
		{
			name:        "no frontmatter",
			text:        "body\n---\nnot metadata\n",
			wantFM:      "",
			wantBody:    "body\n---\nnot metadata\n",
			wantPresent: false,
		},
		{
			name:        "thematic break mid-document is not frontmatter",
			text:        "# Title\n\n---\n\nmore\n",
			wantFM:      "",
			wantBody:    "# Title\n\n---\n\nmore\n",
			wantPresent: false,
		},
		{
			name:        "closing fence at end of file",
			text:        "---\ntitle: A\n---",
			wantFM:      "title: A\n",
			wantBody:    "",
			wantPresent: true,
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			fm, body, offset, present := SplitFrontmatter(tc.text)
			if present != tc.wantPresent {
				t.Fatalf("present = %v, want %v", present, tc.wantPresent)
			}
			if fm != tc.wantFM {
				t.Errorf("frontmatter = %q, want %q", fm, tc.wantFM)
			}
			if body != tc.wantBody {
				t.Errorf("body = %q, want %q", body, tc.wantBody)
			}
			if got := tc.text[offset:]; got != body {
				t.Errorf("text[offset:] = %q, want body %q", got, body)
			}
		})
	}
}

func TestPercentEncodeOnlyTheMinimalCharset(t *testing.T) {
	// Unicode, ampersands, apostrophes and commas stay literal, deliberately
	// — only the characters that break a markdown destination are encoded.
	got := PercentEncode("raw/Über & Co's, notes (draft) #1 <x>.md")
	want := "raw/Über%20&%20Co's,%20notes%20%28draft%29%20%231%20%3Cx%3E.md"
	if got != want {
		t.Fatalf("PercentEncode = %q, want %q", got, want)
	}
}

func TestPercentDecodeLeavesInvalidEscapesVerbatim(t *testing.T) {
	for _, input := range []string{"100%", "a%zz", "trailing%2"} {
		if got := PercentDecode(input); got != input {
			t.Errorf("PercentDecode(%q) = %q, want it unchanged", input, got)
		}
	}
}

func TestPercentRoundTrip(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		path := rapid.String().Draw(t, "path")
		if got := PercentDecode(PercentEncode(path)); got != path {
			t.Fatalf("round trip gave %q, want %q", got, path)
		}
	})
}

func TestSplitDestSplitsBeforeDecoding(t *testing.T) {
	// The whole point of the single decode boundary: an encoded `#` in a
	// filename must not be mistaken for an anchor separator.
	path, anchor := SplitDest("raw/notes%20%231.md")
	if path != "raw/notes #1.md" {
		t.Errorf("path = %q, want %q", path, "raw/notes #1.md")
	}
	if anchor != "" {
		t.Errorf("anchor = %q, want empty", anchor)
	}

	path, anchor = SplitDest("wiki/concepts/a.md#some%20heading")
	if path != "wiki/concepts/a.md" || anchor != "some heading" {
		t.Errorf("got (%q, %q), want (%q, %q)",
			path, anchor, "wiki/concepts/a.md", "some heading")
	}
}

func TestIterLinks(t *testing.T) {
	text := strings.Join([]string{
		"---",
		`raw_source: "[notes.md](../../raw/notes%20%281%29.md)"`,
		"---",
		"",
		"A [link](../entities/x.md) and an ![image](img/y.png).",
		"An <angle> one: [t](<a b.md>).",
		"A titled one: [t](z.md \"the title\").",
		"",
		"```",
		"[not a link](nope.md)",
		"```",
		"",
		"    [indented code](nope2.md)",
		"",
		"[external](https://example.com/a(b)c).",
	}, "\n")

	var dests []string
	for _, m := range IterLinks(text) {
		dests = append(dests, m.DecodedPath)
		if text[m.Start:m.End] != m.Dest {
			t.Errorf("offsets don't bracket dest for %q", m.Dest)
		}
	}

	want := []string{
		"../../raw/notes (1).md",
		"../entities/x.md",
		"img/y.png",
		"a b.md",
		"z.md",
		"https://example.com/a(b)c",
	}
	if len(dests) != len(want) {
		t.Fatalf("got %d links %v, want %d %v", len(dests), dests, len(want), want)
	}
	for i := range want {
		if dests[i] != want[i] {
			t.Errorf("link %d = %q, want %q", i, dests[i], want[i])
		}
	}
}

func TestIterLinksMarksImages(t *testing.T) {
	links := IterLinks("[a](a.md) ![b](b.png)")
	if len(links) != 2 {
		t.Fatalf("got %d links, want 2", len(links))
	}
	if links[0].IsImage {
		t.Error("plain link reported as an image")
	}
	if !links[1].IsImage {
		t.Error("image not reported as an image")
	}
}

func TestLinkDest(t *testing.T) {
	got, ok := LinkDest("[Some page](../concepts/some-page.md)")
	if !ok || got != "../concepts/some-page.md" {
		t.Fatalf("got (%q, %v), want (%q, true)", got, ok, "../concepts/some-page.md")
	}
	if _, ok := LinkDest("just a string"); ok {
		t.Error("a non-link scalar reported a destination")
	}
}

func TestResolveLinkDest(t *testing.T) {
	tests := []struct {
		dest, pageDir, want string
	}{
		{"../entities/x.md", "wiki/concepts", "wiki/entities/x.md"},
		{"a.md", "wiki/concepts", "wiki/concepts/a.md"},
		{"../../raw/n.md", "wiki/sources", "raw/n.md"},
		{"wiki/concepts/a.md", "", "wiki/concepts/a.md"},
		{"./a.md", "wiki/concepts", "wiki/concepts/a.md"},
	}
	for _, tc := range tests {
		if got := ResolveLinkDest(tc.dest, tc.pageDir); got != tc.want {
			t.Errorf("ResolveLinkDest(%q, %q) = %q, want %q",
				tc.dest, tc.pageDir, got, tc.want)
		}
	}
}
