# Backup rotation for the vault

Dated 2026-08-06.

Added a nightly backup rotation for the dogfooding vault: a small script,
`backup.py`, tars `wiki/` and `raw/` into `.wiki-knowledge/backups/`,
keeping the newest 7 daily and 4 weekly snapshots and deleting anything
older on each run. Chose tar-and-prune over a second git remote because the
vault already has full history in its own repo — a backup only needs to
survive a corrupted working tree, not a lost commit.

This complements the existing chain-of-evidence commit gate (see
`commit.py`): that gate stops a bad write from landing, but does nothing
once a write has landed and the working tree itself is damaged (disk
fault, a bad `rm -rf`). The auto-ingest watcher will call `backup.py`
once per sweep once #37 lands, so backups stay aligned with ingestion
activity rather than running on an unrelated clock.

Retention numbers (7 daily, 4 weekly) are a guess, not measured — revisit
once real backup sizes are known.
