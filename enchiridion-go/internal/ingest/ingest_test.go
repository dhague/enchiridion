package ingest

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/dhague/enchiridion/enchiridion-go/internal/vault"
	"github.com/dhague/enchiridion/enchiridion-go/internal/vaultgit/vaultgittest"
)

// newVault lays down a {pageRef: text} map under a fresh temp root.
func newVault(t *testing.T, files map[string]string) string {
	t.Helper()
	root := t.TempDir()
	for ref, text := range files {
		path := filepath.Join(root, filepath.FromSlash(ref))
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func decodePlan(t *testing.T, src string) Plan {
	t.Helper()
	plan, err := DecodePlan(strings.NewReader(src))
	if err != nil {
		t.Fatalf("DecodePlan: %v", err)
	}
	return plan
}

// resolveOK resolves against root and fails the test if resolution errors.
func resolveOK(t *testing.T, plan Plan, root string) *Resolved {
	t.Helper()
	resolved, err := Resolve(plan, root)
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	return resolved
}

// --- decoding ---------------------------------------------------------------

func TestDecodePlanDefaultsAction(t *testing.T) {
	if got := decodePlan(t, `{"title":"T"}`).Action; got != "ingest" {
		t.Errorf("Action = %q, want \"ingest\"", got)
	}
}

func TestDecodePlanKeepsExplicitAction(t *testing.T) {
	if got := decodePlan(t, `{"title":"T","action":"synthesize"}`).Action; got != "synthesize" {
		t.Errorf("Action = %q, want \"synthesize\"", got)
	}
}

// Frontmatter keys are applied in plan order, so a Go map (randomised
// iteration) would make the written page vary run to run. ADR-0012 relaxes
// byte-identical round-tripping; it does not license nondeterminism.
func TestFrontmatterKeyOrderIsPreserved(t *testing.T) {
	const src = `{"title":"T","pages":[{"op":"create","title":"A","kind":"concept","body":"b",
	  "frontmatter":{"summary":"s","volatility":"stable","tags":["x"],"source_date":"2026-01-01"}}]}`
	root := newVault(t, nil)
	for range 8 {
		resolved := resolveOK(t, decodePlan(t, src), root)
		text := resolved.Pages[0].Page.Text
		want := "---\ntitle: A\nsummary: s\nvolatility: stable\ntags:\n  - x\nsource_date: \"2026-01-01\"\n---\nb"
		if text != want {
			t.Fatalf("frontmatter =\n%q\nwant\n%q", text, want)
		}
	}
}

func TestDecodePlanRejectsMalformedJSON(t *testing.T) {
	if _, err := DecodePlan(strings.NewReader(`{"title":`)); err == nil {
		t.Error("DecodePlan on truncated JSON: want an error, got nil")
	}
}

func TestOrderedMapNullDecodesEmpty(t *testing.T) {
	plan := decodePlan(t, `{"title":"T","pages":[{"op":"create","title":"A","frontmatter":null}]}`)
	if plan.Pages[0].Frontmatter.Len() != 0 {
		t.Errorf("null frontmatter = %v, want empty", plan.Pages[0].Frontmatter.Keys)
	}
}

// --- placement + link composition -------------------------------------------

func TestResolvePlacesCreatesByKindAndSlug(t *testing.T) {
	plan := decodePlan(t, `{"title":"T","pages":[
	  {"op":"create","title":"Prepared Statements","kind":"concept","body":"b"},
	  {"op":"create","title":"Acme Corp","kind":"entity","body":"b"}]}`)
	resolved := resolveOK(t, plan, newVault(t, nil))

	want := []string{"wiki/concepts/prepared-statements.md", "wiki/entities/acme-corp.md"}
	for i, ref := range want {
		if resolved.Pages[i].PageRef != ref {
			t.Errorf("pages[%d].PageRef = %q, want %q", i, resolved.Pages[i].PageRef, ref)
		}
	}
}

func TestResolveComposesEdgeLinksFromVaultTitles(t *testing.T) {
	root := newVault(t, map[string]string{
		"wiki/concepts/existing.md": "---\ntitle: The Existing Page\n---\nbody\n",
	})
	plan := decodePlan(t, `{"title":"T","pages":[
	  {"op":"create","title":"New","kind":"synthesis","body":"b",
	   "edges":{"refines":["wiki/concepts/existing.md"]}}]}`)
	resolved := resolveOK(t, plan, root)

	want := "  - \"[The Existing Page](../concepts/existing.md)\""
	if !strings.Contains(resolved.Pages[0].Page.Text, want) {
		t.Errorf("composed edge missing from:\n%s\nwant a line %q", resolved.Pages[0].Page.Text, want)
	}
}

// A sibling page this same plan creates supplies the link title, so two new
// pages can link to each other before either exists on disk.
func TestResolveComposesEdgeLinksFromSiblingPlanPages(t *testing.T) {
	plan := decodePlan(t, `{"title":"T","pages":[
	  {"op":"create","title":"First Page","kind":"concept","body":"b"},
	  {"op":"create","title":"Second","kind":"concept","body":"b",
	   "edges":{"related":["wiki/concepts/first-page.md"]}}]}`)
	resolved := resolveOK(t, plan, newVault(t, nil))

	if !strings.Contains(resolved.Pages[1].Page.Text, `"[First Page](first-page.md)"`) {
		t.Errorf("sibling title not used:\n%s", resolved.Pages[1].Page.Text)
	}
}

func TestResolveComposesRawSourceFromSentinel(t *testing.T) {
	root := newVault(t, map[string]string{"raw/a doc (v2).md": "raw\n"})
	plan := decodePlan(t, `{"title":"T","raw":"raw/a doc (v2).md","pages":[
	  {"op":"create","title":"Doc","kind":"source","body":"b","frontmatter":{"raw_source":true}}]}`)
	resolved := resolveOK(t, plan, root)

	want := `raw_source: "[a doc (v2).md](../../raw/a%20doc%20%28v2%29.md)"`
	if !strings.Contains(resolved.Pages[0].Page.Text, want) {
		t.Errorf("raw_source =\n%s\nwant a line %q", resolved.Pages[0].Page.Text, want)
	}
}

func TestResolveNormalizesBodyLinks(t *testing.T) {
	plan := decodePlan(t, `{"title":"T","pages":[
	  {"op":"create","title":"A","kind":"concept","body":"See [x](../../raw/spec(v2).md).\n"}]}`)
	resolved := resolveOK(t, plan, newVault(t, nil))

	if !strings.Contains(resolved.Pages[0].Page.Text, "spec%28v2%29.md") {
		t.Errorf("body link not re-encoded:\n%s", resolved.Pages[0].Page.Text)
	}
}

// An update starts from the on-disk page, so a re-ingest's existing edges and
// the fresh plan's edges are both present afterwards.
func TestUpdateMergesListValuedKeysOntoDiskState(t *testing.T) {
	root := newVault(t, map[string]string{
		"wiki/concepts/a.md": "---\ntitle: A\ntags:\n  - old\n" +
			"related:\n  - \"[B](b.md)\"\n---\nold body\n",
		"wiki/concepts/b.md": "---\ntitle: B\n---\nb\n",
		"wiki/concepts/c.md": "---\ntitle: C\n---\nc\n",
	})
	plan := decodePlan(t, `{"title":"T","pages":[
	  {"op":"update","title":"A","page_ref":"wiki/concepts/a.md",
	   "frontmatter":{"tags":["new"]},"edges":{"related":["wiki/concepts/c.md"]}}]}`)
	resolved := resolveOK(t, plan, root)

	text := resolved.Pages[0].Page.Text
	for _, want := range []string{"- old", "- new", `"[B](b.md)"`, `"[C](c.md)"`} {
		if !strings.Contains(text, want) {
			t.Errorf("update lost %q:\n%s", want, text)
		}
	}
	if !strings.Contains(text, "old body") {
		t.Errorf("an update omitting body should keep the on-disk body:\n%s", text)
	}
}

func TestUpdateReplacesBodyWhenGiven(t *testing.T) {
	root := newVault(t, map[string]string{"wiki/concepts/a.md": "---\ntitle: A\n---\nold body\n"})
	plan := decodePlan(t, `{"title":"T","pages":[
	  {"op":"update","title":"A","page_ref":"wiki/concepts/a.md","body":"new body\n"}]}`)
	resolved := resolveOK(t, plan, root)

	text := resolved.Pages[0].Page.Text
	if strings.Contains(text, "old body") || !strings.Contains(text, "new body") {
		t.Errorf("body not replaced:\n%s", text)
	}
}

func TestCreateAcceptsVaultDiscoveredKind(t *testing.T) {
	root := newVault(t, map[string]string{"wiki/decisions/.keep": ""})
	plan := decodePlan(t, `{"title":"T","pages":[
	  {"op":"create","title":"Use Go","kind":"decision","body":"b"}]}`)
	resolved := resolveOK(t, plan, root)

	if got := resolved.Pages[0].PageRef; got != "wiki/decisions/use-go.md" {
		t.Errorf("PageRef = %q, want wiki/decisions/use-go.md", got)
	}
	if err := resolved.Validate(); err != nil {
		t.Errorf("Validate: %v", err)
	}
}

// --- shape validation -------------------------------------------------------

// validationErrors resolves and validates, returning the error text.
func validationErrors(t *testing.T, src, root string) string {
	t.Helper()
	err := resolveOK(t, decodePlan(t, src), root).Validate()
	if err == nil {
		return ""
	}
	if !errors.Is(err, ErrPlan) {
		t.Fatalf("Validate returned %v, want an ErrPlan", err)
	}
	return err.Error()
}

func TestShapeValidationReportsEveryProblemAtOnce(t *testing.T) {
	got := validationErrors(t, `{"pages":[{"op":"create"}]}`, "")
	for _, want := range []string{
		"plan.title is required",
		"pages[0].title is required",
		"pages[0].kind is required for op=create",
		"pages[0].body is required for op=create",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("missing %q in: %s", want, got)
		}
	}
}

func TestShapeValidationEmptyPlan(t *testing.T) {
	if got := validationErrors(t, `{"title":"T"}`, ""); !strings.Contains(got, "at least one page") {
		t.Errorf("got %q", got)
	}
}

func TestShapeValidationRejectsBadOp(t *testing.T) {
	got := validationErrors(t, `{"title":"T","pages":[{"op":"delete","title":"A"}]}`, "")
	if !strings.Contains(got, `pages[0].op must be 'create' or 'update', got "delete"`) {
		t.Errorf("got %q", got)
	}
}

func TestShapeValidationRejectsPageRefOnCreate(t *testing.T) {
	got := validationErrors(t, `{"title":"T","pages":[
	  {"op":"create","title":"A","kind":"concept","body":"b","page_ref":"wiki/concepts/a.md"}]}`, "")
	if !strings.Contains(got, "page_ref must not be set for op=create") {
		t.Errorf("got %q", got)
	}
}

func TestShapeValidationRejectsKindOnUpdate(t *testing.T) {
	got := validationErrors(t, `{"title":"T","pages":[
	  {"op":"update","title":"A","page_ref":"wiki/concepts/a.md","kind":"concept"}]}`, "")
	if !strings.Contains(got, "kind must not be set for op=update") {
		t.Errorf("got %q", got)
	}
}

func TestShapeValidationRejectsUnknownKind(t *testing.T) {
	got := validationErrors(t, `{"title":"T","pages":[
	  {"op":"create","title":"A","kind":"nonsense","body":"b"}]}`, "")
	if !strings.Contains(got, `pages[0].kind "nonsense" is not a valid kind`) {
		t.Errorf("got %q", got)
	}
}

func TestShapeValidationRejectsNonTrueRawSource(t *testing.T) {
	got := validationErrors(t, `{"title":"T","raw":"raw/d.md","pages":[
	  {"op":"create","title":"A","kind":"source","body":"b","frontmatter":{"raw_source":"x"}}]}`, "")
	if !strings.Contains(got, "raw_source must be true (derived from plan.raw)") {
		t.Errorf("got %q", got)
	}
}

func TestShapeValidationRejectsRawSourceWithoutPlanRaw(t *testing.T) {
	got := validationErrors(t, `{"title":"T","pages":[
	  {"op":"create","title":"A","kind":"source","body":"b","frontmatter":{"raw_source":true}}]}`, "")
	if !strings.Contains(got, "raw_source is true but plan.raw is not set") {
		t.Errorf("got %q", got)
	}
}

// --- semantic validation ----------------------------------------------------

func TestSemanticValidationRejectsExistingCreateTarget(t *testing.T) {
	root := newVault(t, map[string]string{"wiki/concepts/a.md": "---\ntitle: A\n---\nb\n"})
	got := validationErrors(t, `{"title":"T","pages":[
	  {"op":"create","title":"A","kind":"concept","body":"b"}]}`, root)
	if !strings.Contains(got, "create target wiki/concepts/a.md already exists") {
		t.Errorf("got %q", got)
	}
}

func TestSemanticValidationRejectsMissingUpdateTarget(t *testing.T) {
	got := validationErrors(t, `{"title":"T","pages":[
	  {"op":"update","title":"A","page_ref":"wiki/concepts/gone.md"}]}`, newVault(t, nil))
	if !strings.Contains(got, "page_ref wiki/concepts/gone.md does not exist") {
		t.Errorf("got %q", got)
	}
}

func TestSemanticValidationRejectsUnresolvableEdgeTarget(t *testing.T) {
	got := validationErrors(t, `{"title":"T","pages":[
	  {"op":"create","title":"A","kind":"concept","body":"b",
	   "edges":{"related":["wiki/concepts/nowhere.md"]}}]}`, newVault(t, nil))
	if !strings.Contains(got, `related target "wiki/concepts/nowhere.md" does not resolve`) {
		t.Errorf("got %q", got)
	}
}

func TestSemanticValidationAcceptsSiblingCreateAsEdgeTarget(t *testing.T) {
	got := validationErrors(t, `{"title":"T","pages":[
	  {"op":"create","title":"First Page","kind":"concept","body":"b"},
	  {"op":"create","title":"Second","kind":"concept","body":"b",
	   "edges":{"related":["wiki/concepts/first-page.md"]}}]}`, newVault(t, nil))
	if got != "" {
		t.Errorf("sibling create should resolve, got %q", got)
	}
}

func TestSemanticValidationEnforcesPathLength(t *testing.T) {
	root := newVault(t, nil)
	longTitle := strings.Repeat("x", 60)
	got := validationErrors(t, `{"title":"T","pages":[
	  {"op":"create","title":"`+longTitle+`","kind":"concept","body":"b"}]}`,
		filepath.Join(root, strings.Repeat("d", 150)))
	if !strings.Contains(got, "exceeds 255 chars") {
		t.Errorf("got %q", got)
	}
}

// The chain-of-evidence pre-flight is a courtesy for the agent; commit runs
// the same check as the hard gate.
func TestSemanticValidationRunsChainOfEvidence(t *testing.T) {
	root := newVault(t, map[string]string{"raw/doc.md": "raw\n"})
	got := validationErrors(t, `{"title":"T","raw":"raw/doc.md","pages":[
	  {"op":"create","title":"A","kind":"concept","body":"b"}]}`, root)
	if !strings.Contains(got, "needs a sources/ page whose raw_source points at it") {
		t.Errorf("got %q", got)
	}
}

func TestValidateWithoutVaultSkipsSemanticChecks(t *testing.T) {
	got := validationErrors(t, `{"title":"T","pages":[
	  {"op":"update","title":"A","page_ref":"wiki/concepts/gone.md"}]}`, "")
	if got != "" {
		t.Errorf("a vault-less resolve should run shape checks only, got %q", got)
	}
}

// --- execution --------------------------------------------------------------

const wholeIngestPlan = `{
  "title":"Deploy notes","action":"ingest","source_date":"2026-03-01","raw":"raw/doc.md",
  "pages":[
    {"op":"create","title":"Doc","kind":"source","body":"stub body\n",
     "frontmatter":{"summary":"the doc","raw_source":true}},
    {"op":"create","title":"Prepared Statements","kind":"concept","body":"page body\n",
     "frontmatter":{"summary":"s","volatility":"stable"},
     "edges":{"source":["wiki/sources/doc.md"],"supersedes":["wiki/concepts/old.md"]}}]}`

func TestExecuteWritesPagesAndCommits(t *testing.T) {
	root := newVault(t, map[string]string{
		"raw/doc.md":           "raw\n",
		"wiki/concepts/old.md": "---\ntitle: Old\n---\nold\n",
	})
	resolved := resolveOK(t, decodePlan(t, wholeIngestPlan), root)
	if err := resolved.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}

	git := &vaultgittest.Fake{}
	sha, err := resolved.Execute(git)
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if len(sha) != 40 {
		t.Errorf("SHA = %q", sha)
	}

	v := vault.New(root)
	for _, ref := range []string{"wiki/sources/doc.md", "wiki/concepts/prepared-statements.md"} {
		if !v.Exists(ref) {
			t.Errorf("%s was not written", ref)
		}
	}

	message := git.Messages[0]
	for _, want := range []string{
		"ingest: Deploy notes",
		"created: wiki/sources/doc.md",
		"created: wiki/concepts/prepared-statements.md",
		"superseded: wiki/concepts/old.md -> wiki/concepts/prepared-statements.md",
		"source-date: 2026-03-01",
	} {
		if !strings.Contains(message, want) {
			t.Errorf("commit message missing %q:\n%s", want, message)
		}
	}
	if !strings.Contains(strings.Join(git.Added, ","), "raw/doc.md") {
		t.Errorf("the raw artifact was not staged: %v", git.Added)
	}
}

