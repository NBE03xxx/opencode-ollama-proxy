"""Claude Code / Anthropic Messages API protocol conversion."""

from copy import deepcopy
import json
from typing import Any, Callable, Iterable, Iterator
import uuid

from common import StreamEvent, normalize_tool_arguments


IdFactory = Callable[[str, int], str]
EMPTY_RESPONSE_TEXT = (
    "The model returned no visible text or tool call. "
    "If thinking is enabled, disable OLLAMA_THINK or increase max_tokens."
)


class AnthropicRequestError(ValueError):
    """Raised when an Anthropic Messages request is structurally invalid."""


def _new_id(prefix: str, length: int) -> str:
    return prefix + uuid.uuid4().hex[:length]


def anthropic_error(
    message: str,
    error_type: str = "api_error",
) -> dict[str, Any]:
    return {"type": "error", "error": {"type": error_type, "message": message}}


def _text_from_blocks(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise AnthropicRequestError("content must be a string or an array")
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            raise AnthropicRequestError("content blocks must be objects")
        if block.get("type") == "text":
            text = block.get("text", "")
            if not isinstance(text, str):
                raise AnthropicRequestError("text block text must be a string")
            parts.append(text)
    return "".join(parts)


def _tool_result_text(block: dict[str, Any]) -> str:
    content = block.get("content", "")
    if isinstance(content, str):
        result = content
    elif isinstance(content, list):
        parts = []
        for nested in content:
            if isinstance(nested, dict) and nested.get("type") == "text":
                text = nested.get("text", "")
                if isinstance(text, str):
                    parts.append(text)
        result = "".join(parts)
    elif content is None:
        result = ""
    else:
        result = str(content)
    if block.get("is_error"):
        return "Tool execution error: " + result
    return result


def anthropic_messages_to_ollama(
    system: Any,
    messages: Any,
) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        raise AnthropicRequestError("messages must be an array")

    converted: list[dict[str, Any]] = []
    system_text = _text_from_blocks(system)
    if system_text:
        converted.append({"role": "system", "content": system_text})

    for message in messages:
        if not isinstance(message, dict):
            raise AnthropicRequestError("messages must contain objects")
        role = message.get("role")
        if role not in ("user", "assistant", "system", "developer"):
            raise AnthropicRequestError(
                "message role must be user, assistant, system, or developer"
            )
        content = message.get("content", "")
        if role in ("system", "developer"):
            converted.append({"role": "system", "content": _text_from_blocks(content)})
            continue
        if isinstance(content, str):
            converted.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            raise AnthropicRequestError("message content must be a string or an array")

        if role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for block in content:
                if not isinstance(block, dict):
                    raise AnthropicRequestError("content blocks must be objects")
                block_type = block.get("type")
                if block_type == "text":
                    text = block.get("text", "")
                    if not isinstance(text, str):
                        raise AnthropicRequestError("text block text must be a string")
                    text_parts.append(text)
                elif block_type == "tool_use":
                    name = block.get("name", "")
                    if not isinstance(name, str) or not name:
                        raise AnthropicRequestError("tool_use name must be a non-empty string")
                    arguments = block.get("input", {})
                    if not isinstance(arguments, dict):
                        raise AnthropicRequestError("tool_use input must be an object")
                    call: dict[str, Any] = {
                        "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    }
                    if block.get("id"):
                        call["id"] = str(block["id"])
                    tool_calls.append(call)
                elif block_type in ("thinking", "redacted_thinking"):
                    continue
            result: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(text_parts),
            }
            if tool_calls:
                for index, call in enumerate(tool_calls):
                    call["index"] = index
                result["tool_calls"] = tool_calls
            converted.append(result)
            continue

        text_parts = []
        tool_messages: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                raise AnthropicRequestError("content blocks must be objects")
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text", "")
                if not isinstance(text, str):
                    raise AnthropicRequestError("text block text must be a string")
                text_parts.append(text)
            elif block_type == "tool_result":
                tool_use_id = block.get("tool_use_id", "")
                if not isinstance(tool_use_id, str) or not tool_use_id:
                    raise AnthropicRequestError(
                        "tool_result tool_use_id must be a non-empty string"
                    )
                tool_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_use_id,
                        "content": _tool_result_text(block),
                    }
                )
            elif block_type in ("thinking", "redacted_thinking"):
                continue
        converted.extend(tool_messages)
        if text_parts or not tool_messages:
            converted.append({"role": "user", "content": "".join(text_parts)})
    return converted


