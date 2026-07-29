"""CLI adapter for /save-conversation: argv parsing and wiring only.
Pipeline lives in transcript_capture.py. Prints the vault-relative
path of the raw file written, for the skill to pass to wiki-ingest."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from transcript_capture import CaptureError, capture_session  # noqa: E402
from vault import resolve_vault_root  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="Save this session's transcript.")
    parser.add_argument(
        "--slug",
        default=None,
        help="Phrase naming what this session covered; sanitized, first-save only.",
    )
    args = parser.parse_args(argv)

    try:
        print(capture_session(wiki_root=resolve_vault_root(), slug=args.slug))
    except CaptureError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