// Re-running a plan after fixing its cause must be safe — every write is
// idempotent, which is what lets this package skip rollback entirely.
func TestExecuteIsIdempotent(t *testing.T) {
	root := newVault(t, map[string]string{
		"raw/doc.md":           "raw\n",
		"wiki/concepts/old.md": "---\ntitle: Old\n---\nold\n",
	})
	plan := decodePlan(t, wholeIngestPlan)

	first := resolveOK(t, plan, root)
	if err := first.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}
	if _, err := first.Execute(&vaultgittest.Fake{}); err != nil {
		t.Fatalf("Execute: %v", err)
	}
	before := readAll(t, root)

	// The second run's creates now collide, so re-executing the resolved plan
	// directly is the rerun-after-fix path: same bytes out.
	if _, err := first.Execute(&vaultgittest.Fake{}); err != nil {
		t.Fatalf("second Execute: %v", err)
	}
	after := readAll(t, root)
	for ref, text := range before {
		if after[ref] != text {
			t.Errorf("%s changed on re-execute:\n%q\n%q", ref, text, after[ref])
		}
	}
}

func TestExecuteWithoutRootIsAnError(t *testing.T) {
	resolved := resolveOK(t, decodePlan(t, `{"title":"T","pages":[]}`), "")
	if _, err := resolved.Execute(&vaultgittest.Fake{}); !errors.Is(err, ErrPlan) {
		t.Errorf("Execute = %v, want an ErrPlan", err)
	}
}

