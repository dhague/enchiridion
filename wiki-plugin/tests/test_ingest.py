"""TDD for ingest.py — the IngestPlan schema + single-call executor (#49, per #42).

Collapses the ~12-call ingestion orchestration behind one seam: a plan
describing the decided outcome (pages to create/update, their frontmatter and
edges) in, a commit SHA out. Steps 1-3 of wiki-ingest (read, chunk, overlap
classification) stay judgment and stay with the agent; this module only
executes the mechanical remainder: place -> frontmatter -> body -> index ->
manifest -> commit.
"""
from __future__ import annotations

import subprocess

import pytest

import ingest
from vault import Vault
from wikipage import WikiPage


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def vault_root(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "wiki" / "concept").mkdir(parents=True)
    (tmp_path / "wiki" / "entity").mkdir(parents=True)
    (tmp_path / "wiki" / "source").mkdir(parents=True)
    (tmp_path / "raw").mkdir()
    existing = tmp_path / "wiki" / "concept" / "existing.md"
    existing.write_text(
        '---\ntitle: Existing\nsummary: an existing page\ntags:\n    - db\nsource_date: "2026-01-01"\nvolatility: stable\n---\n# Existing\n\nSome body.\n',
        encoding="utf-8",
    )
    (tmp_path / "wiki" / "_index.md").write_text("stub\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q", "-m", "seed"], check=True
    )
    return tmp_path


def _plan_dict(**overrides):
    base = {
        "title": "Postgres tuning notes",
        "source_date": "2026-03-01",
        "pages": [
            {
                "op": "create",
                "kind": "concept",
                "title": "Prepared Statements",
                "body": "# Prepared Statements\n\nReduce parse overhead.\n",
                "frontmatter": {
                    "summary": "Reusing a parsed query plan",
                    "tags": ["db"],
                    "source_date": "2026-03-01",
                    "volatility": "stable",
                },
                "edges": {"related": ["[Existing](../concept/existing.md)"]},
            }
        ],
    }
    base.update(overrides)
    return base


# --- IngestPlan / PagePlan schema --------------------------------------------


def test_plan_from_dict_roundtrip():
    plan = ingest.IngestPlan.from_dict(_plan_dict())
    assert plan.title == "Postgres tuning notes"
    assert plan.source_date == "2026-03-01"
    assert plan.raw is None
    assert len(plan.pages) == 1
    page = plan.pages[0]
    assert page.op == "create"
    assert page.kind == "concept"
    assert page.title == "Prepared Statements"
    assert page.frontmatter["tags"] == ["db"]
    assert page.edges["related"] == ["[Existing](../concept/existing.md)"]
    assert page.rel is None


def test_plan_from_dict_defaults():
    plan = ingest.IngestPlan.from_dict({"title": "T", "pages": []})
    assert plan.source_date is None
    assert plan.raw is None
    assert plan.pages == []


def test_page_from_dict_update_shape():
    d = {
        "op": "update",
        "title": "Existing",
        "rel": "wiki/concept/existing.md",
        "frontmatter": {"tags": ["db", "sql"]},
        "edges": {},
    }
    page = ingest.PagePlan.from_dict(d)
    assert page.op == "update"
    assert page.rel == "wiki/concept/existing.md"
    assert page.kind is None
    assert page.body is None


# --- validation ---------------------------------------------------------------


def test_validate_passes_for_good_plan(vault_root):
    plan = ingest.IngestPlan.from_dict(_plan_dict())
    ingest.validate(plan, vault_root)  # no raise


def test_validate_rejects_missing_title():
    plan = ingest.IngestPlan.from_dict({"title": "", "pages": []})
    with pytest.raises(ingest.PlanError):
        ingest.validate(plan, None)


def test_validate_rejects_bad_op(vault_root):
    d = _plan_dict()
    d["pages"][0]["op"] = "delete"
    plan = ingest.IngestPlan.from_dict(d)
    with pytest.raises(ingest.PlanError, match="op"):
        ingest.validate(plan, vault_root)


