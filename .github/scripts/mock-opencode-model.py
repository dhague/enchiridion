"""Mock OpenAI Responses API for CI validation of the OpenCode wiring (#94).

``opencode run`` drives its model through the OpenAI Responses API (POST
``/v1/responses``, streamed as SSE). CI has no real model, so this server
stands in for one: every request gets the same canned assistant turn,
streamed in the exact wire format opencode's bundled ``@ai-sdk/openai``
parses (full ``response.*`` event names plus ``sequence_number`` on each
chunk). If opencode stops understanding the mock, the ``response.*`` event
set here is what to re-check against opencode's AI SDK.

The response is deliberately deterministic: one text part, ``stop`` finish,
fixed token usage. CI asserts opencode surfaces (skills, agents, commands)
load against this server without error — the model's answer itself is
irrelevant to the test.

Run::

    python mock-opencode-model.py [--port 8799]

Stdlib only, so CI needs no extra Python deps.
"""
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

#: Matches the model id the CI throwaway config maps to (openai/gpt-4o-mini).
RESPONSE_ID = "resp_ci_mock_000000000000000000"
MESSAGE_ID = "msg_ci_mock_000000000000000000"
TEXT = "This is the CI mock model response."


def _event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _sequence(model: str) -> str:
    """The canned SSE event sequence for one assistant text turn."""
    now = int(time.time())
    n = 0

    def seq() -> int:
        nonlocal n
        n += 1
        return n

    resp = {
        "id": RESPONSE_ID,
        "object": "response",
        "created_at": now,
        "status": "in_progress",
        "model": model,
        "output": [],
        "usage": None,
    }

    chunks = []
    chunks.append(_event("response.created", {
        "type": "response.created", "response": resp, "sequence_number": seq(),
    }))
    chunks.append(_event("response.in_progress", {
        "type": "response.in_progress", "response": resp, "sequence_number": seq(),
    }))
    chunks.append(_event("response.output_item.added", {
        "type": "response.output_item.added",
        "item": {"id": MESSAGE_ID, "type": "message", "status": "in_progress",
                 "content": [], "phase": "final_answer", "role": "assistant"},
        "output_index": 0, "sequence_number": seq(),
    }))
    chunks.append(_event("response.content_part.added", {
        "type": "response.content_part.added", "content_index": 0,
        "item_id": MESSAGE_ID, "output_index": 0,
        "part": {"type": "output_text", "annotations": [], "text": ""},
        "sequence_number": seq(),
    }))
    for i, char in enumerate(TEXT):
        chunks.append(_event("response.output_text.delta", {
            "type": "response.output_text.delta", "content_index": 0,
            "delta": char, "item_id": MESSAGE_ID, "output_index": 0,
            "sequence_number": seq(),
        }))
    chunks.append(_event("response.output_text.done", {
        "type": "response.output_text.done", "content_index": 0,
        "item_id": MESSAGE_ID, "output_index": 0, "text": TEXT,
        "sequence_number": seq(),
    }))
    chunks.append(_event("response.content_part.done", {
        "type": "response.content_part.done", "content_index": 0,
        "item_id": MESSAGE_ID, "output_index": 0,
        "part": {"type": "output_text", "annotations": [], "text": TEXT},
        "sequence_number": seq(),
    }))
    chunks.append(_event("response.output_item.done", {
        "type": "response.output_item.done",
        "item": {"id": MESSAGE_ID, "type": "message", "status": "completed",
                 "content": [{"type": "output_text", "annotations": [], "text": TEXT}],
                 "phase": "final_answer", "role": "assistant"},
        "output_index": 0, "sequence_number": seq(),
    }))
    chunks.append(_event("response.completed", {
        "type": "response.completed",
        "response": {
            "id": RESPONSE_ID, "object": "response", "created_at": now,
            "status": "completed", "model": model,
            "output": [{"id": MESSAGE_ID, "type": "message", "status": "completed",
                        "role": "assistant",
                        "content": [{"type": "output_text", "annotations": [], "text": TEXT}]}],
            "usage": {"input_tokens": 10, "output_tokens": len(TEXT),
                      "total_tokens": 10 + len(TEXT)},
        },
        "sequence_number": seq(),
    }))
    chunks.append("data: [DONE]\n\n")
    return "".join(chunks)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401 - silence per-request noise
        pass

    def _respond(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        if self.path.startswith("/v1/responses"):
            payload = _sequence("gpt-4o-mini")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload.encode())
            return
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = _respond
    do_GET = _respond


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args(argv)
    print(f"mock-opencode-model listening on 127.0.0.1:{args.port}", flush=True)
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
