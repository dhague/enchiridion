"""IngestPlan schema + single-call executor. Plan in, commit SHA out.

An :class:`IngestPlan` is the decided outcome of an ingestion: which pages to
create/update, with what frontmatter and typed edges. Semantic chunking and
overlap classification are judgment and stay with the ingesting agent;
everything downstream of that decision is mechanics and lives here.

**A plan names link targets by vault-relative page reference only** —
`edges` and `supersedes` hold paths like `"wiki/concept/foo.md"`, never
composed `"[Title](../dest.md)"` strings. Composing the link (title lookup,
`../` relativisation, percent-encoding, YAML quoting) is this module's job,
via :func:`_compose_edges` / :func:`wikipage.compose_link`. `raw_source` uses
a boolean sentinel for the same reason: `frontmatter: {"raw_source": true}`
marks the page as the stub for `plan.raw`, and the link is composed from
that. Body links are re-encoded on write by
:func:`wikipage.normalize_body_links`.

Pipeline: :func:`resolve` -> :meth:`ResolvedPlan.validate` ->
:meth:`ResolvedPlan.execute` -> derive a `commit.Manifest` -> commit.

:func:`resolve` is the single place placement (:func:`place.path`),
frontmatter projection and edge/`raw_source` link composition happen: it
turns a plan into the exact ``(page_ref, WikiPage)`` pairs the vault will end
up holding. Validation then reads only resolved facts, and execution writes
only resolved pages — so the plan that was checked and the plan that gets
written cannot diverge. `resolve` is pure apart from vault reads.

Validation runs entirely before any write, shape (required fields, valid op)
then semantic (an update's `page_ref` exists, a create's target doesn't yet,
every edge target resolves to a page already on disk *or* created by this
same plan, and :mod:`chain_of_evidence` holds). That last check is a courtesy
to the agent — :mod:`commit` re-runs it as the hard gate, so a hand-built
manifest can't route around :func:`validate` into history.

Ingestion isn't the only caller: `wiki-retrieval`'s confirmed synthesis-page
save is the same shape (one `create` of kind `synthesis`, `source` edges, no
raw artifact) and passes `action: "synthesize"` so the history distinguishes
the two without reading the diff.

`plan.raw` is never renamed or moved — a file with external identity keeps
its name forever. Ingestion reads it and stages it; `raw_source` links point
at it where it sits, percent-encoded by the link machinery rather than
sanitized on disk.

**No rollback on failure, deliberately.** A page written before a later step
raises stays on disk, uncommitted. Every write here is idempotent, so
re-running the plan after fixing the cause is always safe.

CLI::

    python ingest.py --plan <path>   # executes against the resolved vault, prints the commit SHA
    python ingest.py --plan <path> --dry-run   # resolves + validates, prints what would be written
"""
from __future__ import annotations

import posixpath
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import chain_of_evidence
import commit
import place
import wikipage
from vault import Vault
from wikipage import WikiPage

#: Cap on full-path length (vault root + vault-relative path), for Windows'
#: 255-char limit (#70).
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
    page_ref: str | None = None
    frontmatter: dict = field(default_factory=dict)
    edges: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "PagePlan":
        return cls(
            op=d.get("op", ""),
            title=d.get("title", ""),
            body=d.get("body"),
            kind=d.get("kind"),
            page_ref=d.get("page_ref"),
            frontmatter=dict(d.get("frontmatter", {})),
            edges={k: list(v) for k, v in d.get("edges", {}).items()},
        )


@dataclass
class IngestPlan:
    """The deterministic description of one ingestion's decided outcome."""

    title: str
    #: The structured commit's verb (`commit.Manifest.action`): `ingest`, or
    #: `synthesize` for a wiki-retrieval synthesis save.
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


def _page_ref(page: PagePlan, extra_kind_folders: dict[str, str]) -> str | None:
    """The vault-relative path this page will occupy, or ``None`` when it can't
    be computed yet (an invalid ``kind``/``page_ref`` already recorded as its
    own shape error).

    The **only** caller of :func:`place.path` in this module — every other
    consumer reads :attr:`ResolvedPage.page_ref`.
    """
    if page.op == "create":
        if not page.title:
            return None
        if page.kind in place.KINDS:
            return place.path(page.kind, page.title)
        if page.kind in extra_kind_folders:
            return place.path(page.kind, page.title, extra_kind_folders=extra_kind_folders)
        return None
    return page.page_ref or None