def test_validate_rejects_create_missing_kind(vault_root):
    d = _plan_dict()
    del d["pages"][0]["kind"]
    plan = ingest.IngestPlan.from_dict(d)
    with pytest.raises(ingest.PlanError, match="kind"):
        ingest.validate(plan, vault_root)


def test_validate_rejects_create_target_already_exists(vault_root):
    d = _plan_dict()
    d["pages"][0]["title"] = "Existing"
    plan = ingest.IngestPlan.from_dict(d)
    with pytest.raises(ingest.PlanError, match="already exists"):
        ingest.validate(plan, vault_root)


def test_validate_rejects_update_missing_rel(vault_root):
    plan = ingest.IngestPlan.from_dict(
        {"title": "T", "pages": [{"op": "update", "title": "X"}]}
    )
    with pytest.raises(ingest.PlanError, match="rel"):
        ingest.validate(plan, vault_root)


def test_validate_rejects_update_rel_not_found(vault_root):
    plan = ingest.IngestPlan.from_dict(
        {
            "title": "T",
            "pages": [
                {"op": "update", "title": "X", "rel": "wiki/concept/missing.md"}
            ],
        }
    )
    with pytest.raises(ingest.PlanError, match="does not exist"):
        ingest.validate(plan, vault_root)


def test_validate_rejects_dangling_edge_link(vault_root):
    d = _plan_dict()
    d["pages"][0]["edges"] = {"related": ["[Nope](../concept/nope.md)"]}
    plan = ingest.IngestPlan.from_dict(d)
    with pytest.raises(ingest.PlanError, match="does not resolve"):
        ingest.validate(plan, vault_root)


def test_validate_accepts_edge_link_to_sibling_page_created_in_same_plan(vault_root):
    d = {
        "title": "Two new pages",
        "pages": [
            {
                "op": "create",
                "kind": "concept",
                "title": "Alpha",
                "body": "# Alpha\n",
                "frontmatter": {},
                "edges": {"related": ["[Beta](../concept/beta.md)"]},
            },
            {
                "op": "create",
                "kind": "concept",
                "title": "Beta",
                "body": "# Beta\n",
                "frontmatter": {},
                "edges": {"related": ["[Alpha](../concept/alpha.md)"]},
            },
        ],
    }
    plan = ingest.IngestPlan.from_dict(d)
    ingest.validate(plan, vault_root)  # no raise


def test_validate_rejects_raw_source_not_matching_plan_raw(vault_root):
    (vault_root / "raw" / "notes.md").write_text("raw\n", encoding="utf-8")
    d = _plan_dict(raw="raw/notes.md")
    d["pages"][0]["kind"] = "source"
    d["pages"][0]["frontmatter"]["raw_source"] = "[other.md](../../raw/other.md)"
    plan = ingest.IngestPlan.from_dict(d)
    with pytest.raises(ingest.PlanError, match="does not resolve"):
        ingest.validate(plan, vault_root)


# --- validation: the mandatory source/ stub + source edges (#34) ----------------


def _raw_plan_dict(**overrides):
    """A plan sourced from a raw artifact: its `source/` stub plus one distilled page.

    The shape #34 makes mandatory — every page a raw file produces carries a
    `source` edge back to that file's stub, and the stub always exists.
    """
    base = {
        "title": "Postgres tuning notes",
        "source_date": "2026-03-01",
        "raw": "raw/notes.md",
        "pages": [
            {
                "op": "create",
                "kind": "source",
                "title": "Notes",
                "body": "# Notes\n\nStands in for the raw file.\n",
                "frontmatter": {
                    "summary": "The tuning notes as filed",
                    "raw_source": "[notes.md](../../raw/notes.md)",
                },
                "edges": {},
            },
            {
                "op": "create",
                "kind": "concept",
                "title": "Prepared Statements",
                "body": "# Prepared Statements\n\nReduce parse overhead.\n",
                "frontmatter": {"summary": "Reusing a parsed query plan"},
                "edges": {"source": ["[Notes](../source/notes.md)"]},
            },
        ],
    }
    base.update(overrides)
    return base


@pytest.fixture
def raw_notes(vault_root):
    (vault_root / "raw" / "notes.md").write_text("raw notes\n", encoding="utf-8")
    return vault_root


