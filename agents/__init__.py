"""Public API for OpenAI-compatible protocol adapters."""

from typing import Any

from .codex import (
    build_responses_request,
    responses_failure_events,
    responses_from_ollama,
    responses_input_to_messages,
    responses_stream_events,
    responses_tools_to_ollama,
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
    "build_chat_request",
    "build_responses_request",
    "chat_completion_from_ollama",
    "chat_stream_events",
    "models_from_ollama",
    "responses_failure_events",
    "responses_from_ollama",
    "responses_input_to_messages",
    "responses_stream_events",
    "responses_tools_to_ollama",
]
