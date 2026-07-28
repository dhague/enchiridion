"""IngestPlan schema + single-call executor (#49, per #42's resolution).

Collapses the wiki-ingest procedure's mechanical remainder — the ~12 shell
calls SKILL.md steps 4-9 used to spell out by hand — behind one seam: an
:class:`IngestPlan` describing the decided outcome (pages to create/update,
their frontmatter and typed edges) in, a commit SHA out. Steps 1-3 (read,
semantic-chunk, overlap classification) stay judgment and stay with the
ingesting agent; everything downstream of that decision is mechanics, and
mechanics belongs in a tested script, not prose an agent re-derives every run.

The pipeline, in order: validate -> per page (place -> frontmatter -> body) ->
regenerate the index -> derive a commit.Manifest from the plan -> commit.
Validation is two-phase and runs entirely before any write: shape (required
fields, valid op) then semantic (an update's `rel` exists, a create's target
doesn't yet, every edge/raw_source link resolves to a real page — either
already on disk or another page this same plan creates).

The raw artifact named by `plan.raw` is never renamed or moved: per #28/#38 a
file with external identity keeps its name verbatim, forever. Ingestion only
reads it and stages it into the commit; `raw_source` links point at it where it
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

import commit
import place
import wikipage
from wikipage import Vault, WikiPage


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
    source_date: str | None = None
    raw: str | None = None
    pages: list[PagePlan] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "IngestPlan":
        return cls(
            title=d.get("title", ""),
            source_date=d.get("source_date"),
            raw=d.get("raw"),
            pages=[PagePlan.from_dict(p) for p in d.get("pages", [])],
        )


def _link_path(link: str) -> str | None:
    """Return a markdown link's decoded, anchor-free destination path.

    ``None`` when ``link`` isn't a markdown link at all. Decoded rather than raw
    (#38): a preserved raw filename with a space or paren in it is
    percent-encoded in the destination, and only the decoded form compares
    against a path on disk.
    """
    match = next(iter(wikipage.iter_links(link)), None)
    return match.decoded_path if match is not None else None


def _resolve(path: str, page_dir: str) -> str:
    """Re-express a decoded link path (relative to ``page_dir``) as vault-relative."""
    return posixpath.normpath(posixpath.join(page_dir or ".", path))


def _page_dir(page: PagePlan) -> str | None:
    """The vault-relative directory this page's links resolve from, or ``None``
    when it can't be computed yet (an invalid ``kind``/``rel`` already recorded
    as its own shape error)."""
    if page.op == "create":
        if page.kind not in place.KINDS or not page.title:
            return None
        return posixpath.dirname(place.path(page.kind, page.title))
    if not page.rel:
        return None
    return posixpath.dirname(page.rel)


def _page_links(page: PagePlan) -> list[str]:
    links = list(page.frontmatter.get("raw_source", "") and [page.frontmatter["raw_source"]] or [])
    for targets in page.edges.values():
        links.extend(targets)
    return links


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
        else:
            if page.kind is not None:
                errors.append(f"{prefix}.kind must not be set for op=update")
            if not page.rel:
                errors.append(f"{prefix}.rel is required for op=update")
            elif root is not None and not (root / page.rel).is_file():
                errors.append(f"{prefix}.rel {page.rel} does not exist")

        if root is None:
            continue

        page_dir = _page_dir(page)
        if page_dir is None:
            continue
        for link in _page_links(page):
            dest = _link_path(link)
            if dest is None:
                errors.append(f"{prefix}: {link!r} is not a markdown link")
                continue
            resolved = _resolve(dest, page_dir)
            if resolved in prospective:
                continue
            if not (root / resolved).exists():
                errors.append(f"{prefix}: link {link!r} does not resolve to a real page")

    if errors:
        raise PlanError("; ".join(errors))


def _apply_frontmatter(page: WikiPage, plan_page: PagePlan) -> WikiPage:
    page = page.set("title", plan_page.title)
    merging = plan_page.op == "update"
    for key, value in plan_page.frontmatter.items():
        if merging and isinstance(value, list):
            page = page.merge(key, value)
        else:
            page = page.set(key, value)
    for key, links in plan_page.edges.items():
        page = page.merge(key, links) if merging else page.set(key, links)
    return page


def _apply_body(page: WikiPage, new_body: str | None) -> WikiPage:
    if new_body is None:
        return page
    _fm, _body, offset = wikipage.split_frontmatter(page.text)
    return WikiPage(page.text[:offset] + new_body)


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
            page_dir = posixpath.dirname(rel)
            page = _apply_frontmatter(WikiPage(""), plan_page)
            page = WikiPage(page.text + plan_page.body)
            v.write(rel, page)
            created.append(rel)

            for link in plan_page.edges.get("supersedes", []):
                dest = _link_path(link)
                if dest is not None:
                    superseded.append((_resolve(dest, page_dir), rel))
        else:
            rel = plan_page.rel
            page = v.load(rel)
            page = _apply_frontmatter(page, plan_page)
            page = _apply_body(page, plan_page.body)
            v.write(rel, page)
            updated.append(rel)

    from build_index import write_index

    write_index(root)

    manifest = commit.Manifest(
        title=plan.title,
        action="ingest",
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

    import vault as vault_mod

    parser = argparse.ArgumentParser(description="Execute an IngestPlan against the resolved vault.")
    parser.add_argument("--plan", required=True, help="path to an IngestPlan JSON file")
    args = parser.parse_args(argv)

    data = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    plan = IngestPlan.from_dict(data)
    root = vault_mod.resolve_vault_root()
    print(execute(root, plan))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
