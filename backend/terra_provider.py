"""Adapt the Harness Messages transport to OpenAI Responses for Terra chat."""

from contextlib import contextmanager
import json
import os

import httpx

from . import openai_relay
from .provider_errors import HarnessTurnError


ENDPOINT = "https://api.openai.com/v1/responses"


def payload(body, reasoning, effort):
    inputs = []
    for message in body["messages"]:
        role, content = message["role"], message["content"]
        if isinstance(content, str):
            inputs.append({"role": role, "content": content})
            continue
        for block in content:
            kind = block["type"]
            if kind == "text":
                if block["text"]:
                    inputs.append({"role": role, "content": block["text"]})
            elif kind == "image":
                source = block["source"]
                if source["type"] != "base64":
                    raise ValueError("Expected an inline image")
                inputs.append(
                    {
                        "role": role,
                        "content": [
                            {
                                "type": "input_image",
                                "image_url": f"data:{source['media_type']};base64,{source['data']}",
                            }
                        ],
                    }
                )
            elif kind == "tool_use":
                inputs.extend(reasoning.get(block["id"], []))
                inputs.append(
                    {
                        "type": "function_call",
                        "call_id": block["id"],
                        "name": block["name"],
                        "arguments": json.dumps(block["input"], ensure_ascii=False),
                    }
                )
            elif kind == "tool_result":
                result = block["content"]
                inputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": block["tool_use_id"],
                        "output": (
                            result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
                        ),
                    }
                )
            elif kind not in ("thinking", "redacted_thinking"):
                raise ValueError("Unsupported chat content block")
    system = body.get("system", "")
    return {
        "model": "gpt-6.1-terra",
        "input": inputs,
        "instructions": (
            system if isinstance(system, str) else "\n".join(b["text"] for b in system if b["type"] == "text")
        ),
        "tools": [
            {
                "type": "function",
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t["input_schema"],
                "strict": False,
            }
            for t in body.get("tools", [])
        ],
        "store": False,
        "stream": True,
        "parallel_tool_calls": False,
        "include": ["reasoning.encrypted_content"],
        "reasoning": {"effort": effort},
    }


def frame(kind, **fields):
    data = json.dumps({"type": kind, **fields}, ensure_ascii=False)
    return f"event: {kind}\ndata: {data}\n\n".encode()


def messages_stream(response, reasoning):
    index = 0
    used_tool = False
    pending_reasoning = []
    for line in response.iter_lines():
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if raw == "[DONE]":
            break
        event = json.loads(raw)
        kind = event["type"]
        if kind == "response.created":
            yield frame(
                "message_start",
                message={
                    "id": event["response"]["id"],
                    "type": "message",
                    "role": "assistant",
                    "model": "gpt-6.1-terra",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            )
        elif kind == "response.content_part.added" and event["part"]["type"] == "output_text":
            yield frame("content_block_start", index=index, content_block={"type": "text", "text": ""})
        elif kind == "response.output_text.delta":
            yield frame(
                "content_block_delta", index=index, delta={"type": "text_delta", "text": event["delta"]}
            )
        elif kind == "response.content_part.done" and event["part"]["type"] == "output_text":
            yield frame("content_block_stop", index=index)
            index += 1
        elif kind == "response.output_item.done":
            item = event["item"]
            if item["type"] == "reasoning":
                pending_reasoning.append(item)
            elif item["type"] == "function_call":
                used_tool = True
                reasoning[item["call_id"]] = pending_reasoning
                pending_reasoning = []
                yield frame(
                    "content_block_start",
                    index=index,
                    content_block={
                        "type": "tool_use",
                        "id": item["call_id"],
                        "name": item["name"],
                        "input": {},
                    },
                )
                yield frame(
                    "content_block_delta",
                    index=index,
                    delta={"type": "input_json_delta", "partial_json": item["arguments"]},
                )
                yield frame("content_block_stop", index=index)
                index += 1
        elif kind == "response.completed":
            usage = event["response"].get("usage") or {}
            counts = {}
            if type(usage.get("output_tokens")) is int:
                counts["output_tokens"] = usage["output_tokens"]
            total = usage.get("input_tokens")
            cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
            if type(total) is int and type(cached) is int and 0 <= cached <= total:
                counts.update(
                    input_tokens=total - cached, cache_read_input_tokens=cached, cache_creation_input_tokens=0
                )
            yield frame(
                "message_delta",
                delta={"stop_reason": "tool_use" if used_tool else "end_turn", "stop_sequence": None},
                usage=counts,
            )
            yield frame("message_stop")
            return
        elif kind == "response.refusal.done":
            raise HarnessTurnError("refusal")
        elif kind in {"response.incomplete", "response.failed", "error"}:
            details = (event.get("response") or {}).get("incomplete_details") or {}
            raise HarnessTurnError(
                "max-tokens" if details.get("reason") == "max_output_tokens" else "incomplete"
            )
    raise HarnessTurnError("incomplete")


@contextmanager
def stream(client, body, reasoning, *, cancelled=None):
    if openai_relay.configured():
        upstream_context = openai_relay.response(body, cancelled)
    else:
        headers = {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"].strip()}
        upstream_context = client.stream("POST", ENDPOINT, headers=headers, json=body)
    with upstream_context as upstream:
        upstream.raise_for_status()
        adapted = httpx.Response(
            200,
            request=upstream.request,
            content=messages_stream(upstream, reasoning),
            headers={"Content-Type": "text/event-stream"},
        )
        try:
            yield adapted
        finally:
            adapted.close()
