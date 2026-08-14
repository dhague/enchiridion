# Measurement harness (#104)

Produces a like-for-like before/after tool-call figure for changes to
`wiki-ingest`, using the instrumentation from #100 (`enchiridion hook
post-tool-use` + `enchiridion tool-call-stats`, printed by `enchiridion
ingest` after every commit).

## Why a snapshot, not the eval fixture

`wiki-plugin/evals/` is otherwise empty (#47 is still open), and a sparse
synthetic vault would surface fewer overlap candidates than the live
dogfooding vault — under-representing exactly the read-heavy behaviour
these changes target. So the harness snapshots the real vault instead:
free to create, byte-identical to real conditions, re-runnable, and leaves
no commits in the real vault.

## Files

- `snapshot_vault.sh` — `cp -r`s `$WIKI_ROOT` (or an explicit path) into a
  fresh temp dir and reindexes it (`enchiridion search --reindex --full`). Prints
  the snapshot path on stdout.
- `fixed-test-note.md` — the committed test document, ~150 words,
  comparable in size to the run that triggered #98. References two
  existing dogfooding-vault concepts (`commit.py`'s chain-of-evidence
  gate, the auto-ingest watcher) so it exercises the overlap path, plus
  new material so it also exercises `op: "create"`.

## Running one measurement

Requires a Claude Code checkout with the `claude` CLI on `$PATH`, and a
permission mode that allows a non-interactive agent session to write
(`--permission-mode acceptEdits`) — this is a real ingest run and needs to
be approved as such; it isn't something to route around from inside
another agent session.

```bash
# 1. Snapshot the live vault
DEST=$(wiki-plugin/evals/measurement/snapshot_vault.sh)

# 2. Drop the fixed test note into the snapshot's raw/ folder
mkdir -p "$DEST/raw/notes"
cp wiki-plugin/evals/measurement/fixed-test-note.md \
   "$DEST/raw/notes/2026-08-06-test-backup-rotation.md"

# 3. Run the ingest as its own headless session, from inside the snapshot,
#    with WIKI_ROOT pointed at it. ingest.py prints the tool-call summary
#    (from #100) after the commit SHA — that's the number to record.
cd "$DEST"
WIKI_ROOT="$DEST" claude -p \
  "/wiki-ingest raw/notes/2026-08-06-test-backup-rotation.md" \
  --permission-mode acceptEdits

# 4. Clean up
rm -rf "$DEST"
```

## Before/after protocol (#104)

1. `git checkout 9c4977f` (merge of #100 — instrumentation exists, but
   none of #101/#102/#103 have landed yet) in a worktree, run the steps
   above, record the `Total tool calls` / `Prompts (proxy for turns)` line
   ingest.py prints.
2. `git checkout main` (or the commit under test), repeat.
3. Report both numbers, plus the calls-per-prompt ratio for each, on #98.

The comparison is single-run, not an average — acceptable per the note in
#104 on attribution: the packaging measures once at the end rather than
after each change, so an individual regression could be masked by
improvements elsewhere. That's a known limitation of the resulting number,
not a bug in the harness.