def _resolve_title(target_ref: str, titles: dict[str, str], v: Vault | None) -> str:
    """The title a link to ``target_ref`` should carry.

    This plan's own page for that page_ref wins (``titles``), so an update
    that corrects a title propagates to every link the same plan writes. Then
    the on-disk title; then the basename, reachable only if validation let an
    unresolvable target through.
    """
    target_ref = posixpath.normpath(target_ref)
    if target_ref in titles:
        return titles[target_ref]
    if v is not None and (v.root / target_ref).is_file():
        title = v.load(target_ref).get("title")
        if title:
            return title
    return posixpath.basename(target_ref)


def _compose_raw_source(page_dir: str, plan: IngestPlan) -> str:
    """The composed ``raw_source`` link for the ``frontmatter.raw_source: true`` sentinel."""
    assert plan.raw is not None  # guarded in _apply_frontmatter: only composed when plan.raw is set
    return wikipage.compose_link(posixpath.basename(plan.raw), plan.raw, page_dir)


def _compose_edges(
    edges: dict[str, list[str]], page_dir: str, titles: dict[str, str], v: Vault | None
) -> dict[str, list[str]]:
    """Compose every edge-key's vault-relative page refs into markdown links."""
    return {
        key: [wikipage.compose_link(_resolve_title(ref, titles, v), ref, page_dir) for ref in refs]
        for key, refs in edges.items()
    }


def _page_link_targets(page: PagePlan, plan: IngestPlan) -> list[tuple[str, str]]:
    """``(key, normalized target page_ref)`` pairs for existence validation.
    Plans name targets by vault-relative page reference only, so this is a
    plain normalize — no markdown-link parsing.
    """
    targets: list[tuple[str, str]] = []
    if page.frontmatter.get("raw_source") is True and plan.raw:
        targets.append(("raw_source", posixpath.normpath(plan.raw)))
    for key, refs in page.edges.items():
        for ref in refs:
            targets.append((key, posixpath.normpath(ref)))
    return targets


def _apply_frontmatter(
    page: WikiPage,
    plan_page: PagePlan,
    page_dir: str,
    plan: IngestPlan,
    titles: dict[str, str],
    v: Vault | None,
) -> WikiPage:
    """The **only** frontmatter projection in this module — see :func:`resolve`."""
    page = page.set("title", plan_page.title)
    merging = plan_page.op == "update"
    for key, value in plan_page.frontmatter.items():
        if key == "raw_source" and value is True:
            if not plan.raw:
                continue  # nothing to point at; validate reports it as a shape error
            value = _compose_raw_source(page_dir, plan)
        if merging and isinstance(value, list):
            page = page.merge(key, value)
        else:
            page = page.set(key, value)
    for key, links in _compose_edges(plan_page.edges, page_dir, titles, v).items():
        page = page.merge(key, links) if merging else page.set(key, links)
    return page


def _apply_body(page: WikiPage, new_body: str | None) -> WikiPage:
    if new_body is None:
        return page
    _fm, _body, offset = wikipage.split_frontmatter(page.text)
    return WikiPage(page.text[:offset] + wikipage.normalize_body_links(new_body))


@dataclass
class ResolvedPage:
    """One plan page, resolved to the exact file the vault will hold.

    ``page_ref``/``page`` are ``None`` together, when placement couldn't be
    computed (an invalid ``kind``, a missing ``page_ref``) — its own shape
    error, reported by :meth:`ResolvedPlan.validate`.
    """

    plan_page: PagePlan
    page_ref: str | None
    #: The full post-write content: projected frontmatter plus body. A create
    #: starts blank, an update from its on-disk copy, so a re-ingest's
    #: existing edges and the fresh plan's edges are both visible at once.
    page: WikiPage | None
    #: Whether ``page_ref`` was already taken when the plan was resolved — a
    #: create may not claim it.
    exists: bool = False
    #: Whether an existing page was read as this page's base. An update needs
    #: one; a create never has one.
    loaded: bool = False

    @property
    def op(self) -> str:
        return self.plan_page.op


