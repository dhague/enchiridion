package ingest

import (
	"errors"
	"fmt"
	gopath "path"
	"slices"
	"strings"

	"github.com/dhague/enchiridion/enchiridion-go/internal/chainofevidence"
	"github.com/dhague/enchiridion/enchiridion-go/internal/commit"
	"github.com/dhague/enchiridion/enchiridion-go/internal/place"
	"github.com/dhague/enchiridion/enchiridion-go/internal/vault"
	"github.com/dhague/enchiridion/enchiridion-go/internal/wikipage"
)

// MaxPathLength caps a full path (vault root plus vault-relative path), for
// Windows' 255-char limit (#70).
const MaxPathLength = 255

// The two verbs a plan page may carry. Named because placement, frontmatter
// projection, both validation passes and execution each branch on them, and
// a typo in one of those literals would be a silently wrong branch rather
// than a compile error.
const (
	opCreate = "create"
	opUpdate = "update"
)

// ErrPlan is returned when a plan fails shape or semantic validation. The
// wrapped message lists every problem found, not just the first.
var ErrPlan = errors.New("invalid plan")

// ResolvedPage is one plan page, resolved to the exact file the vault will
// hold.
//
// PageRef is "" and Page is nil together, when placement couldn't be computed
// (an invalid Kind, a missing PageRef) — its own shape error, reported by
// [Resolved.Validate].
type ResolvedPage struct {
	Plan    PagePlan
	PageRef string
	// Page is the full post-write content: projected frontmatter plus body. A
	// create starts blank, an update from its on-disk copy, so a re-ingest's
	// existing edges and the fresh plan's edges are both visible at once.
	Page *wikipage.Page
	// Occupied records whether anything was already at PageRef when the plan
	// was resolved — a create may not claim it. A *directory* counts, which
	// is why this is not the same question as Loaded.
	Occupied bool
	// Loaded records whether an existing page was read as this page's base.
	// An update needs one; a create never has one.
	Loaded bool
}

// Op is this page's plan verb, `create` or `update`.
func (p ResolvedPage) Op() string { return p.Plan.Op }

// Resolved is a plan with every derived fact computed exactly once.
//
// Constructible directly (no vault needed) for tests; [Resolve] is the
// production path.
type Resolved struct {
	Plan  Plan
	Pages []ResolvedPage
	// Root is "" when resolved without a vault — shape checks only, no reads.
	Root string
	// ExtraKindFolders is {kind: folder} for vault-discovered kind-folders
	// beyond the four canonical ones; empty when resolved without a vault.
	ExtraKindFolders map[string]string
}

// vault returns a handle on the vault this plan resolved against, or nil when
// it resolved without one.
//
// A [vault.Vault] is just a pinned root, so minting one per use costs
// nothing and beats carrying a field that a directly-constructed Resolved
// (the test path) would leave nil.
func (r *Resolved) vault() *vault.Vault {
	if r.Root == "" {
		return nil
	}
	return vault.New(r.Root)
}

