"""IngestPlan schema + single-call executor.

An :class:`IngestPlan` describes the decided outcome of an ingestion (pages
to create/update, their frontmatter and typed edges) in, a commit SHA out.
Semantic chunking and overlap classification stay judgment and stay with the
ingesting agent; everything downstream of that decision is mechanics, and
mechanics belongs in a tested script, not prose an agent re-derives every run.

A plan names link targets by **vault-relative rel only** — `edges` and
`supersedes` values are paths like `"wiki/concept/foo.md"`, never composed
`"[Title](../dest.md)"` strings. Composing the actual link — title (read
from the target, on disk or elsewhere in this same plan), `../`
relativisation, percent-encoding, and YAML quoting — is this module's job
(:func:`_compose_edges`/:func:`wikipage.compose_link`), not the authoring
agent's. `raw_source` follows the same rule via a boolean sentinel:
`frontmatter: {"raw_source": true}` marks the page as the plan's `raw`
artifact's stub, and the real link is composed from `plan.raw`. Body links
are normalised (re-encoded) the same way on write, via
:func:`wikipage.normalize_body_links`.

The pipeline, in order: validate -> per page (place -> frontmatter -> body) ->
regenerate the index -> derive a commit.Manifest from the plan -> commit.
Validation is two-phase and runs entirely before any write: shape (required
fields, valid op) then semantic (an update's `rel` exists, a create's target
doesn't yet, every edge/raw_source link resolves to a real page — either
already on disk or another page this same plan creates — and, for a plan
naming a raw artifact, the chain of evidence: a `source/` stub for that
artifact plus a `source` edge to it from every other page in the plan). This
is the agent-time layer of the chain-of-evidence check (#34 point 4): it
catches a violating plan at the agent's working point, before any write.
The real hard block lives in :mod:`commit` — every manifest that names a
`raw_source` is re-checked at commit time, so a hand-built manifest or a
future caller cannot slip a violation past :func:`validate` and into
history.

Ingestion is not the only caller: `wiki-retrieval`'s confirmed synthesis-page
save is the same shape — one `create` page of kind `synthesis`, `source`
edges to what it drew on, no raw artifact — and runs through this same
executor with `action: "synthesize"` so the commit history distinguishes the
two without reading the diff.

The raw artifact named by `plan.raw` is never renamed or moved: a file with
external identity keeps its name verbatim, forever. Ingestion only reads it
and stages it into the commit; `raw_source` links point at it where it
already sits, percent-encoded by the link machinery rather than sanitized on
disk.

There is deliberately no rollback on failure: a page written before a later
step raises is left on disk, uncommitted. Every write here is idempotent
(`WikiPage.set`/`merge` overwrite or union, `Vault.write` overwrites), so
re-running the same plan after fixing whatever failed is always safe.

CLI::

    python ingest.py --plan <path>   # executes against the resolved vault, prints the commit SHA
"""
from __future__ import annotations

import posixpath
import sys
from dataclasses import dataclass, field
from pathlib import Path

import chain_of_evidence
import commit
import place
import wikipage
from vault import Vault
from wikipage import WikiPage

#: Maximum full-path length (vault root + vault-relative path) in characters,
#: to stay under Windows' 255-char path limit (#70).
MAX_PATH_LENGTH = 255


class PlanError(ValueError):
    """Raised when a plan fails shape or semantic validation."""


@dataclass
class PagePlan:
    """One page this plan creates or updates."""

    op: str
    title: str
    body: str | None = None
    kind: str | None = None
    rel: str | None = None
    frontmatter: dict = field(default_factory=dict)
    edges: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "PagePlan":
        return cls(
            op=d.get("op", ""),
            title=d.get("title", ""),
            body=d.get("body"),
            kind=d.get("kind"),
            rel=d.get("rel"),
            frontmatter=dict(d.get("frontmatter", {})),
            edges={k: list(v) for k, v in d.get("edges", {}).items()},
        )


@dataclass
class IngestPlan:
    """The deterministic description of one ingestion's decided outcome."""

    title: str
    #: The structured commit's verb (`commit.Manifest.action`). Defaults to
    #: `ingest`; `wiki-retrieval`'s confirmed synthesis-page save passes
    #: `synthesize`, so the history can tell a researcher-saved page from an
    #: ingested one without reading the diff.
    action: str = "ingest"
    source_date: str | None = None
    raw: str | None = None
    pages: list[PagePlan] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "IngestPlan":
        return cls(
            title=d.get("title", ""),
            action=d.get("action", "ingest"),
            source_date=d.get("source_date"),
            raw=d.get("raw"),
            pages=[PagePlan.from_dict(p) for p in d.get("pages", [])],
        )


def _page_rel(page: PagePlan) -> str | None:
    """The vault-relative path this page will occupy, or ``None`` when it can't
    be computed yet (an invalid ``kind``/``rel`` already recorded as its own
    shape error)."""
    if page.op == "create":
        if page.kind not in place.KINDS or not page.title:
            return None
        return place.path(page.kind, page.title)
    return page.rel or None


