"""Codex CLI / Responses API protocol conversion."""

from copy import deepcopy
import socket
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


def responses_tools_to_ollama(tools: Any) -> list[dict[str, Any]]:
    converted = []
    if not isinstance(tools, list):
        return converted
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            function = {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": tool.get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            }
        if not function.get("name"):
            continue
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": function["name"],
                    "description": function.get("description", ""),
                    "parameters": function.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                },
            }
        )
    return converted


def responses_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type", "") in (
                "input_text",
                "output_text",
                "text",
            ):
                parts.append(part.get("text", ""))
        return "".join(parts)
    return str(content)


def responses_input_to_messages(
    instructions: Any,
    input_data: Any,
    *,
    id_factory: IdFactory = _new_id,
) -> list[dict[str, Any]]:
    messages = []
    system_parts = []
    if instructions:
        system_parts.append(responses_content_to_text(instructions))

    if isinstance(input_data, str):
        messages.append({"role": "user", "content": input_data})
    elif not isinstance(input_data, list):
        messages.append({"role": "user", "content": str(input_data)})
    else:
        for item in input_data:
            if isinstance(item, str):
                messages.append({"role": "user", "content": item})
                continue
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "")
            if item_type == "message":
                role = item.get("role", "user")
                content = responses_content_to_text(item.get("content"))
                if role in ("system", "developer"):
                    system_parts.append(content)
                else:
                    messages.append({"role": role, "content": content})
            elif item_type == "function_call":
                call_id = item.get("call_id") or id_factory("call_", 16)
                arguments = normalize_tool_arguments(item.get("arguments", "{}"))
                if (
                    not messages
                    or messages[-1].get("role") != "assistant"
                    or not messages[-1].get("tool_calls")
                ):
                    messages.append(
                        {"role": "assistant", "content": "", "tool_calls": []}
                    )
                index = len(messages[-1]["tool_calls"])
                messages[-1]["tool_calls"].append(
                    {
                        "id": call_id,
                        "type": "function",
                        "index": index,
                        "function": {
                            "name": item.get("name", ""),
                            "arguments": arguments,
                        },
                    }
                )
            elif item_type == "function_call_output":
                output = item.get("output", "")
                if not isinstance(output, str):
                    try:
                        import json

                        output = json.dumps(output, ensure_ascii=False)
                    except Exception:
                        output = str(output)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": item.get("call_id", ""),
                        "content": output,
                    }
                )
            elif item_type == "reasoning":
                continue
            else:
                content = item.get("content", "")
                if isinstance(content, str) and content:
                    messages.append({"role": "user", "content": content})

    if system_parts:
        messages.insert(0, {"role": "system", "content": "\n\n".join(system_parts)})
    return messages


def build_responses_request(
    body: dict[str, Any],
) -> tuple[str, bool, dict[str, Any]]:
    model = body.get("model", "")
    stream = bool(body.get("stream", False))
    messages = responses_input_to_messages(
        body.get("instructions"), body.get("input", [])
    )
    request: dict[str, Any] = {
        "model": model,
        "messages": [convert_message_to_ollama(message) for message in messages],
        "stream": stream,
    }

    tool_choice = body.get("tool_choice")
    tools = responses_tools_to_ollama(body.get("tools"))
    if tools and tool_choice != "none":
        request["tools"] = tools
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        selected = tool_choice.get("function")
        if not isinstance(selected, dict):
            selected = {"name": tool_choice.get("name", "")}
        if selected.get("name"):
            request["tool_choice"] = {
                "type": "function",
                "function": {"name": selected["name"]},
            }

    max_tokens = body.get("max_output_tokens")
    if max_tokens is not None:
        try:
            value = int(max_tokens)
            if value > 0:
                request["options"] = {"num_predict": value}
        except (TypeError, ValueError):
            pass
    return model, stream, request


def responses_from_ollama(
    data: dict[str, Any],
    model: str,
    *,
    created: int | None = None,
    id_factory: IdFactory = _new_id,
) -> dict[str, Any]:
    message = data.get("message", {})
    if not isinstance(message, dict):
        message = {}
    output = []
    content = message.get("content", "")
    if content:
        output.append(
            {
                "type": "message",
                "id": id_factory("msg_", 16),
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": content,
                        "annotations": [],
                    }
                ],
            }
        )
    for index, tool_call in enumerate(message.get("tool_calls", []) or []):
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function", {})
        if not isinstance(function, dict) or not function.get("name"):
            continue
        output.append(
            {
                "type": "function_call",
                "id": id_factory("fc_", 16),
                "status": "completed",
                "call_id": tool_call.get("id")
                or f"call_{index}_{id_factory('', 12)}",
                "name": function["name"],
                "arguments": normalize_tool_arguments(function.get("arguments", {})),
            }
        )
    input_tokens = data.get("prompt_eval_count", 0)
    output_tokens = data.get("eval_count", 0)
    return {
        "id": id_factory("resp_", 16),
        "object": "response",
        "created_at": int(time.time()) if created is None else created,
        "model": model,
        "status": "completed",
        "output": output,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        "error": None,
        "incomplete_details": None,
    }


def responses_failure_events(
    model: str,
    code: str,
    message: str,
    *,
    response_id: str | None = None,
    created: int | None = None,
    id_factory: IdFactory = _new_id,
) -> Iterator[StreamEvent]:
    response_id = response_id or id_factory("resp_", 16)
    created = int(time.time()) if created is None else created

    def response(status: str, error=None):
        return {
            "id": response_id,
            "object": "response",
            "created_at": created,
            "model": model,
            "status": status,
            "output": [],
            "usage": None,
            "error": error,
            "incomplete_details": None,
        }

    yield StreamEvent(
        {"type": "response.created", "sequence_number": 0, "response": response("in_progress")}
    )
    yield StreamEvent(
        {
            "type": "response.failed",
            "sequence_number": 1,
            "response": response("failed", {"code": code, "message": message}),
        }
    )
    yield StreamEvent(terminal=True)


