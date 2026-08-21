#!/bin/bash
# PROTOTYPE — throwaway. Answers: how does a user ingest a file in OpenCode
# via the wiki-knowledge plugin, and do scripts/*-opencode.py need changes?
# Ticket #293. Not production; capture findings, then delete.
#
# Simulates the full agent flow without a model: what the wiki-ingest subagent
# would do is driven here as the same shell invocations the skill text
# prescribes. Prints state at each step.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PLUGIN_ROOT="$REPO/wiki-plugin"
VENV_PY="$PLUGIN_ROOT/.venv/bin/python"
SCRATCH="$(dirname "$0")/vault"
WIKI_SKILLS="$PLUGIN_ROOT/skills"

echo "=============================================================="
echo "STEP 0 — prerequisites"
echo "=============================================================="
echo "plugin_root      = $PLUGIN_ROOT"
echo "committed bundle = $(ls "$PLUGIN_ROOT/scripts/cli.cjs" "$PLUGIN_ROOT/scripts/node-sqlite3-wasm.wasm" 2>/dev/null | tr '\n' ' ')"
echo "node on PATH     = $(command -v node || echo MISSING)"
if command -v bun >/dev/null 2>&1; then
  echo "bun on PATH      = $(command -v bun)"
else
  echo "bun on PATH      = MISSING (OpenCode embeds Bun internally; not exposed as CLI)"
fi
echo

# --- fresh vault ---
rm -rf "$SCRATCH"
mkdir -p "$SCRATCH/raw/inbox"
cd "$SCRATCH"
git init -q .
mkdir -p wiki/concepts wiki/entities wiki/sources wiki/synthesis
cat > raw/inbox/meeting-2026-08-20.md <<'EOF'
# Team Sync — 2026-08-20

Darren and Maya discussed the FTS5 search index migration. Maya confirmed the
bm25 weights (0.0,10.0,5.0,1.0) are final. Decided: the index stays a
materialised view of HEAD, read from git blobs. Next: wire the freshness guard
into CI as a PR-time byte-equality job.
EOF
git add -A && git -c user.email=proto@example.com -c user.name="Proto" commit -qm "seed vault"

echo "=============================================================="
echo "STEP 1 — user runs install-opencode.py (one-time, per vault)"
echo "=============================================================="
cat > models.json <<'EOF'
{"sonnet":"anthropic/claude-sonnet-4-5","haiku":"anthropic/claude-haiku-4-5"}
EOF
"$VENV_PY" "$PLUGIN_ROOT/scripts/install-opencode.py" --plugin-root "$PLUGIN_ROOT" --model-config models.json
echo
echo "--- marker the skill text tells the agent to read on OpenCode: ---"
cat .opencode/wiki-knowledge/config.json
echo

echo "=============================================================="
echo "STEP 2 — user runs /wiki-ingest raw/inbox/meeting-2026-08-20.md"
echo "  (command wiki-ingest.md has agent: wiki-ingest, so OpenCode"
echo "   spawns the wiki-ingest subagent directly)"
echo "=============================================================="
echo "generated command:"
cat .opencode/commands/wiki-ingest.md
echo
echo "--- the subagent loads wiki-ingest + wiki-conventions skills ---"
echo "    (SEE GAP #1 below: generated agent denies the skill tool)"
cat .opencode/agents/wiki-ingest.md
echo

echo "=============================================================="
echo "STEP 3 — subagent resolves <plugin-root> from the marker, then"
echo "         shells out exactly as wiki-ingest SKILL.md prescribes"
echo "=============================================================="
PLUGIN_ROOT_RESOLVED="$(python3 -c "import json;print(json.load(open('.opencode/wiki-knowledge/config.json'))['plugin_root'])")"
echo "resolved plugin_root = $PLUGIN_ROOT_RESOLVED"
echo

echo "--- 3a. place (vault-root resolved from cwd; dedicated mode) ---"
env -i PATH="/usr/local/bin:/usr/bin:/bin" "$PLUGIN_ROOT_RESOLVED/bin/enchiridion" vault
echo

echo "--- 3b. subagent drafts plan.json (would be authored by the model) ---"
cat > plan.json <<'EOF'
{
  "title": "Team Sync 2026-08-20",
  "source_date": "2026-08-20",
  "raw": "raw/inbox/meeting-2026-08-20.md",
  "pages": [
    {
      "op": "create",
      "kind": "source",
      "title": "Team Sync 2026-08-20",
      "body": "Meeting notes from the 2026-08-20 team sync, covering the FTS5 search-index migration.",
      "frontmatter": {"summary": "Team sync notes on FTS5 index migration decisions.", "raw_source": true},
      "edges": {}
    },
    {
      "op": "create",
      "kind": "concept",
      "title": "FTS5 search index bm25 weights",
      "body": "The search index uses bm25 weights (0.0,10.0,5.0,1.0) as confirmed on 2026-08-20.",
      "frontmatter": {"summary": "Final bm25 weights for the FTS5 index.", "tags": ["search-index"], "source_date": "2026-08-20", "volatility": "stable"},
      "edges": {"source": ["wiki/sources/team-sync-2026-08-20.md"]}
    }
  ]
}
EOF

echo "--- 3c. discover --plan (FTS5 index builds; .wasm loads) ---"
env -i PATH="/usr/local/bin:/usr/bin:/bin" "$PLUGIN_ROOT_RESOLVED/bin/enchiridion" discover --plan plan.json --tags-containing "search-index" --tag-count "search-index"
echo

echo "--- 3d. ingest --plan (validate → write → commit) ---"
env -i PATH="/usr/local/bin:/usr/bin:/bin" "$PLUGIN_ROOT_RESOLVED/bin/enchiridion" ingest --plan plan.json
echo
echo "--- vault after: ---"
git log --oneline -2
git ls-files wiki/

echo
echo "=============================================================="
echo "VERDICT"
echo "=============================================================="
echo "The full ingest flow WORKS on an OpenCode host that has node on"
echo "PATH (this host: node $(node --version)). install-opencode.py and"
echo "generate-opencode.py need NO change for the happy path: the marker"
echo "points plugin_root at the clone, the clone carries the committed"
echo "bundle, the shim resolves it, node runs it."
echo
echo "TWO GAPS FOUND (see findings notes for detail):"
echo "  GAP #1 — generated subagents deny the skill tool:"
echo "           permission: {'*': deny} with no 'skill: allow', while the"
echo "           generator drops CC's skills: preload. The agent therefore"
echo "           CANNOT load wiki-ingest/wiki-conventions on OpenCode."
echo "  GAP #2 — the shim execs 'node'; OpenCode guarantees only its"
echo "           embedded Bun (not exposed as a CLI). On a host with"
echo "           OpenCode but no node, ingest fails. This host has node,"
echo "           so it passes here; the research doc's 'bun is guaranteed"
echo "           for skills' inference is refuted on this host."