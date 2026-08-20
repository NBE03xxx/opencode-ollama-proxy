"""OpenCode / Chat Completions protocol conversion."""

import time
from typing import Any, Callable, Iterable, Iterator
import uuid

from common import (
    StreamEvent,
    convert_message_to_ollama,
    normalize_tool_arguments,
)


IdFactory = Callable[[str, int], str]


def _new_id(prefix: str, length: int) -> str:
    return prefix + uuid.uuid4().hex[:length]


def build_chat_request(body: dict[str, Any]) -> tuple[str, bool, dict[str, Any]]:
    model = body.get("model", "")
    messages = body.get("messages", [])
    stream = bool(body.get("stream", False))
    request: dict[str, Any] = {
        "model": model,
        "messages": [convert_message_to_ollama(message) for message in messages],
        "stream": stream,
    }

    tools = body.get("tools")
    tool_choice = body.get("tool_choice")
    if isinstance(tools, list) and tools and tool_choice != "none":
        request["tools"] = tools
    if isinstance(tool_choice, dict):
        if tool_choice.get("type") == "function" and isinstance(
            tool_choice.get("function"), dict
        ):
            request["tool_choice"] = tool_choice

    max_tokens = body.get("max_tokens")
    if max_tokens is not None:
        try:
            value = int(max_tokens)
            if value > 0:
                request["options"] = {"num_predict": value}
        except (TypeError, ValueError):
            pass
    return model, stream, request


def chat_completion_from_ollama(
    data: dict[str, Any],
    model: str,
    *,
    created: int | None = None,
    id_factory: IdFactory = _new_id,
) -> dict[str, Any]:
    message = data.get("message", {})
    if not isinstance(message, dict):
        message = {}
    content = message.get("content", "")
    openai_tool_calls = []
    tool_calls = message.get("tool_calls", [])
    if isinstance(tool_calls, list):
        for index, tool_call in enumerate(tool_calls):
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function", {})
            if not isinstance(function, dict):
                function = {}
            openai_tool_calls.append(
                {
                    "id": tool_call.get("id") or id_factory("call_", 16),
                    "type": "function",
                    "index": tool_call.get("index", index),
                    "function": {
                        "name": function.get("name", ""),
                        "arguments": normalize_tool_arguments(
                            function.get("arguments", {})
                        ),
                    },
                }
            )

    prompt_tokens = data.get("prompt_eval_count", 0)
    completion_tokens = data.get("eval_count", 0)
    response_message: dict[str, Any] = {
        "role": "assistant",
        "content": content if content != "" else None,
    }
    if openai_tool_calls:
        response_message["tool_calls"] = openai_tool_calls
    return {
        "id": id_factory("chatcmpl-", 16),
        "object": "chat.completion",
        "created": int(time.time()) if created is None else created,
        "model": model,
        "system_fingerprint": "fp_ollama",
        "choices": [
            {
                "index": 0,
                "message": response_message,
                "finish_reason": (
                    "tool_calls"
                    if openai_tool_calls
                    else data.get("done_reason", "stop")
                ),
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def chat_stream_events(
    items: Iterable[dict[str, Any]],
    model: str,
    *,
    completion_id: str | None = None,
    created: int | None = None,
    id_factory: IdFactory = _new_id,
) -> Iterator[StreamEvent]:
    completion_id = completion_id or id_factory("chatcmpl-", 16)
    created = int(time.time()) if created is None else created
    first_chunk = True
    prompt_tokens = 0
    completion_tokens = 0
    saw_tool_call = False

    def chunk(delta: dict[str, Any], finish_reason: str | None = None):
        return {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }
            ],
        }

    for data in items:
        message = data.get("message", {})
        if not isinstance(message, dict):
            message = {}
        role = message.get("role", "assistant")
        content = message.get("content", "")

        if data.get("prompt_eval_count") is not None:
            prompt_tokens = data.get("prompt_eval_count", prompt_tokens)
        if data.get("eval_count") is not None:
            completion_tokens = data.get("eval_count", completion_tokens)

        if first_chunk:
            yield StreamEvent(chunk({"role": role}))
            first_chunk = False
        if content:
            yield StreamEvent(chunk({"content": content}))

        tool_calls = message.get("tool_calls", [])
        if isinstance(tool_calls, list):
            for index, tool_call in enumerate(tool_calls):
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function", {})
                if not isinstance(function, dict):
                    function = {}
                call = {
                    "index": tool_call.get("index", index),
                    "id": tool_call.get("id") or id_factory("call_", 16),
                    "type": "function",
                    "function": {
                        "name": function.get("name", ""),
                        "arguments": normalize_tool_arguments(
                            function.get("arguments", {})
                        ),
                    },
                }
                yield StreamEvent(chunk({"tool_calls": [call]}))
                saw_tool_call = True

        if data.get("done"):
            finish_reason = (
                "tool_calls"
                if saw_tool_call
                else data.get("done_reason", "stop")
            )
            final = chunk({}, finish_reason)
            final["usage"] = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
            yield StreamEvent(final)
            break
    yield StreamEvent(terminal=True)