def test_validate_passes_when_stub_and_every_source_edge_are_present(raw_notes):
    plan = ingest.IngestPlan.from_dict(_raw_plan_dict())
    ingest.validate(plan, raw_notes)  # no raise


def test_validate_rejects_a_raw_ingestion_with_no_source_stub(raw_notes):
    """The case the old 'distil straight into concept/ and skip source/' allowed."""
    d = _raw_plan_dict()
    del d["pages"][0]
    d["pages"][0]["edges"] = {}
    plan = ingest.IngestPlan.from_dict(d)
    with pytest.raises(ingest.PlanError, match="source/ page"):
        ingest.validate(plan, raw_notes)


def test_validate_rejects_a_stub_whose_raw_source_points_elsewhere(raw_notes):
    (raw_notes / "raw" / "other.md").write_text("other\n", encoding="utf-8")
    d = _raw_plan_dict()
    d["pages"][0]["frontmatter"]["raw_source"] = "[other.md](../../raw/other.md)"
    plan = ingest.IngestPlan.from_dict(d)
    with pytest.raises(ingest.PlanError, match="source/ page"):
        ingest.validate(plan, raw_notes)


def test_validate_rejects_a_created_page_missing_the_source_edge(raw_notes):
    d = _raw_plan_dict()
    d["pages"][1]["edges"] = {"related": ["[Existing](../concept/existing.md)"]}
    plan = ingest.IngestPlan.from_dict(d)
    with pytest.raises(ingest.PlanError, match="source edge"):
        ingest.validate(plan, raw_notes)


def test_validate_rejects_one_missing_source_edge_among_several_pages(raw_notes):
    d = _raw_plan_dict()
    d["pages"].append(
        {
            "op": "create",
            "kind": "concept",
            "title": "Connection Pooling",
            "body": "# Connection Pooling\n",
            "frontmatter": {},
            "edges": {},  # the one page that forgot it
        }
    )
    plan = ingest.IngestPlan.from_dict(d)
    with pytest.raises(ingest.PlanError, match="wiki/concept/connection-pooling.md"):
        ingest.validate(plan, raw_notes)


# --- validation: path-length ceiling (#70) --------------------------------------


def test_validate_rejects_path_exceeding_max_length(monkeypatch, vault_root):
    """A create plan whose full path exceeds the limit is rejected before any write."""
    monkeypatch.setattr(ingest, "MAX_PATH_LENGTH", 60)
    d = _plan_dict()
    d["pages"][0]["title"] = "Long Enough Title"
    plan = ingest.IngestPlan.from_dict(d)
    with pytest.raises(ingest.PlanError, match="exceeds"):
        ingest.validate(plan, vault_root)


def test_validate_accepts_path_within_limit(vault_root):
    d = _plan_dict()
    plan = ingest.IngestPlan.from_dict(d)
    ingest.validate(plan, vault_root)  # no raise


def test_validate_requires_the_source_edge_on_an_updated_page_too(raw_notes):
    """#34 point 3: step 3's update-in-place case is not exempt."""
    d = _raw_plan_dict()
    d["pages"][1] = {
        "op": "update",
        "title": "Existing",
        "rel": "wiki/concept/existing.md",
        "frontmatter": {"volatility": "evolving"},
        "edges": {},
    }
    plan = ingest.IngestPlan.from_dict(d)
    with pytest.raises(ingest.PlanError, match="source edge"):
        ingest.validate(plan, raw_notes)


def test_validate_accepts_an_updated_page_carrying_the_source_edge(raw_notes):
    d = _raw_plan_dict()
    d["pages"][1] = {
        "op": "update",
        "title": "Existing",
        "rel": "wiki/concept/existing.md",
        "frontmatter": {"volatility": "evolving"},
        "edges": {"source": ["[Notes](../source/notes.md)"]},
    }
    plan = ingest.IngestPlan.from_dict(d)
    ingest.validate(plan, raw_notes)  # no raise


