"""OpenCode /save-conversation adapter — the OpenCode-specific caller.

Everything this script does is OpenCode-shaped, over the shared
``transcript_capture`` seam: the session id comes from
``$OPENCODE_SESSION_ID`` (injected into every shell env by the session-tracker
plugin's ``shell.env`` hook), is validated against the session-tracker state
(``.opencode/wiki-knowledge/sessions/``), the transcript is fetched via
``opencode export <sessionID>``, normalized from the export format (``info`` +
``messages[{info:{role}, parts[...]}]``) into ``(role, text)`` turns,
re-encoded into the Claude Code JSONL shape ``transcript_to_page()`` already
reads, and written by ``write_capture()``. The pure seam stays unchanged;
``save-session-to-vault.py`` (the Claude Code caller) is untouched.

A transcript written this way carries the same "Source: Claude Code session
transcript" line ``transcript_to_page()`` always emits — the seam is shared
verbatim by both hosts, and OpenCode captures inherit the generic label.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from transcript_capture import CaptureError, transcript_to_page, write_capture  # noqa: E402
from vault import resolve_vault_root  # noqa: E402


SESSIONS_SUBDIR = os.path.join(".opencode", "wiki-knowledge", "sessions")


def _sessions_dir(cwd: Path | None = None) -> Path:
    """The OpenCode session-tracker state dir for this project.

    The nearest ancestor of cwd containing ``.opencode/`` — where the
    session-tracker plugin writes, and the reader must agree even when cwd is
    a subdirectory; cwd itself when no ancestor holds the marker, so a path
    is always returned (it may not exist yet).
    """
    base = cwd if cwd is not None else Path.cwd()
    for ancestor in (base, *base.parents):
        if (ancestor / ".opencode").is_dir():
            return ancestor / SESSIONS_SUBDIR
    return base / SESSIONS_SUBDIR


def _session_is_tracked(session_id: str, state_dir: Path) -> bool:
    """``True`` iff the tracker recorded this session: the ``<id>.json`` file
    exists, parses, and names ``session_id`` back. A corrupt file counts as
    untracked, mirroring ``read_transcript_path``'s JSON-decode guard.
    """
    path = state_dir / f"{session_id}.json"
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return data.get("session_id") == session_id


def find_session_id(
    env: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
) -> tuple[str | None, str | None]:
    """Return ``(session_id, error_message)``; exactly one is ``None``.

    Three distinct failures, kept distinct so the user can tell them apart:
    no ``$OPENCODE_SESSION_ID`` (the session-tracker plugin's ``shell.env``
    hook must inject it); state directory not located (no ``.opencode/``
    ancestor of cwd, so the plugin has never recorded state in this project);
    located but no entry for this session (started before the plugin was
    installed).
    """
    env = env if env is not None else os.environ
    cwd_path = Path(cwd) if cwd is not None else Path.cwd()

    session_id = env.get("OPENCODE_SESSION_ID")
    if not session_id:
        return None, (
            "$OPENCODE_SESSION_ID is not set in this environment. "
            "(The session-tracker plugin's shell.env hook injects it; is the "
            "plugin installed and loaded in this project?)"
        )

    state_dir = _sessions_dir(cwd=cwd_path)
    if not state_dir.is_dir():
        return None, (
            "Could not locate OpenCode session-tracker state. Searched "
            f"{cwd_path.resolve()} and its ancestors for a '.opencode/' "
            "directory and did not find one. (Has the session-tracker plugin "
            "ever run in this project? Start a new session in the project "
            "root and try again.)"
        )

    if not _session_is_tracked(session_id, state_dir):
        return None, (
            f"No state recorded for session {session_id} under {state_dir}. "
            "(If this session was started before the session-tracker plugin "
            "was installed, it was never recorded; start a new session and "
            "try again.)"
        )

    return session_id, None


def export_transcript(session_id: str, *, command: str = "opencode") -> dict:
    """Run ``opencode export <sessionID>``; return the parsed export dict.

    **Strict:** raises :class:`CaptureError` when the CLI is absent from
    PATH, the command exits non-zero, or its stdout is not a JSON object.

    ``opencode export`` truncates its JSON when stdout is a pipe (observed on
    1.18.15: output stops ~64KB in), so stdout is written to a real temp file
    and read back from there — a file redirect carries the whole transcript.
    """
    if shutil.which(command) is None:
        raise CaptureError(
            f"{command} CLI is required but was not found on PATH"
        )
    tmp = tempfile.NamedTemporaryFile(
        mode="w+", encoding="utf-8", suffix=".json", delete=False,
    )
    out_path = tmp.name
    try:
        proc = subprocess.run(
            [command, "export", session_id],
            stdout=tmp,
            stderr=subprocess.PIPE,
            text=True,
        )
    finally:
        tmp.close()
    try:
        with open(out_path, encoding="utf-8") as f:
            stdout_text = f.read()
    finally:
        os.unlink(out_path)
    if proc.returncode != 0:
        raise CaptureError(
            f"opencode export failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    try:
        data = json.loads(stdout_text)
    except json.JSONDecodeError:
        raise CaptureError("opencode export returned invalid JSON") from None
    if not isinstance(data, dict):
        raise CaptureError("opencode export returned an unexpected shape")
    return data


def normalize_export(export: Mapping) -> list[tuple[str, str]]:
    """Map the OpenCode export format into ``(role, text)`` turns.

    The export is ``info`` + ``messages[{info:{role}, parts[{type:"text"}]}]``.
    Only ``user``/``assistant`` messages and ``type: "text"`` parts count —
    tool calls, reasoning, step markers, and patches are not the back-and-forth
    anyone re-reads later. Sub-agent work runs in its own OpenCode session, so
    it never appears in a parent session's export and needs no sidechain
    filter here. Multiple text parts in one message join with a blank line.
    """
    turns = []
    for message in export.get("messages") or []:
        if not isinstance(message, dict):
            continue
        info = message.get("info")
        role = info.get("role") if isinstance(info, dict) else None
        if role not in ("user", "assistant"):
            continue
        texts = []
        for part in message.get("parts") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text = (part.get("text") or "").strip()
                if text:
                    texts.append(text)
        if texts:
            turns.append((role, "\n\n".join(texts)))
    return turns


def encode_turns(turns) -> list[str]:
    """Re-encode ``(role, text)`` turns as the Claude Code JSONL shape
    ``transcript_to_page()`` reads, so the pure seam needs no change to serve
    OpenCode transcripts."""
    return [
        json.dumps({
            "type": role,
            "isMeta": False,
            "isSidechain": False,
            "message": {"role": role, "content": text},
        })
        for role, text in turns
    ]


def capture_session(
    *,
    wiki_root: Path | str,
    slug: str | None = None,
    env: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
    now: datetime | None = None,
    command: str = "opencode",
) -> str:
    """Resolve the current session, export + normalize its transcript, and
    write the capture; return its vault-relative path.

    The whole pipeline (find_session_id -> export_transcript ->
    normalize_export -> transcript_to_page -> write_capture) in one call.
    Raises CaptureError with a user-facing message on any failure; the
    individual steps remain available.
    """
    session_id, error = find_session_id(env=env, cwd=cwd)
    if error:
        raise CaptureError(error)
    assert session_id is not None  # exactly one of the pair is None

    export = export_transcript(session_id, command=command)
    lines = encode_turns(normalize_export(export))

    try:
        filename, markdown = transcript_to_page(
            lines,
            session_id=session_id,
            now=now or datetime.now(),
            slug=slug,
        )
    except ValueError as exc:
        raise CaptureError(f"Not enough conversation to save: {exc}") from exc

    return write_capture(
        wiki_root, filename, markdown, short_id=session_id.split("-")[0],
    )


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Save this OpenCode session's transcript.",
    )
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