@dataclass
class ResolvedPlan:
    """A plan with every derived fact computed exactly once.

    Constructible directly (no vault needed) for tests; :func:`resolve` is the
    production path.
    """

    plan: IngestPlan
    pages: list[ResolvedPage] = field(default_factory=list)
    #: ``None`` when resolved without a vault — shape checks only, no reads.
    root: Path | None = None
    #: ``{kind: folder}`` for vault-discovered kind-folders beyond the four
    #: canonical ones; empty when resolved without a vault.
    extra_kind_folders: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        """Validate this plan, shape then semantic, before any write.

        Raises :class:`PlanError` with every problem found (not just the
        first) on failure.
        """
        errors = _shape_errors(self) + _semantic_errors(self)
        if errors:
            raise PlanError("; ".join(errors))

    def execute(self) -> str:
        """Write every resolved page and commit. Returns the commit SHA.

        Assumes :meth:`validate` has already passed. No rollback on failure —
        see the module docstring.
        """
        if self.root is None:
            raise PlanError("cannot execute a plan resolved without a vault root")
        v = Vault(self.root)

        created: list[str] = []
        updated: list[str] = []
        superseded: list[tuple[str, str]] = []

        for resolved in self.pages:
            if resolved.page_ref is None or resolved.page is None:
                raise PlanError(f"page {resolved.plan_page.title!r} was not resolved")
            v.write(resolved.page_ref, resolved.page)
            if resolved.op == "create":
                created.append(resolved.page_ref)
                for target_ref in resolved.plan_page.edges.get("supersedes", []):
                    superseded.append((posixpath.normpath(target_ref), resolved.page_ref))
            else:
                updated.append(resolved.page_ref)

        manifest = commit.Manifest(
            title=self.plan.title,
            action=self.plan.action,
            created=created,
            updated=updated,
            superseded=superseded,
            source_date=self.plan.source_date,
            raw_source=self.plan.raw,
        )
        return commit.commit(self.root, manifest)

    def describe(self) -> str:
        """Human-readable summary of what :meth:`execute` would write."""
        lines = [f"{self.plan.action}: {self.plan.title}"]
        for resolved in self.pages:
            lines.append(f"  {resolved.op:6} {resolved.page_ref}")
        return "\n".join(lines)


def resolve(plan: IngestPlan, vault_root: Path | str | None) -> ResolvedPlan:
    """Turn ``plan`` into the exact pages the vault will hold.

    Pure apart from vault reads: placement, frontmatter projection and link
    composition each happen here and nowhere else, so validation and
    execution read the same facts by construction rather than by convention.
    """
    root = Path(vault_root) if vault_root is not None else None
    v = Vault(root) if root is not None else None
    extra_kind_folders = v.discovered_kinds() if v is not None else {}

    refs = [_page_ref(p, extra_kind_folders) for p in plan.pages]

    # First page wins, so a link's title matches the earliest plan page
    # claiming that page_ref.
    titles: dict[str, str] = {}
    for ref, plan_page in zip(refs, plan.pages):
        if ref is not None:
            titles.setdefault(ref, plan_page.title)

    pages: list[ResolvedPage] = []
    for plan_page, page_ref in zip(plan.pages, refs):
        if page_ref is None:
            pages.append(ResolvedPage(plan_page, None, None))
            continue
        if plan_page.op == "update" and v is not None and (v.root / page_ref).is_file():
            base = v.load(page_ref)
            loaded = True
        else:
            base = WikiPage("")
            loaded = False
        page = _apply_frontmatter(base, plan_page, posixpath.dirname(page_ref), plan, titles, v)
        page = _apply_body(page, plan_page.body)
        exists = root is not None and (root / page_ref).exists()
        pages.append(ResolvedPage(plan_page, page_ref, page, exists, loaded))

    return ResolvedPlan(plan=plan, pages=pages, root=root, extra_kind_folders=extra_kind_folders)


def _shape_errors(resolved: ResolvedPlan) -> list[str]:
    """Required fields and valid ops — everything checkable without a vault."""
    plan = resolved.plan
    errors: list[str] = []

    if not plan.title:
        errors.append("plan.title is required")
    if not plan.pages:
        errors.append("plan.pages must contain at least one page")

    for i, rp in enumerate(resolved.pages):
        page = rp.plan_page
        prefix = f"pages[{i}]"

        if page.op not in ("create", "update"):
            errors.append(f"{prefix}.op must be 'create' or 'update', got {page.op!r}")
            continue
        if not page.title:
            errors.append(f"{prefix}.title is required")

        if page.op == "create":
            if page.page_ref is not None:
                errors.append(f"{prefix}.page_ref must not be set for op=create")
            if not page.kind:
                errors.append(f"{prefix}.kind is required for op=create")
            elif page.kind not in place.KINDS and page.kind not in resolved.extra_kind_folders:
                errors.append(f"{prefix}.kind {page.kind!r} is not a valid kind")
            if page.body is None:
                errors.append(f"{prefix}.body is required for op=create")
        else:
            if page.kind is not None:
                errors.append(f"{prefix}.kind must not be set for op=update")
            if not page.page_ref:
                errors.append(f"{prefix}.page_ref is required for op=update")

        raw_source = page.frontmatter.get("raw_source")
        if raw_source is not None and raw_source is not True:
            errors.append(
                f"{prefix}.frontmatter.raw_source must be true (derived from plan.raw),"
                f" got {raw_source!r}"
            )
        elif raw_source is True and not plan.raw:
            errors.append(f"{prefix}.frontmatter.raw_source is true but plan.raw is not set")

    return errors