// Resolve turns plan into the exact pages the vault will hold.
//
// Pure apart from vault reads: placement, frontmatter projection and link
// composition each happen here and nowhere else, so validation and execution
// read the same facts by construction rather than by convention. Pass root ==
// "" to resolve without a vault, for shape checks alone.
func Resolve(plan Plan, root string) (*Resolved, error) {
	var v *vault.Vault
	extraKindFolders := map[string]string{}
	if root != "" {
		v = vault.New(root)
		// Refuse an unmigrated vault outright rather than filing pages into
		// the plural folders while the old ones sit in the singular — see
		// [vault.Vault.LegacyKindFolders].
		legacy, err := v.LegacyKindFolders()
		if err != nil {
			return nil, err
		}
		if len(legacy) > 0 {
			// The #114 migration script this used to name is retired, so the
			// remedy is spelled out here instead: one `git mv` per folder,
			// which is all that script ever did on a vault without
			// collisions.
			moves := make([]string, 0, len(legacy))
			for _, kind := range legacy {
				moves = append(moves, fmt.Sprintf("git mv wiki/%s/* wiki/%ss/", kind, kind))
			}
			return nil, fmt.Errorf(
				"%s holds pre-ADR-0008 kind-folders (wiki/%s); move their pages into the "+
					"plural folders before ingesting (%s), then remove the empty singular "+
					"folders. Merge by hand where both spellings hold the same filename",
				root, strings.Join(legacy, ", wiki/"), strings.Join(moves, "; "))
		}
		discovered, err := v.DiscoveredKinds()
		if err != nil {
			return nil, err
		}
		extraKindFolders = discovered
	}

	refs := make([]string, len(plan.Pages))
	for i, page := range plan.Pages {
		refs[i] = pageRef(page, extraKindFolders)
	}

	// First page wins, so a link's title matches the earliest plan page
	// claiming that pageRef.
	titles := map[string]string{}
	for i, ref := range refs {
		if ref == "" {
			continue
		}
		if _, seen := titles[ref]; !seen {
			titles[ref] = plan.Pages[i].Title
		}
	}

	resolved := &Resolved{Plan: plan, Root: root, ExtraKindFolders: extraKindFolders}
	for i, planPage := range plan.Pages {
		pageRef := refs[i]
		if pageRef == "" {
			resolved.Pages = append(resolved.Pages, ResolvedPage{Plan: planPage})
			continue
		}

		base := wikipage.Page{}
		loaded := false
		if planPage.Op == opUpdate && v != nil && v.Exists(pageRef) {
			existing, err := v.Load(pageRef)
			if err != nil {
				return nil, err
			}
			base, loaded = existing, true
		}

		page, err := applyFrontmatter(base, planPage, gopath.Dir(pageRef), plan, titles, v)
		if err != nil {
			return nil, fmt.Errorf("pages[%d] (%s): %w", i, planPage.Title, err)
		}
		page = applyBody(page, planPage.Body)

		resolved.Pages = append(resolved.Pages, ResolvedPage{
			Plan:     planPage,
			PageRef:  pageRef,
			Page:     &page,
			Occupied: v != nil && v.Occupied(pageRef),
			Loaded:   loaded,
		})
	}
	return resolved, nil
}

// pageRef is the vault-relative path a plan page will occupy, or "" when it
// can't be computed yet (an invalid kind or a missing pageRef, each already
// recorded as its own shape error).
//
// The **only** caller of [place.Path] in this package — every other consumer
// reads [ResolvedPage.PageRef].
func pageRef(page PagePlan, extraKindFolders map[string]string) string {
	if page.Op != opCreate {
		return page.PageRef
	}
	if page.Title == "" {
		return ""
	}
	ref, err := place.Path(page.Kind, page.Title, extraKindFolders)
	if err != nil {
		return ""
	}
	return ref
}

// resolveTitle is the title a link to targetRef should carry.
//
// This plan's own page for that pageRef wins (titles), so an update that
// corrects a title propagates to every link the same plan writes. Then the
// on-disk title; then the basename, reachable only if validation let an
// unresolvable target through.
func resolveTitle(targetRef string, titles map[string]string, v *vault.Vault) string {
	targetRef = gopath.Clean(targetRef)
	if title, ok := titles[targetRef]; ok {
		return title
	}
	if v != nil && v.Exists(targetRef) {
		if page, err := v.Load(targetRef); err == nil {
			if title, err := page.GetString("title"); err == nil && title != "" {
				return title
			}
		}
	}
	return gopath.Base(targetRef)
}

// applyFrontmatter is the **only** frontmatter projection in this package —
// see [Resolve].
func applyFrontmatter(
	page wikipage.Page,
	planPage PagePlan,
	pageDir string,
	plan Plan,
	titles map[string]string,
	v *vault.Vault,
) (wikipage.Page, error) {
	page, err := page.Set("title", planPage.Title)
	if err != nil {
		return page, err
	}

	merging := planPage.Op == opUpdate
	for key, value := range planPage.Frontmatter.All {
		if key == "raw_source" && value == true {
			if plan.Raw == "" {
				// Nothing to point at; validate reports it as a shape error.
				continue
			}
			value = wikipage.ComposeLink(gopath.Base(plan.Raw), plan.Raw, pageDir)
		}
		if list, isList := value.([]any); merging && isList {
			page, err = page.Merge(key, list)
		} else {
			page, err = page.Set(key, value)
		}
		if err != nil {
			return page, err
		}
	}

	for key, refs := range planPage.Edges.All {
		links := make([]string, len(refs))
		for i, ref := range refs {
			links[i] = wikipage.ComposeLink(resolveTitle(ref, titles, v), ref, pageDir)
		}
		if merging {
			page, err = page.MergeStrings(key, links)
		} else {
			page, err = page.Set(key, links)
		}
		if err != nil {
			return page, err
		}
	}
	return page, nil
}