def _page_dir(page: PagePlan) -> str | None:
    """The vault-relative directory this page's links resolve from."""
    rel = _page_rel(page)
    return None if rel is None else posixpath.dirname(rel)


def _resolve_title(target_rel: str, plan: IngestPlan, v: Vault) -> str:
    """The title a link to ``target_rel`` should carry.

    Prefers this same plan's own page for that rel — an update that
    corrects a title makes every link written by the same plan reflect the
    correction — then falls back to the on-disk page's own title, then the
    rel's basename as a last resort (only reachable when validation didn't
    already reject an unresolvable target).
    """
    target_rel = posixpath.normpath(target_rel)
    for p in plan.pages:
        if _page_rel(p) == target_rel:
            return p.title
    if (v.root / target_rel).is_file():
        title = v.load(target_rel).get("title")
        if title:
            return title
    return posixpath.basename(target_rel)


def _compose_raw_source(page_dir: str, plan: IngestPlan) -> str:
    """The composed ``raw_source`` link for the ``frontmatter.raw_source: true`` sentinel."""
    return wikipage.compose_link(posixpath.basename(plan.raw), plan.raw, page_dir)


def _compose_edges(
    edges: dict[str, list[str]], page_dir: str, plan: IngestPlan, v: Vault
) -> dict[str, list[str]]:
    """Compose every edge-key's vault-relative rels into markdown links."""
    return {
        key: [wikipage.compose_link(_resolve_title(rel, plan, v), rel, page_dir) for rel in rels]
        for key, rels in edges.items()
    }


def _page_link_targets(page: PagePlan, plan: IngestPlan) -> list[tuple[str, str]]:
    """``(key, normalized target rel)`` pairs this page's edges/``raw_source``
    name, for existence validation. Plans name targets by vault-relative rel
    only, so this is a direct normalize — no markdown-link parsing.
    """
    targets: list[tuple[str, str]] = []
    if page.frontmatter.get("raw_source") is True and plan.raw:
        targets.append(("raw_source", posixpath.normpath(plan.raw)))
    for key, rels in page.edges.items():
        for rel in rels:
            targets.append((key, posixpath.normpath(rel)))
    return targets


def _projected_page(page: PagePlan, plan: IngestPlan, v: Vault) -> WikiPage | None:
    """The frontmatter this page will carry once ``plan`` is executed, without
    writing anything.

    A create page starts blank; an update page starts from its on-disk copy
    (or blank, for an update naming a page the plan itself hasn't written
    yet — the shape error that's caught elsewhere), so a re-ingest's on-disk
    edges and a fresh plan's edges are both visible to the same check.
    ``None`` when the page's rel can't yet be resolved (its own shape error).
    """
    rel = _page_rel(page)
    if rel is None:
        return None
    if page.op == "create":
        base = WikiPage("")
    else:
        base = v.load(rel) if (v.root / rel).is_file() else WikiPage("")
    return _apply_frontmatter(base, page, plan, v)


def _chain_of_evidence_errors(plan: IngestPlan, root: Path) -> list[str]:
    """Check the page -> stub -> raw file chain every raw ingestion must leave
    (#34 point 4), via the shared :func:`chain_of_evidence.check`.

    ``staged`` projects each page in the plan to the frontmatter it will
    carry post-write — an update merges onto its on-disk copy, so a page
    already carrying the edge on disk need not restate it in the plan.
    """
    v = Vault(root)
    staged: dict[str, WikiPage] = {}
    for page in plan.pages:
        rel = _page_rel(page)
        if rel is None:
            continue
        projected = _projected_page(page, plan, v)
        if projected is not None:
            staged[rel] = projected
    return chain_of_evidence.check(staged, plan.raw)


