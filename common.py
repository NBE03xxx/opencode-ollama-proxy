"""Pure helpers shared by the protocol adapters."""

from dataclasses import dataclass
import json
from typing import Any, Optional


@dataclass(frozen=True)
class StreamEvent:
    """A transport-neutral event emitted by an agent adapter."""

    payload: Optional[dict[str, Any]] = None
    terminal: bool = False


def json_bytes(obj: Any) -> bytes:
    return json.dumps(
        obj,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def openai_error(message: str, error_type: str = "api_error") -> dict[str, Any]:
    return {
        "error": {
            "message": message,
            "type": error_type,
            "param": None,
            "code": None,
        }
    }


def normalize_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text = part.get("text", "")
                if text:
                    parts.append(str(text))
        return "".join(parts)
    return str(content)


def normalize_tool_arguments(arguments: Any) -> str:
    if isinstance(arguments, str):
        return arguments
    try:
        return json.dumps(
            arguments if arguments is not None else {},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except Exception:
        return "{}"


def convert_message_to_ollama(msg: dict[str, Any]) -> dict[str, Any]:
    role = msg.get("role", "")
    result: dict[str, Any] = {
        "role": role,
        "content": normalize_content(msg.get("content")),
    }

    tool_calls = msg.get("tool_calls")
    if tool_calls:
        converted = []
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function", {})
            if not isinstance(function, dict):
                function = {}
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except Exception:
                    arguments = {}
            converted_call: dict[str, Any] = {
                "function": {
                    "name": function.get("name", ""),
                    "arguments": arguments,
                }
            }
            if tool_call.get("id"):
                converted_call["id"] = tool_call["id"]
            if "index" in tool_call:
                converted_call["index"] = tool_call["index"]
            converted.append(converted_call)
        if converted:
            result["tool_calls"] = converted

    if role == "tool":
        if msg.get("tool_call_id"):
            result["tool_call_id"] = msg["tool_call_id"]
        if msg.get("name"):
            result["name"] = msg["name"]
    return result