def test_validate_accepts_a_source_edge_the_page_already_carries_on_disk(raw_notes):
    """A re-ingest need not restate an edge the page already has."""
    (raw_notes / "wiki" / "source" / "notes.md").write_text(
        '---\ntitle: Notes\nraw_source: "[notes.md](../../raw/notes.md)"\n---\n# Notes\n',
        encoding="utf-8",
    )
    Vault(raw_notes).set(
        "wiki/concept/existing.md", "source", ["[Notes](../source/notes.md)"]
    )
    d = {
        "title": "Second pass over the same notes",
        "raw": "raw/notes.md",
        "pages": [
            {
                "op": "update",
                "title": "Notes",
                "rel": "wiki/source/notes.md",
                "frontmatter": {},
                "edges": {},
            },
            {
                "op": "update",
                "title": "Existing",
                "rel": "wiki/concept/existing.md",
                "frontmatter": {"volatility": "evolving"},
                "edges": {},
            },
        ],
    }
    plan = ingest.IngestPlan.from_dict(d)
    ingest.validate(plan, raw_notes)  # no raise


def test_validate_recognises_an_existing_stub_updated_in_place(raw_notes):
    """The stub may be an `update` whose raw_source lives on disk, not in the plan."""
    (raw_notes / "wiki" / "source" / "notes.md").write_text(
        '---\ntitle: Notes\nraw_source: "[notes.md](../../raw/notes.md)"\n---\n# Notes\n',
        encoding="utf-8",
    )
    d = {
        "title": "Second pass over the same notes",
        "raw": "raw/notes.md",
        "pages": [
            {
                "op": "update",
                "title": "Notes",
                "rel": "wiki/source/notes.md",
                "frontmatter": {},
                "edges": {},
            },
            {
                "op": "create",
                "kind": "concept",
                "title": "Prepared Statements",
                "body": "# Prepared Statements\n",
                "frontmatter": {},
                "edges": {"source": ["[Notes](../source/notes.md)"]},
            },
        ],
    }
    plan = ingest.IngestPlan.from_dict(d)
    ingest.validate(plan, raw_notes)  # no raise


def test_validate_does_not_require_the_stub_to_link_to_itself(raw_notes):
    """The stub is exempt from the edge rule — nothing sources itself."""
    d = _raw_plan_dict()
    del d["pages"][1]
    plan = ingest.IngestPlan.from_dict(d)
    ingest.validate(plan, raw_notes)  # no raise


def test_validate_skips_the_gate_when_the_plan_has_no_raw(vault_root):
    """A synthesis save has no raw artifact, so there is no stub to demand."""
    plan = ingest.IngestPlan.from_dict(_plan_dict())
    assert plan.raw is None
    ingest.validate(plan, vault_root)  # no raise


def test_execute_writes_the_source_edge_to_every_page_from_a_raw_file(raw_notes):
    plan = ingest.IngestPlan.from_dict(_raw_plan_dict())
    ingest.execute(raw_notes, plan)

    v = Vault(raw_notes)
    stub = v.load("wiki/source/notes.md")
    assert stub.get("raw_source") == "[notes.md](../../raw/notes.md)"
    distilled = v.load("wiki/concept/prepared-statements.md")
    assert distilled.get("source") == ["[Notes](../source/notes.md)"]
    assert _git(raw_notes, "status", "--porcelain") == ""


# --- execution: create ---------------------------------------------------------


def test_execute_creates_page_writes_frontmatter_and_body(vault_root):
    plan = ingest.IngestPlan.from_dict(_plan_dict())
    sha = ingest.execute(vault_root, plan)

    assert sha
    v = Vault(vault_root)
    page = v.load("wiki/concept/prepared-statements.md")
    assert page.get("title") == "Prepared Statements"
    assert page.get("summary") == "Reusing a parsed query plan"
    assert page.get("tags") == ["db"]
    assert page.get("related") == ["[Existing](../concept/existing.md)"]
    assert "Reduce parse overhead." in page.body


def test_execute_regenerates_index(vault_root):
    plan = ingest.IngestPlan.from_dict(_plan_dict())
    ingest.execute(vault_root, plan)
    index = (vault_root / "wiki" / "_index.md").read_text(encoding="utf-8")
    assert "prepared-statements.md" in index


