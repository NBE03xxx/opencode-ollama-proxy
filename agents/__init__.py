"""Public API for agent protocol adapters."""

from typing import Any

from .codex import (
    build_responses_request,
    responses_failure_events,
    responses_from_ollama,
    responses_input_to_messages,
    responses_stream_events,
    responses_tools_to_ollama,
)
from .claudecode import (
    AnthropicRequestError,
    anthropic_error,
    anthropic_messages_to_ollama,
    anthropic_tools_to_ollama,
    build_messages_request,
    message_from_ollama,
    messages_failure_event,
    messages_stream_events,
)
from .opencode import (
    build_chat_request,
    chat_completion_from_ollama,
    chat_stream_events,
)


def models_from_ollama(data: dict[str, Any]) -> dict[str, Any]:
    models = []
    for model in data.get("models", []):
        if not isinstance(model, dict):
            continue
        name = model.get("name", "")
        if name:
            models.append(
                {
                    "id": name,
                    "object": "model",
                    "created": 0,
                    "owned_by": "ollama",
                }
            )
    return {"object": "list", "data": models}


__all__ = [
    "AnthropicRequestError",
    "anthropic_error",
    "anthropic_messages_to_ollama",
    "anthropic_tools_to_ollama",
    "build_chat_request",
    "build_messages_request",
    "build_responses_request",
    "chat_completion_from_ollama",
    "chat_stream_events",
    "models_from_ollama",
    "message_from_ollama",
    "messages_failure_event",
    "messages_stream_events",
    "responses_failure_events",
    "responses_from_ollama",
    "responses_input_to_messages",
    "responses_stream_events",
    "responses_tools_to_ollama",
]