def validate(plan: IngestPlan, vault_root: Path | str | None) -> None:
    """Validate ``plan``, shape then semantic, before any write.

    Raises :class:`PlanError` with every problem found (not just the first)
    on failure.
    """
    errors: list[str] = []

    if not plan.title:
        errors.append("plan.title is required")
    if not plan.pages:
        errors.append("plan.pages must contain at least one page")

    root = Path(vault_root) if vault_root is not None else None

    # A page this same plan is about to create counts as resolvable too, so
    # sibling new pages can link to each other before either exists on disk.
    prospective: set[str] = set()
    for page in plan.pages:
        if page.op == "create" and page.kind in place.KINDS and page.title:
            prospective.add(place.path(page.kind, page.title))

    for i, page in enumerate(plan.pages):
        prefix = f"pages[{i}]"

        if page.op not in ("create", "update"):
            errors.append(f"{prefix}.op must be 'create' or 'update', got {page.op!r}")
            continue
        if not page.title:
            errors.append(f"{prefix}.title is required")

        if page.op == "create":
            if page.rel is not None:
                errors.append(f"{prefix}.rel must not be set for op=create")
            if not page.kind:
                errors.append(f"{prefix}.kind is required for op=create")
            elif page.kind not in place.KINDS:
                errors.append(f"{prefix}.kind {page.kind!r} is not a valid kind")
            if page.body is None:
                errors.append(f"{prefix}.body is required for op=create")
            if root is not None and page.kind in place.KINDS and page.title:
                target = place.path(page.kind, page.title)
                if (root / target).exists():
                    errors.append(f"{prefix}: create target {target} already exists")
                full = str(root / target)
                if len(full) > MAX_PATH_LENGTH:
                    errors.append(
                        f"{prefix}: path {target} exceeds {MAX_PATH_LENGTH} chars"
                        f" ({len(full)} chars with vault root)"
                    )
        else:
            if page.kind is not None:
                errors.append(f"{prefix}.kind must not be set for op=update")
            if not page.rel:
                errors.append(f"{prefix}.rel is required for op=update")
            elif root is not None and not (root / page.rel).is_file():
                errors.append(f"{prefix}.rel {page.rel} does not exist")

        raw_source = page.frontmatter.get("raw_source")
        if raw_source is not None and raw_source is not True:
            errors.append(
                f"{prefix}.frontmatter.raw_source must be true (derived from plan.raw),"
                f" got {raw_source!r}"
            )
        elif raw_source is True and not plan.raw:
            errors.append(f"{prefix}.frontmatter.raw_source is true but plan.raw is not set")

        if root is None:
            continue

        for key, target in _page_link_targets(page, plan):
            if target in prospective:
                continue
            if not (root / target).exists():
                errors.append(f"{prefix}: {key} target {target!r} does not resolve to a real page")

    if root is not None and plan.raw:
        errors.extend(_chain_of_evidence_errors(plan, root))

    if errors:
        raise PlanError("; ".join(errors))


def _apply_frontmatter(page: WikiPage, plan_page: PagePlan, plan: IngestPlan, v: Vault) -> WikiPage:
    page_dir = _page_dir(plan_page) or ""
    page = page.set("title", plan_page.title)
    merging = plan_page.op == "update"
    for key, value in plan_page.frontmatter.items():
        if key == "raw_source" and value is True:
            value = _compose_raw_source(page_dir, plan)
        if merging and isinstance(value, list):
            page = page.merge(key, value)
        else:
            page = page.set(key, value)
    for key, links in _compose_edges(plan_page.edges, page_dir, plan, v).items():
        page = page.merge(key, links) if merging else page.set(key, links)
    return page


def _apply_body(page: WikiPage, new_body: str | None) -> WikiPage:
    if new_body is None:
        return page
    _fm, _body, offset = wikipage.split_frontmatter(page.text)
    return WikiPage(page.text[:offset] + wikipage.normalize_body_links(new_body))


def execute(vault_root: Path | str, plan: IngestPlan) -> str:
    """Execute ``plan`` against the vault at ``vault_root``. Returns the commit SHA.

    No rollback on failure: whatever was already written stays on disk,
    uncommitted, and rerunning the same plan is safe once the cause is fixed.
    """
    root = Path(vault_root)
    validate(plan, root)
    v = Vault(root)

    created: list[str] = []
    updated: list[str] = []
    superseded: list[tuple[str, str]] = []

    for plan_page in plan.pages:
        if plan_page.op == "create":
            rel = place.path(plan_page.kind, plan_page.title)
            page = _apply_frontmatter(WikiPage(""), plan_page, plan, v)
            page = WikiPage(page.text + wikipage.normalize_body_links(plan_page.body))
            v.write(rel, page)
            created.append(rel)

            for target_rel in plan_page.edges.get("supersedes", []):
                superseded.append((posixpath.normpath(target_rel), rel))
        else:
            rel = plan_page.rel
            page = v.load(rel)
            page = _apply_frontmatter(page, plan_page, plan, v)
            page = _apply_body(page, plan_page.body)
            v.write(rel, page)
            updated.append(rel)

    from build_index import write_index

    write_index(root)

    manifest = commit.Manifest(
        title=plan.title,
        action=plan.action,
        created=created,
        updated=updated,
        superseded=superseded,
        source_date=plan.source_date,
        raw_source=plan.raw,
        extra_paths=["wiki/_index.md"],
    )
    return commit.commit(root, manifest)


def _main(argv=None) -> int:  # pragma: no cover - thin CLI wrapper
    import argparse
    import json
    import os

    import tool_call_stats
    import vault as vault_mod

    parser = argparse.ArgumentParser(description="Execute an IngestPlan against the resolved vault.")
    parser.add_argument("--plan", required=True, help="path to an IngestPlan JSON file")
    args = parser.parse_args(argv)

    data = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    plan = IngestPlan.from_dict(data)
    root = vault_mod.resolve_vault_root()
    print(execute(root, plan))

    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if session_id:
        events = tool_call_stats.read_log(session_id)
        if events:
            print(tool_call_stats.format_summary(tool_call_stats.summarize(events)))

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
