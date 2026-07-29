"""CLI adapter for the /save-conversation skill: save this session's
transcript into the vault's raw inbox.

Run manually (via the save-conversation skill), not as a hook, so there is
no hook JSON payload here to read transcript_path from directly. Instead,
the plugin's `SessionStart` hook (hooks/store_transcript_path.py) records
transcript_path per session_id, under this project's
`.claude/wiki-knowledge/sessions/` (gitignored), as each session starts;
this script looks itself up by the $CLAUDE_CODE_SESSION_ID env var and its
own cwd, via transcript_capture.find_transcript_path().

Prints the vault-relative path of the raw file it wrote, so the calling
skill can pass it straight to wiki-ingest.

The JSONL filter + markdown render + filename scheme + vault write live in
transcript_capture.py (the reusable capability); this module is argv
parsing and wiring only.
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from transcript_capture import (  # noqa: E402
    find_transcript_path,
    transcript_to_page,
    write_capture,
)
from vault import resolve_vault_root  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Save this session's transcript into the vault's raw inbox.",
    )
    parser.add_argument(
        "--slug",
        default=None,
        help=(
            "Short phrase naming what this session covered, used in the "
            "filename. Sanitized to [a-z0-9-]; ignored if nothing survives. "
            "Only used on the first save - a re-save keeps the bound name."
        ),
    )
    args = parser.parse_args(argv)

    transcript_path, error = find_transcript_path()
    if error:
        print(error, file=sys.stderr)
        sys.exit(1)

    session_id = os.path.splitext(os.path.basename(transcript_path))[0]

    with open(transcript_path, encoding="utf-8") as f:
        jsonl_lines = f.readlines()

    try:
        filename, markdown = transcript_to_page(
            jsonl_lines, session_id=session_id, now=datetime.now(), slug=args.slug,
        )
    except ValueError as exc:
        print(f"Not enough conversation to save: {exc}", file=sys.stderr)
        sys.exit(1)

    # Vault root is resolved at call time, not at import, so the module
    # is importable in a test environment that has no vault on disk.
    wiki_root = resolve_vault_root()
    print(
        write_capture(
            wiki_root, filename, markdown, short_id=session_id.split("-")[0],
        )
    )


if __name__ == "__main__":
    main()