// applyBody replaces the body while leaving the frontmatter block byte-exact,
// re-encoding the new body's link destinations on the way in.
func applyBody(page wikipage.Page, newBody *string) wikipage.Page {
	if newBody == nil {
		return page
	}
	_, _, offset, _ := wikipage.SplitFrontmatter(page.Text)
	return wikipage.Page{Text: page.Text[:offset] + wikipage.NormalizeBodyLinks(*newBody)}
}

// Validate checks this plan, shape then semantic, before any write. The
// returned error wraps [ErrPlan] and names every problem found.
func (r *Resolved) Validate() error {
	problems := append(r.shapeErrors(), r.semanticErrors()...)
	if len(problems) > 0 {
		return fmt.Errorf("%w: %s", ErrPlan, strings.Join(problems, "; "))
	}
	return nil
}

// shapeErrors covers required fields and valid ops — everything checkable
// without a vault.
func (r *Resolved) shapeErrors() []string {
	var problems []string
	if r.Plan.Title == "" {
		problems = append(problems, "plan.title is required")
	}
	if len(r.Plan.Pages) == 0 {
		problems = append(problems, "plan.pages must contain at least one page")
	}

	for i, rp := range r.Pages {
		page := rp.Plan
		prefix := fmt.Sprintf("pages[%d]", i)

		if page.Op != opCreate && page.Op != opUpdate {
			problems = append(problems,
				fmt.Sprintf("%s.op must be 'create' or 'update', got %q", prefix, page.Op))
			continue
		}
		if page.Title == "" {
			problems = append(problems, prefix+".title is required")
		}

		if page.Op == opCreate {
			if page.PageRef != "" {
				problems = append(problems, prefix+".page_ref must not be set for op=create")
			}
			switch {
			case page.Kind == "":
				problems = append(problems, prefix+".kind is required for op=create")
			case !slices.Contains(place.Kinds, page.Kind):
				if _, ok := r.ExtraKindFolders[page.Kind]; !ok {
					problems = append(problems,
						fmt.Sprintf("%s.kind %q is not a valid kind", prefix, page.Kind))
				}
			}
			if page.Body == nil {
				problems = append(problems, prefix+".body is required for op=create")
			}
		} else {
			if page.Kind != "" {
				problems = append(problems, prefix+".kind must not be set for op=update")
			}
			if page.PageRef == "" {
				problems = append(problems, prefix+".page_ref is required for op=update")
			}
		}

		// An explicit null reads as absent, matching Python's
		// `frontmatter.get("raw_source") is not None` — a plan valid there
		// must not be rejected here.
		if rawSource, present := page.Frontmatter.Get("raw_source"); present && rawSource != nil {
			switch {
			case rawSource != true:
				problems = append(problems, fmt.Sprintf(
					"%s.frontmatter.raw_source must be true (derived from plan.raw), got %v",
					prefix, rawSource))
			case r.Plan.Raw == "":
				problems = append(problems,
					prefix+".frontmatter.raw_source is true but plan.raw is not set")
			}
		}
	}
	return problems
}