def test_execute_commits_with_structured_message(vault_root):
    plan = ingest.IngestPlan.from_dict(_plan_dict())
    sha = ingest.execute(vault_root, plan)
    body = _git(vault_root, "log", "-1", "--pretty=%B")
    assert body.startswith("ingest: Postgres tuning notes\n")
    assert "created: wiki/concept/prepared-statements.md" in body
    assert "source-date: 2026-03-01" in body
    assert _git(vault_root, "rev-parse", "HEAD").strip() == sha


def test_execute_leaves_no_dirty_state_on_success(vault_root):
    plan = ingest.IngestPlan.from_dict(_plan_dict())
    ingest.execute(vault_root, plan)
    assert _git(vault_root, "status", "--porcelain") == ""


# --- execution: update ----------------------------------------------------------


def test_execute_update_merges_tags(vault_root):
    d = {
        "title": "Add sql tag",
        "pages": [
            {
                "op": "update",
                "title": "Existing",
                "rel": "wiki/concept/existing.md",
                "frontmatter": {"tags": ["sql"]},
                "edges": {},
            }
        ],
    }
    plan = ingest.IngestPlan.from_dict(d)
    ingest.execute(vault_root, plan)
    page = Vault(vault_root).load("wiki/concept/existing.md")
    assert page.get("tags") == ["db", "sql"]


def test_execute_update_overwrites_scalar_frontmatter(vault_root):
    d = {
        "title": "Bump volatility",
        "pages": [
            {
                "op": "update",
                "title": "Existing",
                "rel": "wiki/concept/existing.md",
                "frontmatter": {"volatility": "volatile"},
                "edges": {},
            }
        ],
    }
    plan = ingest.IngestPlan.from_dict(d)
    ingest.execute(vault_root, plan)
    page = Vault(vault_root).load("wiki/concept/existing.md")
    assert page.get("volatility") == "volatile"


def test_execute_update_merges_edge_lists(vault_root):
    Vault(vault_root).set(
        "wiki/concept/existing.md", "related", ["[Other](../entity/other.md)"]
    )
    (vault_root / "wiki" / "entity" / "other.md").write_text(
        "---\ntitle: Other\n---\n# Other\n", encoding="utf-8"
    )
    d = {
        "title": "Add a related edge",
        "pages": [
            {
                "op": "update",
                "title": "Existing",
                "rel": "wiki/concept/existing.md",
                "frontmatter": {},
                "edges": {"related": ["[Existing](../concept/existing.md)"]},
            }
        ],
    }
    # point the new edge at a real page instead of itself to keep this realistic
    d["pages"][0]["edges"]["related"] = ["[Other2](../entity/other.md)"]
    plan = ingest.IngestPlan.from_dict(d)
    ingest.execute(vault_root, plan)
    page = Vault(vault_root).load("wiki/concept/existing.md")
    assert page.get("related") == [
        "[Other](../entity/other.md)",
        "[Other2](../entity/other.md)",
    ]


def test_execute_update_replaces_body_when_given(vault_root):
    d = {
        "title": "Rewrite body",
        "pages": [
            {
                "op": "update",
                "title": "Existing",
                "rel": "wiki/concept/existing.md",
                "body": "# Existing\n\nNew body text.\n",
                "frontmatter": {},
                "edges": {},
            }
        ],
    }
    plan = ingest.IngestPlan.from_dict(d)
    ingest.execute(vault_root, plan)
    page = Vault(vault_root).load("wiki/concept/existing.md")
    assert "New body text." in page.body
    assert "Some body." not in page.body
    assert page.get("title") == "Existing"  # frontmatter untouched by the body swap


def test_execute_update_leaves_body_untouched_when_omitted(vault_root):
    d = {
        "title": "Frontmatter-only update",
        "pages": [
            {
                "op": "update",
                "title": "Existing",
                "rel": "wiki/concept/existing.md",
                "frontmatter": {"volatility": "evolving"},
                "edges": {},
            }
        ],
    }
    plan = ingest.IngestPlan.from_dict(d)
    ingest.execute(vault_root, plan)
    page = Vault(vault_root).load("wiki/concept/existing.md")
    assert "Some body." in page.body