// A page that never resolved must stop execution rather than being silently
// skipped — Validate would have caught it, so reaching here is a caller bug.
func TestExecuteRefusesUnresolvedPage(t *testing.T) {
	resolved := resolveOK(t, decodePlan(t,
		`{"title":"T","pages":[{"op":"create","title":"A","kind":"nonsense","body":"b"}]}`),
		newVault(t, nil))
	if _, err := resolved.Execute(&vaultgittest.Fake{}); !errors.Is(err, ErrPlan) {
		t.Errorf("Execute = %v, want an ErrPlan", err)
	}
}

func TestExecuteUpdateRecordsUpdatedNotCreated(t *testing.T) {
	root := newVault(t, map[string]string{"wiki/concepts/a.md": "---\ntitle: A\n---\nbody\n"})
	resolved := resolveOK(t, decodePlan(t, `{"title":"T","pages":[
	  {"op":"update","title":"A","page_ref":"wiki/concepts/a.md","body":"new\n"}]}`), root)
	if err := resolved.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}
	git := &vaultgittest.Fake{}
	if _, err := resolved.Execute(git); err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if !strings.Contains(git.Messages[0], "updated: wiki/concepts/a.md") ||
		strings.Contains(git.Messages[0], "created:") {
		t.Errorf("commit message = %q", git.Messages[0])
	}
}