// semanticErrors covers the checks that need the vault: target existence,
// path length, evidence chain.
func (r *Resolved) semanticErrors() []string {
	if r.Root == "" {
		return nil
	}
	v := r.vault()
	var problems []string

	// A page this same plan is about to create counts as resolvable too, so
	// sibling new pages can link to each other before either exists on disk.
	prospective := map[string]bool{}
	for _, rp := range r.Pages {
		if rp.Op() == opCreate && rp.PageRef != "" {
			prospective[rp.PageRef] = true
		}
	}

	for i, rp := range r.Pages {
		page := rp.Plan
		prefix := fmt.Sprintf("pages[%d]", i)
		if page.Op != opCreate && page.Op != opUpdate {
			continue
		}

		switch {
		case page.Op == opCreate && rp.PageRef != "":
			if rp.Occupied {
				problems = append(problems,
					fmt.Sprintf("%s: create target %s already exists", prefix, rp.PageRef))
			}
			full := v.Path(rp.PageRef)
			if len(full) > MaxPathLength {
				problems = append(problems, fmt.Sprintf(
					"%s: path %s exceeds %d chars (%d chars with vault root)",
					prefix, rp.PageRef, MaxPathLength, len(full)))
			}
		case page.Op == opUpdate && rp.PageRef != "" && !rp.Loaded:
			problems = append(problems,
				fmt.Sprintf("%s.page_ref %s does not exist", prefix, rp.PageRef))
		}

		for _, target := range pageLinkTargets(page, r.Plan) {
			if prospective[target.ref] {
				continue
			}
			// Exists is file-only, so a target naming a directory fails here
			// rather than composing a link to something unopenable.
			if !v.Exists(target.ref) {
				problems = append(problems, fmt.Sprintf(
					"%s: %s target %q does not resolve to a real page",
					prefix, target.key, target.ref))
			}
		}
	}

	if r.Plan.Raw != "" {
		// A courtesy check for the agent; commit re-runs it as the hard gate.
		staged := map[string]wikipage.Page{}
		for _, rp := range r.Pages {
			if rp.PageRef != "" && rp.Page != nil {
				staged[rp.PageRef] = *rp.Page
			}
		}
		found, err := chainofevidence.Check(staged, r.Plan.Raw)
		if err != nil {
			problems = append(problems, err.Error())
		}
		problems = append(problems, found...)
	}
	return problems
}

// linkTarget is one (edge key, normalized target pageRef) pair awaiting
// existence validation.
type linkTarget struct {
	key string
	ref string
}

// pageLinkTargets returns the targets this page's plan asks to link to.
// Plans name targets by vault-relative page reference only, so this is a
// plain normalize — no markdown-link parsing.
func pageLinkTargets(page PagePlan, plan Plan) []linkTarget {
	var targets []linkTarget
	if rawSource, ok := page.Frontmatter.Get("raw_source"); ok && rawSource == true && plan.Raw != "" {
		targets = append(targets, linkTarget{"raw_source", gopath.Clean(plan.Raw)})
	}
	for key, refs := range page.Edges.All {
		for _, ref := range refs {
			targets = append(targets, linkTarget{key, gopath.Clean(ref)})
		}
	}
	return targets
}

// Execute writes every resolved page and commits, returning the commit SHA.
//
// Assumes [Resolved.Validate] has already passed. No rollback on failure —
// see the package comment. git is injectable for tests; pass a
// [vaultgit.Repo] over the vault root in production.
func (r *Resolved) Execute(git commit.Git) (string, error) {
	if r.Root == "" {
		return "", fmt.Errorf("%w: cannot execute a plan resolved without a vault root", ErrPlan)
	}
	v := r.vault()

	var created, updated []string
	var superseded []commit.Supersession

	for _, resolved := range r.Pages {
		if resolved.PageRef == "" || resolved.Page == nil {
			return "", fmt.Errorf("%w: page %q was not resolved", ErrPlan, resolved.Plan.Title)
		}
		if err := v.Write(resolved.PageRef, *resolved.Page); err != nil {
			return "", err
		}
		if resolved.Op() != opCreate {
			updated = append(updated, resolved.PageRef)
			continue
		}
		created = append(created, resolved.PageRef)
		if targets, ok := resolved.Plan.Edges.Get("supersedes"); ok {
			for _, target := range targets {
				superseded = append(superseded,
					commit.Supersession{Old: gopath.Clean(target), New: resolved.PageRef})
			}
		}
	}

	return commit.Commit(r.Root, commit.Manifest{
		Title:      r.Plan.Title,
		Action:     r.Plan.Action,
		Created:    created,
		Updated:    updated,
		Superseded: superseded,
		SourceDate: r.Plan.SourceDate,
		RawSource:  r.Plan.Raw,
	}, git)
}

// Describe is a human-readable summary of what [Resolved.Execute] would
// write.
func (r *Resolved) Describe() string {
	lines := []string{r.Plan.Action + ": " + r.Plan.Title}
	for _, resolved := range r.Pages {
		lines = append(lines, fmt.Sprintf("  %-6s %s", resolved.Op(), resolved.PageRef))
	}
	return strings.Join(lines, "\n")
}