def responses_stream_events(
    items: Iterable[dict[str, Any]],
    model: str,
    *,
    response_id: str | None = None,
    created: int | None = None,
    id_factory: IdFactory = _new_id,
) -> Iterator[StreamEvent]:
    response_id = response_id or id_factory("resp_", 16)
    created = int(time.time()) if created is None else created
    output: list[dict[str, Any]] = []
    message_state = None
    calls_by_key = {}
    calls_in_order = []
    prompt_tokens = 0
    completion_tokens = 0
    sequence_number = 0
    saw_done = False

    def event(event_type: str, **fields: Any) -> StreamEvent:
        nonlocal sequence_number
        payload = {"type": event_type, "sequence_number": sequence_number}
        sequence_number += 1
        payload.update(deepcopy(fields))
        return StreamEvent(payload)

    def response_object(status: str, error=None, incomplete_details=None):
        usage = None
        if status in ("completed", "incomplete", "failed"):
            usage = {
                "input_tokens": prompt_tokens,
                "output_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
        return {
            "id": response_id,
            "object": "response",
            "created_at": created,
            "model": model,
            "status": status,
            "output": deepcopy(output),
            "usage": usage,
            "error": error,
            "incomplete_details": incomplete_details,
        }

    try:
        yield event("response.created", response=response_object("in_progress"))
        yield event("response.in_progress", response=response_object("in_progress"))

        for data in items:
            if data.get("prompt_eval_count") is not None:
                prompt_tokens = int(data.get("prompt_eval_count") or 0)
            if data.get("eval_count") is not None:
                completion_tokens = int(data.get("eval_count") or 0)

            message = data.get("message", {})
            if not isinstance(message, dict):
                message = {}
            content = message.get("content")
            if content:
                if message_state is None:
                    index = len(output)
                    item = {
                        "type": "message",
                        "id": id_factory("msg_", 16),
                        "status": "in_progress",
                        "role": "assistant",
                        "content": [],
                    }
                    message_state = {"index": index, "item": item, "text": ""}
                    output.append(item)
                    yield event("response.output_item.added", output_index=index, item=item)
                    yield event(
                        "response.content_part.added",
                        item_id=item["id"],
                        output_index=index,
                        content_index=0,
                        part={"type": "output_text", "text": "", "annotations": []},
                    )
                delta = str(content)
                message_state["text"] += delta
                yield event(
                    "response.output_text.delta",
                    item_id=message_state["item"]["id"],
                    output_index=message_state["index"],
                    content_index=0,
                    delta=delta,
                )

            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for stream_index, tool_call in enumerate(tool_calls):
                    if not isinstance(tool_call, dict):
                        continue
                    function = tool_call.get("function", {})
                    if not isinstance(function, dict):
                        function = {}
                    upstream_id = tool_call.get("id")
                    upstream_index = tool_call.get("index", stream_index)
                    key = (
                        ("id", str(upstream_id))
                        if upstream_id
                        else ("index", upstream_index)
                    )
                    state = calls_by_key.get(key)
                    if state is None:
                        index = len(output)
                        item = {
                            "type": "function_call",
                            "id": id_factory("fc_", 16),
                            "status": "in_progress",
                            "call_id": str(upstream_id)
                            if upstream_id
                            else id_factory("call_", 24),
                            "name": str(function.get("name") or ""),
                            "arguments": "",
                        }
                        state = {
                            "index": index,
                            "item": item,
                            "arguments": "",
                        }
                        calls_by_key[key] = state
                        calls_in_order.append(state)
                        output.append(item)
                        yield event(
                            "response.output_item.added",
                            output_index=index,
                            item=item,
                        )
                    if function.get("name"):
                        state["item"]["name"] = str(function["name"])
                    if "arguments" in function:
                        state["arguments"] = normalize_tool_arguments(
                            function.get("arguments")
                        )
            if data.get("done"):
                saw_done = True
                break

        if not saw_done:
            raise RuntimeError("Ollama stream ended before its done marker")

        if message_state is not None:
            item = message_state["item"]
            index = message_state["index"]
            text = message_state["text"]
            part = {"type": "output_text", "text": text, "annotations": []}
            item["content"] = [part]
            yield event(
                "response.output_text.done",
                item_id=item["id"],
                output_index=index,
                content_index=0,
                text=text,
            )
            yield event(
                "response.content_part.done",
                item_id=item["id"],
                output_index=index,
                content_index=0,
                part=part,
            )
            item["status"] = "completed"
            yield event("response.output_item.done", output_index=index, item=item)

        for state in calls_in_order:
            item = state["item"]
            arguments = state["arguments"] or "{}"
            item["arguments"] = arguments
            yield event(
                "response.function_call_arguments.delta",
                item_id=item["id"],
                output_index=state["index"],
                delta=arguments,
            )
            yield event(
                "response.function_call_arguments.done",
                item_id=item["id"],
                output_index=state["index"],
                arguments=arguments,
            )
            item["status"] = "completed"
            yield event(
                "response.output_item.done",
                output_index=state["index"],
                item=item,
            )

        yield event("response.completed", response=response_object("completed"))
    except (socket.timeout, TimeoutError) as exc:
        error = {
            "code": "stream_timeout",
            "message": str(exc) or "Ollama stream timed out",
        }
        yield event("response.failed", response=response_object("failed", error=error))
    except Exception as exc:
        error = {"code": "upstream_error", "message": str(exc)}
        yield event("response.failed", response=response_object("failed", error=error))
    yield StreamEvent(terminal=True)