// A wiki-retrieval synthesis save is the same shape with no raw artifact, and
// commits under its own verb so history distinguishes it without a diff.
func TestExecuteSynthesisSave(t *testing.T) {
	root := newVault(t, map[string]string{"wiki/concepts/a.md": "---\ntitle: A\n---\nbody\n"})
	resolved := resolveOK(t, decodePlan(t, `{"title":"Q","action":"synthesize","pages":[
	  {"op":"create","title":"Answer","kind":"synthesis","body":"b",
	   "edges":{"source":["wiki/concepts/a.md"]}}]}`), root)
	if err := resolved.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}
	git := &vaultgittest.Fake{}
	if _, err := resolved.Execute(git); err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if !strings.HasPrefix(git.Messages[0], "synthesize: Q") {
		t.Errorf("commit message = %q", git.Messages[0])
	}
}

func TestDescribe(t *testing.T) {
	resolved := resolveOK(t, decodePlan(t, `{"title":"T","pages":[
	  {"op":"create","title":"A","kind":"concept","body":"b"}]}`), newVault(t, nil))
	want := "ingest: T\n  create wiki/concepts/a.md"
	if got := resolved.Describe(); got != want {
		t.Errorf("Describe = %q, want %q", got, want)
	}
}

func readAll(t *testing.T, root string) map[string]string {
	t.Helper()
	pages, err := vault.New(root).LoadWikiPages()
	if err != nil {
		t.Fatal(err)
	}
	return pages
}