def test_execute_records_updated_not_created_in_manifest(vault_root):
    d = {
        "title": "Tweak",
        "pages": [
            {
                "op": "update",
                "title": "Existing",
                "rel": "wiki/concept/existing.md",
                "frontmatter": {"volatility": "evolving"},
                "edges": {},
            }
        ],
    }
    plan = ingest.IngestPlan.from_dict(d)
    ingest.execute(vault_root, plan)
    body = _git(vault_root, "log", "-1", "--pretty=%B")
    assert "updated: wiki/concept/existing.md" in body
    assert "created:" not in body


# --- execution: supersedes ------------------------------------------------------


def test_execute_records_supersedes_pair_in_manifest(vault_root):
    d = {
        "title": "Replace deploy doc",
        "pages": [
            {
                "op": "create",
                "kind": "concept",
                "title": "New Way",
                "body": "# New Way\n",
                "frontmatter": {},
                "edges": {
                    "supersedes": ["[Existing](../concept/existing.md)"],
                    "contradicts": ["[Existing](../concept/existing.md)"],
                },
            }
        ],
    }
    plan = ingest.IngestPlan.from_dict(d)
    ingest.execute(vault_root, plan)
    body = _git(vault_root, "log", "-1", "--pretty=%B")
    assert (
        "superseded: wiki/concept/existing.md -> wiki/concept/new-way.md" in body
    )
    # the superseded page's own content is left untouched
    superseded_page = Vault(vault_root).load("wiki/concept/existing.md")
    assert superseded_page.get("summary") == "an existing page"


# --- execution: raw artifacts are preserved, never renamed -----------------------


def test_execute_preserves_raw_filename_and_leaves_raw_source_untargeted(vault_root):
    (vault_root / "raw" / "notes.md").write_text("raw notes\n", encoding="utf-8")
    d = {
        "title": "File a source page",
        "raw": "raw/notes.md",
        "pages": [
            {
                "op": "create",
                "kind": "source",
                "title": "Notes",
                "body": "# Notes\n",
                "frontmatter": {"raw_source": "[notes.md](../../raw/notes.md)"},
                "edges": {},
            }
        ],
    }
    plan = ingest.IngestPlan.from_dict(d)
    ingest.execute(vault_root, plan)

    # per #28/#38 the raw file keeps its name verbatim and raw_source still
    # points at it exactly as the plan wrote it
    assert (vault_root / "raw" / "notes.md").read_text(encoding="utf-8") == "raw notes\n"
    page = Vault(vault_root).load("wiki/source/notes.md")
    assert page.get("raw_source") == "[notes.md](../../raw/notes.md)"

    # the raw artifact landed in the same commit as the page it produced
    tracked = _git(vault_root, "ls-files").splitlines()
    assert "raw/notes.md" in tracked


def test_execute_preserves_a_raw_filename_needing_percent_encoding(vault_root):
    (vault_root / "raw" / "My Notes (draft).md").write_text("raw\n", encoding="utf-8")
    d = {
        "title": "File a source page",
        "raw": "raw/My Notes (draft).md",
        "pages": [
            {
                "op": "create",
                "kind": "source",
                "title": "My Notes",
                "body": "# My Notes\n",
                "frontmatter": {
                    "raw_source": "[My Notes (draft).md](../../raw/My%20Notes%20%28draft%29.md)"
                },
                "edges": {},
            }
        ],
    }
    plan = ingest.IngestPlan.from_dict(d)
    ingest.execute(vault_root, plan)  # the encoded destination validates against the real file

    assert (vault_root / "raw" / "My Notes (draft).md").exists()
    page = Vault(vault_root).load("wiki/source/my-notes.md")
    assert page.get("raw_source") == (
        "[My Notes (draft).md](../../raw/My%20Notes%20%28draft%29.md)"
    )


# --- execution: no rollback on failure -------------------------------------------


