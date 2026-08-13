package supersededby

import (
	"reflect"
	"testing"

	"github.com/dhague/enchiridion/enchiridion-go/internal/pagerecord"
)

func page(title string, supersedes string) string {
	text := "---\ntitle: " + title + "\nsummary: s\ntags: []\nsource_date: 2026-01-01\nvolatility: stable\n"
	if supersedes != "" {
		text += "supersedes:\n  - \"[Old](" + supersedes + ")\"\n"
	}
	text += "---\n\n"
	return text
}

func records(t *testing.T, pages map[string]string) map[string]pagerecord.PageRecord {
	t.Helper()
	recs, err := pagerecord.LoadRecords(pages)
	if err != nil {
		t.Fatalf("LoadRecords: %v", err)
	}
	return recs
}

func TestResolveCurrentPageResolvesToItself(t *testing.T) {
	recs := records(t, map[string]string{"wiki/concepts/a.md": page("A", "")})
	res := Resolve([]string{"wiki/concepts/a.md"}, recs)
	want := []Resolution{{Seed: "wiki/concepts/a.md", Active: "wiki/concepts/a.md", Chain: []string{}}}
	if !reflect.DeepEqual(res, want) {
		t.Errorf("Resolve = %v, want %v", res, want)
	}
}

func TestResolveSupersededSeedResolvesToReplacement(t *testing.T) {
	recs := records(t, map[string]string{
		"wiki/concepts/old.md": page("Old", ""),
		"wiki/concepts/new.md": page("New", "old.md"),
	})
	res := Resolve([]string{"wiki/concepts/old.md"}, recs)
	if res[0].Active != "wiki/concepts/new.md" {
		t.Errorf("active = %q, want new.md", res[0].Active)
	}
	if !reflect.DeepEqual(res[0].Chain, []string{"wiki/concepts/new.md"}) {
		t.Errorf("chain = %v", res[0].Chain)
	}
}

func TestResolveHeadReturnedEvenWhenOutsideCandidateSet(t *testing.T) {
	recs := records(t, map[string]string{
		"wiki/concepts/old.md": page("Old", ""),
		"wiki/concepts/new.md": page("New", "old.md"),
	})
	res := Resolve([]string{"wiki/concepts/old.md"}, recs)
	if res[0].Active != "wiki/concepts/new.md" {
		t.Errorf("active = %q, want new.md", res[0].Active)
	}
}

func TestResolveMultiHopChainWalksToFinalHead(t *testing.T) {
	recs := records(t, map[string]string{
		"wiki/concepts/a.md": page("A", ""),
		"wiki/concepts/b.md": page("B", "a.md"),
		"wiki/concepts/c.md": page("C", "b.md"),
	})
	res := Resolve([]string{"wiki/concepts/a.md"}, recs)
	if res[0].Active != "wiki/concepts/c.md" {
		t.Errorf("active = %q, want c.md", res[0].Active)
	}
	if want := []string{"wiki/concepts/b.md", "wiki/concepts/c.md"}; !reflect.DeepEqual(res[0].Chain, want) {
		t.Errorf("chain = %v, want %v", res[0].Chain, want)
	}
}

func TestResolveMultipleSeedsIndependently(t *testing.T) {
	recs := records(t, map[string]string{
		"wiki/concepts/old.md":     page("Old", ""),
		"wiki/concepts/new.md":     page("New", "old.md"),
		"wiki/concepts/current.md": page("Current", ""),
	})
	res := Resolve([]string{"wiki/concepts/old.md", "wiki/concepts/current.md"}, recs)
	bySeed := map[string]Resolution{}
	for _, r := range res {
		bySeed[r.Seed] = r
	}
	if bySeed["wiki/concepts/old.md"].Active != "wiki/concepts/new.md" {
		t.Errorf("old.md active = %q", bySeed["wiki/concepts/old.md"].Active)
	}
	if bySeed["wiki/concepts/current.md"].Active != "wiki/concepts/current.md" {
		t.Errorf("current.md active = %q", bySeed["wiki/concepts/current.md"].Active)
	}
}

func TestResolveSeedMissingFromVaultResolvesToItself(t *testing.T) {
	recs := records(t, map[string]string{"wiki/concepts/a.md": page("A", "")})
	res := Resolve([]string{"wiki/concepts/gone.md"}, recs)
	want := []Resolution{{Seed: "wiki/concepts/gone.md", Active: "wiki/concepts/gone.md", Chain: []string{}}}
	if !reflect.DeepEqual(res, want) {
		t.Errorf("Resolve = %v, want %v", res, want)
	}
}

func TestResolveSupersedesCycleDoesNotInfiniteLoop(t *testing.T) {
	recs := records(t, map[string]string{
		"wiki/concepts/a.md": page("A", "b.md"),
		"wiki/concepts/b.md": page("B", "a.md"),
	})
	res := Resolve([]string{"wiki/concepts/a.md"}, recs)
	if res[0].Active != "wiki/concepts/a.md" && res[0].Active != "wiki/concepts/b.md" {
		t.Errorf("active = %q, want one of the cycle members", res[0].Active)
	}
}