def _semantic_errors(resolved: ResolvedPlan) -> list[str]:
    """Checks that need the vault: target existence, path length, evidence chain."""
    root = resolved.root
    if root is None:
        return []

    plan = resolved.plan
    errors: list[str] = []

    # A page this same plan is about to create counts as resolvable too, so
    # sibling new pages can link to each other before either exists on disk.
    prospective = {rp.page_ref for rp in resolved.pages if rp.op == "create" and rp.page_ref}

    for i, rp in enumerate(resolved.pages):
        page = rp.plan_page
        prefix = f"pages[{i}]"

        if page.op not in ("create", "update"):
            continue

        if page.op == "create":
            if rp.page_ref is not None:
                if rp.exists:
                    errors.append(f"{prefix}: create target {rp.page_ref} already exists")
                full = str(root / rp.page_ref)
                if len(full) > MAX_PATH_LENGTH:
                    errors.append(
                        f"{prefix}: path {rp.page_ref} exceeds {MAX_PATH_LENGTH} chars"
                        f" ({len(full)} chars with vault root)"
                    )
        elif rp.page_ref is not None and not rp.loaded:
            errors.append(f"{prefix}.page_ref {rp.page_ref} does not exist")

        for key, target in _page_link_targets(page, plan):
            if target in prospective:
                continue
            if not (root / target).exists():
                errors.append(f"{prefix}: {key} target {target!r} does not resolve to a real page")

    if plan.raw:
        # A courtesy check for the agent; `commit` re-runs it as the hard gate.
        staged = {
            rp.page_ref: rp.page
            for rp in resolved.pages
            if rp.page_ref is not None and rp.page is not None
        }
        errors.extend(chain_of_evidence.check(staged, plan.raw))

    return errors


def validate(plan: IngestPlan, vault_root: Path | str | None) -> None:
    """Resolve then validate ``plan``. Raises :class:`PlanError` on failure."""
    resolve(plan, vault_root).validate()


def execute(vault_root: Path | str, plan: IngestPlan) -> str:
    """Execute ``plan`` against the vault at ``vault_root``. Returns the commit
    SHA. No rollback on failure — see the module docstring."""
    resolved = resolve(plan, vault_root)
    resolved.validate()
    return resolved.execute()


def _ignore_raw_file(root: Path, raw_rel: str, comment: str | None) -> None:
    """Append ``raw_rel`` to its own folder's ``.ingestignore``.

    ``raw_rel`` is vault-relative, exactly as `ingest_scan.py` prints it
    (``raw/emails/foo.eml``), so the agent never has to split it into
    ``.ingestignore``'s folder/pattern form itself.
    """
    import ingest_scan
    import vault as vault_mod

    rel_to_raw = PurePosixPath(raw_rel).relative_to("raw")
    folder = "" if rel_to_raw.parent == PurePosixPath(".") else str(rel_to_raw.parent)
    ingest_scan.Sweep(vault_mod.Vault(root)).append_ignore_entry(folder, rel_to_raw.name, comment)


def _main(argv=None) -> int:  # pragma: no cover - thin CLI wrapper
    import argparse
    import json
    import os

    import tool_call_stats
    import vault as vault_mod

    parser = argparse.ArgumentParser(description="Execute an IngestPlan against the resolved vault.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", help="path to an IngestPlan JSON file")
    group.add_argument(
        "--ignore",
        metavar="RAW_REL",
        help="never offer this raw/ file again for a sweep (appends it to its folder's .ingestignore)",
    )
    parser.add_argument(
        "--ignore-comment",
        help="optional trailing comment for the --ignore entry",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve and validate the plan, print what would be written, write nothing",
    )
    args = parser.parse_args(argv)
    if args.dry_run and not args.plan:
        parser.error("--dry-run only applies to --plan; --ignore always writes")

    root = vault_mod.resolve_vault_root()

    if args.ignore:
        _ignore_raw_file(root, args.ignore, args.ignore_comment)
        return 0

    data = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    plan = IngestPlan.from_dict(data)
    resolved = resolve(plan, root)
    resolved.validate()
    if args.dry_run:
        print(resolved.describe())
        return 0
    print(resolved.execute())

    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if session_id:
        events = tool_call_stats.read_log(session_id)
        if events:
            print(tool_call_stats.format_summary(tool_call_stats.summarize(events)))

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