def test_execute_leaves_written_files_uncommitted_on_commit_failure(vault_root, monkeypatch):
    import commit as commit_mod

    def _boom(root, manifest):
        raise commit_mod.GitError("simulated failure")

    monkeypatch.setattr(ingest.commit, "commit", _boom)

    plan = ingest.IngestPlan.from_dict(_plan_dict())
    with pytest.raises(commit_mod.GitError):
        ingest.execute(vault_root, plan)

    # the page was written to disk despite the commit failing
    assert (vault_root / "wiki" / "concept" / "prepared-statements.md").exists()
    status = _git(vault_root, "status", "--porcelain")
    assert "prepared-statements.md" in status


# --- action (#18: a researcher-saved synthesis is not an ingestion) ----------------


def test_execute_defaults_the_commit_action_to_ingest(vault_root):
    plan = ingest.IngestPlan.from_dict(_plan_dict())
    assert plan.action == "ingest"
    ingest.execute(vault_root, plan)
    body = _git(vault_root, "log", "-1", "--pretty=%B")
    assert body.startswith("ingest: Postgres tuning notes\n")


def test_execute_carries_the_plans_action_into_the_commit_subject(vault_root):
    plan = ingest.IngestPlan.from_dict(
        _plan_dict(title="What we decided about pooling", action="synthesize")
    )
    ingest.execute(vault_root, plan)
    body = _git(vault_root, "log", "-1", "--pretty=%B")
    assert body.startswith("synthesize: What we decided about pooling\n")
    assert "created: wiki/concept/prepared-statements.md" in body


def test_execute_saves_a_synthesis_page_with_source_edges(vault_root):
    """#18's shape: one synthesis/ page, source edges back to what it drew on."""
    plan = ingest.IngestPlan.from_dict(
        {
            "title": "How connection pooling is configured",
            "action": "synthesize",
            "source_date": "2026-07-28",
            "pages": [
                {
                    "op": "create",
                    "kind": "synthesis",
                    "title": "How connection pooling is configured",
                    "body": "# How connection pooling is configured\n\nThe answer.\n",
                    "frontmatter": {
                        "summary": "Pool sizing comes from the existing page",
                        "tags": ["db"],
                        "source_date": "2026-07-28",
                        "volatility": "evolving",
                    },
                    "edges": {"source": ["[Existing](../concept/existing.md)"]},
                }
            ],
        }
    )
    ingest.execute(vault_root, plan)

    page = Vault(vault_root).load(
        "wiki/synthesis/how-connection-pooling-is-configured.md"
    )
    assert page.get("source") == ["[Existing](../concept/existing.md)"]
    assert page.get("volatility") == "evolving"

    index = (vault_root / "wiki" / "_index.md").read_text(encoding="utf-8")
    assert "how-connection-pooling-is-configured.md" in index
    assert _git(vault_root, "status", "--porcelain") == ""


# --- CLI --------------------------------------------------------------------------


def test_cli_prints_commit_sha(vault_root, monkeypatch, capsys, tmp_path):
    import json

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan_dict()), encoding="utf-8")

    monkeypatch.chdir(vault_root)
    monkeypatch.delenv("WIKI_ROOT", raising=False)
    rc = ingest._main(["--plan", str(plan_path)])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == _git(vault_root, "rev-parse", "HEAD").strip()


def test_cli_prints_tool_call_summary_after_commit_sha(vault_root, monkeypatch, capsys, tmp_path):
    import json

    import session_state
    import tool_call_stats

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan_dict()), encoding="utf-8")

    monkeypatch.chdir(vault_root)
    monkeypatch.delenv("WIKI_ROOT", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-cli")

    log_path = tool_call_stats.log_path("sess-cli", session_state.sessions_dir(vault_root))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        json.dumps({"tool": "Bash", "prompt_id": "p1"}) + "\n"
        + json.dumps({"tool": "Write", "prompt_id": "p1"}) + "\n",
        encoding="utf-8",
    )

    rc = ingest._main(["--plan", str(plan_path)])
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[0] == _git(vault_root, "rev-parse", "HEAD").strip()
    assert out[1] == "Total tool calls: 2"
    assert "Bash" in out[2] and "Write" in out[3]