def anthropic_tools_to_ollama(tools: Any) -> list[dict[str, Any]]:
    if tools is None:
        return []
    if not isinstance(tools, list):
        raise AnthropicRequestError("tools must be an array")
    converted = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise AnthropicRequestError("tools must contain objects")
        name = tool.get("name", "")
        input_schema = tool.get("input_schema")
        if not isinstance(name, str) or not name or not isinstance(input_schema, dict):
            # Anthropic server-side/beta tools are not executable by Ollama.
            continue
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description", ""),
                    "parameters": input_schema,
                },
            }
        )
    return converted


def build_messages_request(
    body: dict[str, Any],
) -> tuple[str, bool, dict[str, Any]]:
    if not isinstance(body, dict):
        raise AnthropicRequestError("request body must be an object")
    model = body.get("model", "")
    if not isinstance(model, str) or not model:
        raise AnthropicRequestError("model must be a non-empty string")
    max_tokens = body.get("max_tokens")
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool):
        raise AnthropicRequestError("max_tokens must be a positive integer")
    if max_tokens <= 0:
        raise AnthropicRequestError("max_tokens must be a positive integer")

    stream_value = body.get("stream", False)
    if not isinstance(stream_value, bool):
        raise AnthropicRequestError("stream must be a boolean")

    stream = stream_value
    request: dict[str, Any] = {
        "model": model,
        "messages": anthropic_messages_to_ollama(
            body.get("system"), body.get("messages")
        ),
        "stream": stream,
        "options": {"num_predict": max_tokens},
    }

    tools = anthropic_tools_to_ollama(body.get("tools"))
    tool_choice = body.get("tool_choice")
    choice_type = tool_choice.get("type") if isinstance(tool_choice, dict) else None
    if tools and choice_type != "none":
        request["tools"] = tools
    if choice_type == "tool":
        name = tool_choice.get("name", "")
        if isinstance(name, str) and name:
            request["tool_choice"] = {
                "type": "function",
                "function": {"name": name},
            }

    options = request["options"]
    stop_sequences = body.get("stop_sequences")
    if isinstance(stop_sequences, list) and all(
        isinstance(value, str) for value in stop_sequences
    ):
        options["stop"] = stop_sequences
    for source, target in (("temperature", "temperature"), ("top_p", "top_p")):
        value = body.get(source)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            options[target] = value
    return model, stream, request


def _tool_blocks(
    message: dict[str, Any],
    id_factory: IdFactory,
) -> list[dict[str, Any]]:
    result = []
    calls = message.get("tool_calls", [])
    if not isinstance(calls, list):
        return result
    for call in calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function", {})
        if not isinstance(function, dict):
            continue
        name = function.get("name", "")
        if not isinstance(name, str) or not name:
            continue
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except Exception:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        result.append(
            {
                "type": "tool_use",
                "id": str(call.get("id") or id_factory("toolu_", 24)),
                "name": name,
                "input": arguments,
            }
        )
    return result


def _stop_reason(data: dict[str, Any], has_tools: bool) -> str:
    if has_tools:
        return "tool_use"
    reason = str(data.get("done_reason", "stop"))
    if reason in ("length", "max_tokens"):
        return "max_tokens"
    if reason == "stop_sequence":
        return "stop_sequence"
    return "end_turn"


