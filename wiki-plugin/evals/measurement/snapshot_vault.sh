#!/usr/bin/env bash
# Snapshot the dogfooding vault into a throwaway temp dir and reindex it.
#
# Free to create, byte-identical to real conditions (so overlap-candidate
# density in the copy matches the live vault), re-runnable as often as
# needed, and leaves no commits in the real vault. See issue #104.
#
# Usage: snapshot_vault.sh [source_vault_root]
#   source_vault_root defaults to $WIKI_ROOT.
# Prints the snapshot's path on stdout (and nothing else) on success.
set -euo pipefail

SRC="${1:-${WIKI_ROOT:-}}"
if [ -z "$SRC" ]; then
    echo "usage: snapshot_vault.sh [source_vault_root]  (or set \$WIKI_ROOT)" >&2
    exit 1
fi
if [ ! -d "$SRC/wiki" ]; then
    echo "not a vault root (no wiki/ dir): $SRC" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$(mktemp -d -t wiki-vault-snapshot-XXXXXX)"

cp -r "$SRC/." "$DEST/"
WIKI_ROOT="$DEST" python3 "$SCRIPT_DIR/../../scripts/search.py" --reindex --full >&2

echo "$DEST"
