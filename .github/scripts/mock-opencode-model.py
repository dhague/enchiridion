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

One conditional path (added for #333): when a request offers the ``skill``
tool and its newest user message carries the trigger string
(``__CALL_SKILL__``), the mock replies with a ``function_call`` turn for the
``skill`` tool instead of the text turn. CI uses that to exercise a
generated subagent's ``skill`` permission gate end-to-end: an agent with
``skill: allow`` executes the call; one with ``skill: deny`` is never even
offered the tool. Both the presence-of-the-tool and newest-message
conditions matter — the title-generation request carries the trigger in its
history but no tools, and a subagent's later requests re-send the whole
history, so the mock must fire the tool call only once.

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
MODEL_ID = "gpt-4o-mini"
RESPONSE_ID = "resp_ci_mock_000000000000000000"
MESSAGE_ID = "msg_ci_mock_000000000000000000"
TEXT = "This is the CI mock model response."

#: The ids the ``skill`` function_call turn carries (the ``call_id`` is what
#: opencode's AI SDK reads as the tool-call id).
FC_ID = "fc_ci_000000000000000001"
CALL_ID = "call_ci_000000000000000001"

#: Magic trigger string: when any user message in the request body contains
#: it, the mock replies with a ``function_call`` turn for the ``skill`` tool
#: instead of the canned text turn. CI uses this to exercise a generated
#: subagent's ``skill`` permission gate (an agent with ``skill: allow``
#: executes the call; one without it is denied).
TRIGGER = "__CALL_SKILL__"

#: The ``skill`` tool call the mock emits: ``name`` is the one required
#: argument opencode's ``skill`` tool schema declares (verified against the
#: tool JSON opencode actually sends the model).
SKILL_NAME = "wiki-conventions"
SKILL_ARGS = json.dumps({"name": SKILL_NAME})


def _event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _has_trigger(body: bytes) -> bool:
    """True when a request should get the ``skill`` function_call turn.

    Three conditions: the request offers the ``skill`` tool (so a subagent
    could actually call it — the title-generation request, which carries the
    trigger in its user message but no tools, must keep the text turn), the
    *last* input message carries the trigger string (a request later in the
    same session re-sends the whole history, original trigger included, so
    only the newest message decides — otherwise the mock would re-emit the
    tool call forever), and that message is not a tool result. The content is
    a list of parts (``input_text``) for the OpenAI Responses API, but a
    plain string is tolerated for robustness."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return False
    if not any(
        isinstance(tool, dict) and tool.get("name") == "skill"
        for tool in data.get("tools", [])
    ):
        return False
    messages = data.get("input", [])
    if not messages:
        return False
    message = messages[-1]
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if isinstance(content, str):
        return TRIGGER in content
    if not isinstance(content, list):
        return False
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "tool_result":
            continue
        if any(
            isinstance(part.get(key), str) and TRIGGER in part[key]
            for key in ("text", "input_text", "content")
        ):
            return True
    return False


def _function_call_sequence(model: str) -> str:
    """The SSE event sequence for one ``skill`` tool-call turn.

    Same ``response.*`` scaffolding as the text turn, with a single
    ``function_call`` output item for the ``skill`` tool, its JSON arguments
    streamed as ``response.function_call_arguments.delta`` fragments. The
    item carries ``call_id`` (what opencode's AI SDK reads as the tool call
    id) and ``output_item.done`` carries the assembled arguments — there is
    no ``response.function_call_arguments.done`` event in the SDK this CI
    targets, so none is emitted.
    """
    now = int(time.time())
    n = 0

    def seq() -> int:
        nonlocal n
        n += 1
        return n

    fc_id = FC_ID
    call_id = CALL_ID

    resp = {
        "id": RESPONSE_ID,
        "object": "response",
        "created_at": now,
        "status": "in_progress",
        "model": model,
        "output": [],
        "usage": None,
    }
    item = {
        "id": fc_id,
        "type": "function_call",
        "call_id": call_id,
        "name": "skill",
        "arguments": "",
        "status": "in_progress",
    }

    chunks = []
    chunks.append(_event("response.created", {
        "type": "response.created", "response": resp, "sequence_number": seq(),
    }))
    chunks.append(_event("response.in_progress", {
        "type": "response.in_progress", "response": resp, "sequence_number": seq(),
    }))
    chunks.append(_event("response.output_item.added", {
        "type": "response.output_item.added", "item": item, "output_index": 0,
        "sequence_number": seq(),
    }))
    # The arguments arrive as delta fragments, like text does.
    step = 3
    for i in range(0, len(SKILL_ARGS), step):
        chunks.append(_event("response.function_call_arguments.delta", {
            "type": "response.function_call_arguments.delta", "item_id": fc_id,
            "output_index": 0, "delta": SKILL_ARGS[i:i + step],
            "sequence_number": seq(),
        }))
    done_item = {**item, "arguments": SKILL_ARGS, "status": "completed"}
    chunks.append(_event("response.output_item.done", {
        "type": "response.output_item.done", "item": done_item, "output_index": 0,
        "sequence_number": seq(),
    }))
    chunks.append(_event("response.completed", {
        "type": "response.completed",
        "response": {
            "id": RESPONSE_ID, "object": "response", "created_at": now,
            "status": "completed", "model": model,
            "output": [done_item],
            "usage": {"input_tokens": 10, "output_tokens": len(SKILL_ARGS),
                      "total_tokens": 10 + len(SKILL_ARGS)},
        },
        "sequence_number": seq(),
    }))
    chunks.append("data: [DONE]\n\n")
    return "".join(chunks)


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
        body = b""
        if length:
            body = self.rfile.read(length)
        if self.path.startswith("/v1/responses"):
            if _has_trigger(body):
                payload = _function_call_sequence(MODEL_ID)
            else:
                payload = _sequence(MODEL_ID)
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