def message_from_ollama(
    data: dict[str, Any],
    model: str,
    *,
    message_id: str | None = None,
    id_factory: IdFactory = _new_id,
) -> dict[str, Any]:
    message = data.get("message", {})
    if not isinstance(message, dict):
        message = {}
    content: list[dict[str, Any]] = []
    text = message.get("content", "")
    if text:
        content.append({"type": "text", "text": str(text)})
    content.extend(_tool_blocks(message, id_factory))
    if not content:
        content.append({"type": "text", "text": EMPTY_RESPONSE_TEXT})
    has_tools = any(block.get("type") == "tool_use" for block in content)
    return {
        "id": message_id or id_factory("msg_", 24),
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": _stop_reason(data, has_tools),
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(data.get("prompt_eval_count") or 0),
            "output_tokens": int(data.get("eval_count") or 0),
        },
    }


def messages_failure_event(error_type: str, message: str) -> StreamEvent:
    return StreamEvent(
        anthropic_error(message, error_type),
        event="error",
    )


def messages_stream_events(
    items: Iterable[dict[str, Any] | None],
    model: str,
    *,
    message_id: str | None = None,
    id_factory: IdFactory = _new_id,
) -> Iterator[StreamEvent]:
    message_id = message_id or id_factory("msg_", 24)
    input_tokens = 0
    output_tokens = 0
    text_started = False
    next_index = 0
    calls_by_key: dict[tuple[str, Any], dict[str, Any]] = {}
    calls_in_order: list[dict[str, Any]] = []
    final_data: dict[str, Any] | None = None

    def event(event_type: str, **fields: Any) -> StreamEvent:
        payload = {"type": event_type}
        payload.update(deepcopy(fields))
        return StreamEvent(payload, event=event_type)

    yield event(
        "message_start",
        message={
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    )

    for data in items:
        if data is None:
            yield event("ping")
            continue
        if data.get("prompt_eval_count") is not None:
            input_tokens = int(data.get("prompt_eval_count") or 0)
        if data.get("eval_count") is not None:
            output_tokens = int(data.get("eval_count") or 0)
        message = data.get("message", {})
        if not isinstance(message, dict):
            message = {}
        text = message.get("content", "")
        if text:
            if not text_started:
                yield event(
                    "content_block_start",
                    index=next_index,
                    content_block={"type": "text", "text": ""},
                )
                text_started = True
            yield event(
                "content_block_delta",
                index=next_index,
                delta={"type": "text_delta", "text": str(text)},
            )

        calls = message.get("tool_calls", [])
        if isinstance(calls, list):
            for index, call in enumerate(calls):
                if not isinstance(call, dict):
                    continue
                key = (
                    ("id", str(call["id"]))
                    if call.get("id")
                    else ("index", call.get("index", index))
                )
                if key not in calls_by_key:
                    state = deepcopy(call)
                    calls_by_key[key] = state
                    calls_in_order.append(state)
                else:
                    calls_by_key[key].update(deepcopy(call))
        if data.get("done"):
            final_data = data
            break

    if final_data is None:
        raise RuntimeError("Ollama stream ended before its done marker")

    if text_started:
        yield event("content_block_stop", index=next_index)
        next_index += 1

    tool_blocks = _tool_blocks({"tool_calls": calls_in_order}, id_factory)
    if not text_started and not tool_blocks:
        yield event(
            "content_block_start",
            index=next_index,
            content_block={"type": "text", "text": ""},
        )
        yield event(
            "content_block_delta",
            index=next_index,
            delta={"type": "text_delta", "text": EMPTY_RESPONSE_TEXT},
        )
        yield event("content_block_stop", index=next_index)
        next_index += 1

    for block in tool_blocks:
        yield event(
            "content_block_start",
            index=next_index,
            content_block={
                "type": "tool_use",
                "id": block["id"],
                "name": block["name"],
                "input": {},
            },
        )
        yield event(
            "content_block_delta",
            index=next_index,
            delta={
                "type": "input_json_delta",
                "partial_json": normalize_tool_arguments(block["input"]),
            },
        )
        yield event("content_block_stop", index=next_index)
        next_index += 1

    yield event(
        "message_delta",
        delta={
            "stop_reason": _stop_reason(final_data, bool(tool_blocks)),
            "stop_sequence": None,
        },
        usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
    )
    yield event("message_stop")
