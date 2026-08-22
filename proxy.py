#!/usr/bin/env python3
"""Agent API compatibility boundary for an Ollama server."""

from dataclasses import dataclass
import json
import os
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterable
from urllib.parse import urlparse

from agents import (
    AnthropicRequestError,
    anthropic_error,
    build_chat_request,
    build_messages_request,
    build_responses_request,
    chat_completion_from_ollama,
    chat_stream_events,
    models_from_ollama,
    message_from_ollama,
    messages_failure_event,
    messages_stream_events,
    responses_failure_events,
    responses_from_ollama,
    responses_stream_events,
)
from common import StreamEvent, json_bytes, normalize_content, openai_error
from ollama import OllamaClient, OllamaConnectionError, OllamaError


SERVER_NAME = "Ollama Agent Proxy v1.1"


def message_layout_lines(messages: Any) -> list[str]:
    """Return metadata-only diagnostics for an Ollama message sequence."""
    if not isinstance(messages, list):
        return [f"[Message Layout] messages_type={type(messages).__name__}"]
    system_positions = []
    developer_positions = []
    lines = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            lines.append(f"{index}: message_type={type(message).__name__}")
            continue
        role = message.get("role", "")
        if role == "system":
            system_positions.append(index)
        elif role == "developer":
            developer_positions.append(index)
        content = message.get("content")
        length = len(normalize_content(content))
        lines.append(
            f"{index}: role={role}, content_type={type(content).__name__}, "
            f"length={length}"
        )
    return [
        f"[Message Layout] count={len(messages)}, "
        f"system_count={len(system_positions)}, "
        f"system_positions={system_positions}, "
        f"developer_count={len(developer_positions)}, "
        f"developer_positions={developer_positions}",
        *lines,
    ]


def parse_ollama_think(value: str) -> bool | str:
    normalized = value.strip().lower()
    if normalized in ("", "0", "false", "no", "off"):
        return False
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("low", "medium", "high"):
        return normalized
    raise ValueError(
        "OLLAMA_THINK must be false, true, low, medium, or high"
    )


@dataclass(frozen=True)
class Settings:
    ollama_host: str = "http://127.0.0.1:11434"
    listen_host: str = "0.0.0.0"
    listen_port: int = 8000
    connect_timeout: float = 30
    read_timeout: float = 60 * 60 * 6
    stream_idle_timeout: float = 600
    max_request_bytes: int = 64 * 1024 * 1024
    ollama_keep_alive: str = "30m"
    ollama_think: bool | str = False
    anthropic_heartbeat_interval: float = 60
    debug: bool = False

    def __post_init__(self) -> None:
        if not 0 < self.anthropic_heartbeat_interval < 300:
            raise ValueError(
                "ANTHROPIC_HEARTBEAT_INTERVAL must be greater than 0 and less than 300"
            )

    @classmethod
    def from_env(cls) -> "Settings":
        truthy = ("1", "true", "yes")
        return cls(
            ollama_host=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
            listen_host=os.environ.get("LISTEN_HOST", "0.0.0.0"),
            listen_port=int(os.environ.get("LISTEN_PORT", "8000")),
            connect_timeout=float(os.environ.get("CONNECT_TIMEOUT", "30")),
            read_timeout=float(os.environ.get("READ_TIMEOUT", str(60 * 60 * 6))),
            stream_idle_timeout=float(os.environ.get("STREAM_IDLE_TIMEOUT", "600")),
            max_request_bytes=int(
                os.environ.get("MAX_REQUEST_BYTES", str(64 * 1024 * 1024))
            ),
            ollama_keep_alive=os.environ.get("OLLAMA_KEEP_ALIVE", "30m"),
            ollama_think=parse_ollama_think(os.environ.get("OLLAMA_THINK", "0")),
            anthropic_heartbeat_interval=float(
                os.environ.get("ANTHROPIC_HEARTBEAT_INTERVAL", "60")
            ),
            debug=os.environ.get("DEBUG", "").lower() in truthy,
        )


def make_client(settings: Settings) -> OllamaClient:
    return OllamaClient(
        settings.ollama_host,
        connect_timeout=settings.connect_timeout,
        read_timeout=settings.read_timeout,
        stream_idle_timeout=settings.stream_idle_timeout,
        keep_alive=settings.ollama_keep_alive,
        think=settings.ollama_think,
    )


class ProxyHandler(BaseHTTPRequestHandler):
    """HTTP routing and transport; protocol conversion lives in agents/."""

    settings: Settings | None = None
    ollama_client: OllamaClient | None = None
    _counter = 0
    _counter_lock = threading.Lock()

    def setup(self) -> None:
        super().setup()
        self._response_started = False

    @classmethod
    def next_request_number(cls) -> int:
        with cls._counter_lock:
            cls._counter += 1
            return cls._counter

    def log_message(self, fmt: str, *args: Any) -> None:
        print("[HTTP] %s - %s" % (self.address_string(), fmt % args))

    @property
    def config(self) -> Settings:
        if self.settings is None:
            raise RuntimeError("ProxyHandler is not configured")
        return self.settings

    @property
    def client(self) -> OllamaClient:
        if self.ollama_client is None:
            raise RuntimeError("ProxyHandler is not configured")
        return self.ollama_client

    def send_json_headers(self, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self._response_started = True

    def send_sse_headers(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self._response_started = True

    def send_sse(self, payload: dict[str, Any], event: str | None = None) -> None:
        if event:
            self.wfile.write(b"event: " + event.encode("utf-8") + b"\n")
        self.wfile.write(b"data: " + json_bytes(payload) + b"\n\n")
        self.wfile.flush()

    def send_done(self) -> None:
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        self.send_json_headers(status)
        self.wfile.write(json_bytes(payload))
        self.wfile.flush()

    def send_events(
        self,
        events: Iterable[StreamEvent],
        *,
        done_marker: bool = True,
    ) -> None:
        iterator = iter(events)
        try:
            for event in iterator:
                if event.terminal:
                    if done_marker:
                        self.send_done()
                    break
                if event.payload is not None:
                    self.send_sse(event.payload, event.event)
        finally:
            close = getattr(iterator, "close", None)
            if close is not None:
                close()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/v1/chat/completions":
            self._dispatch(path, self.handle_chat_completion)
        elif path == "/v1/responses":
            self._dispatch(path, self.handle_responses)
        elif path == "/v1/messages":
            self._dispatch(path, self.handle_messages)
        elif path == "/v1/messages/count_tokens":
            self.send_json(
                404,
                anthropic_error(
                    "Token counting is not implemented",
                    "not_found_error",
                ),
            )
        else:
            self.send_json(404, openai_error("Not found", "not_found"))

    def do_HEAD(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/hello":
            self.send_response(200)
            self.send_header("Connection", "close")
            self.end_headers()
            self._response_started = True
        else:
            self.send_response(404)
            self.send_header("Connection", "close")
            self.end_headers()
            self._response_started = True

    def _dispatch(self, path: str, handler) -> None:
        request_no = self.next_request_number()
        print()
        print("=" * 70)
        print(f"Request #{request_no} [{path}]")
        print("=" * 70)
        try:
            handler(request_no)
        except BrokenPipeError:
            print("[WARN] Client disconnected")
        except ConnectionResetError:
            print("[WARN] Client connection reset")
        except Exception:
            print("[ERROR] Unhandled exception:")
            traceback.print_exc()
            if not self._response_started:
                try:
                    if path == "/v1/messages":
                        error = anthropic_error("Internal proxy error", "api_error")
                    else:
                        error = openai_error("Internal proxy error", "proxy_error")
                    self.send_json(500, error)
                except Exception:
                    pass

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/v1/models":
            self.handle_models()
        elif path in ("/health", "/v1/health"):
            self.send_json(200, {"status": "ok"})
        else:
            self.send_json(404, openai_error("Not found", "not_found"))

    def handle_models(self) -> None:
        try:
            self.send_json(200, models_from_ollama(self.client.list_models()))
        except OllamaError as exc:
            print(f"[ERROR] /v1/models: {exc}")
            self.send_json(502, openai_error(str(exc), "upstream_error"))

    def _read_json_body(
        self,
        *,
        enforce_limit: bool,
        error_factory=openai_error,
        too_large_type: str = "invalid_request_error",
    ) -> dict[str, Any] | None:
        if enforce_limit:
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                content_length = -1
            if content_length < 0 or content_length > self.config.max_request_bytes:
                self.send_json(
                    413,
                    error_factory("Request body is too large", too_large_type),
                )
                return None
        else:
            content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            self.send_json(
                400,
                error_factory(f"Invalid JSON: {exc}", "invalid_request_error"),
            )
            return None
        return body

    def _log_request(self, label: str, body: dict[str, Any], messages=None) -> None:
        print()
        print(f"[{label} Request Parameters]")
        print(f"model = {body.get('model', '')}")
        print(f"stream = {bool(body.get('stream', False))}")
        if not self.config.debug or messages is None:
            return
        print()
        for line in message_layout_lines(messages):
            print(line)

    @staticmethod
    def _error_details(exc: OllamaError) -> tuple[str, str]:
        if isinstance(exc, OllamaConnectionError):
            return "connection_error", str(exc)
        return "upstream_error", str(exc)

    def _send_chat_upstream_error(self, stream: bool, exc: OllamaError) -> None:
        error_type, message = self._error_details(exc)
        if stream:
            self.send_sse_headers()
            self.send_sse(openai_error(message, error_type))
            self.send_done()
        else:
            self.send_json(502, openai_error(message, error_type))

    def _send_responses_upstream_error(
        self, stream: bool, model: str, exc: OllamaError
    ) -> None:
        error_type, message = self._error_details(exc)
        if stream:
            self.send_sse_headers()
            self.send_events(responses_failure_events(model, error_type, message))
        else:
            self.send_json(502, openai_error(message, error_type))

    def _send_messages_upstream_error(self, stream: bool, exc: Exception) -> None:
        message = str(exc) or "Ollama request failed"
        if stream:
            if not self._response_started:
                self.send_sse_headers()
            self.send_sse(
                anthropic_error(message, "api_error"),
                "error",
            )
        else:
            self.send_json(502, anthropic_error(message, "api_error"))

    def handle_chat_completion(self, request_no: int) -> None:
        body = self._read_json_body(enforce_limit=False)
        if body is None:
            return
        model, stream, ollama_body = build_chat_request(body)
        self._log_request("Chat", body, body.get("messages", []))
        start_time = time.monotonic()
        try:
            if stream:
                with self.client.stream_chat(
                    ollama_body,
                    skip_invalid=True,
                    use_idle_timeout=False,
                ) as items:
                    self.send_sse_headers()
                    try:
                        self.send_events(chat_stream_events(items, model))
                    except OllamaError as exc:
                        print(f"[ERROR] Chat streaming exception: {exc}")
                        self.send_done()
                    except (BrokenPipeError, ConnectionResetError):
                        raise
                    except Exception as exc:
                        print(f"[ERROR] Chat streaming exception: {exc}")
                        self.send_done()
            else:
                response = chat_completion_from_ollama(self.client.chat(ollama_body), model)
                self.send_json(200, response)
        except OllamaError as exc:
            print(f"[ERROR] Ollama request: {exc}")
            if not self._response_started:
                self._send_chat_upstream_error(stream, exc)
        print(
            f"[Chat Complete] Request #{request_no} "
            f"{time.monotonic() - start_time:.3f}s"
        )

    def handle_responses(self, request_no: int) -> None:
        body = self._read_json_body(enforce_limit=True)
        if body is None:
            return
        model = body.get("model", "")
        if not isinstance(model, str) or not model:
            self.send_json(
                400,
                openai_error("model must be a non-empty string", "invalid_request_error"),
            )
            return
        model, stream, ollama_body = build_responses_request(body)
        self._log_request("Responses", body, ollama_body.get("messages", []))
        start_time = time.monotonic()
        try:
            if stream:
                with self.client.stream_chat(ollama_body) as items:
                    self.send_sse_headers()
                    self.send_events(responses_stream_events(items, model))
            else:
                response = responses_from_ollama(self.client.chat(ollama_body), model)
                self.send_json(200, response)
        except OllamaError as exc:
            print(f"[ERROR] Ollama request: {exc}")
            if not self._response_started:
                self._send_responses_upstream_error(stream, model, exc)
        print(
            f"[Responses Complete] Request #{request_no} "
            f"{time.monotonic() - start_time:.3f}s"
        )

    def handle_messages(self, request_no: int) -> None:
        body = self._read_json_body(
            enforce_limit=True,
            error_factory=anthropic_error,
            too_large_type="request_too_large",
        )
        if body is None:
            return
        try:
            model, stream, ollama_body = build_messages_request(body)
        except AnthropicRequestError as exc:
            self.send_json(400, anthropic_error(str(exc), "invalid_request_error"))
            return

        self._log_request("Messages", body, ollama_body.get("messages", []))
        start_time = time.monotonic()
        try:
            if stream:
                with self.client.stream_chat_with_heartbeat(
                    ollama_body,
                    heartbeat_interval=self.config.anthropic_heartbeat_interval,
                ) as items:
                    self.send_sse_headers()
                    try:
                        self.send_events(
                            messages_stream_events(items, model),
                            done_marker=False,
                        )
                    except (BrokenPipeError, ConnectionResetError):
                        raise
                    except Exception as exc:
                        print(f"[ERROR] Messages streaming exception: {exc}")
                        self.send_sse(
                            anthropic_error(str(exc) or "Streaming failed", "api_error"),
                            "error",
                        )
            else:
                response = message_from_ollama(self.client.chat(ollama_body), model)
                self.send_json(200, response)
        except OllamaError as exc:
            print(f"[ERROR] Ollama request: {exc}")
            if not self._response_started:
                self._send_messages_upstream_error(stream, exc)
        print(
            f"[Messages Complete] Request #{request_no} "
            f"{time.monotonic() - start_time:.3f}s"
        )


def configured_handler(settings: Settings, client: OllamaClient):
    class ConfiguredProxyHandler(ProxyHandler):
        pass

    ConfiguredProxyHandler.settings = settings
    ConfiguredProxyHandler.ollama_client = client
    return ConfiguredProxyHandler


def main() -> None:
    settings = Settings.from_env()
    client = make_client(settings)
    server = ThreadingHTTPServer(
        (settings.listen_host, settings.listen_port),
        configured_handler(settings, client),
    )
    print("=" * 70)
    print("Ollama Agent Proxy")
    print("=" * 70)
    print(f"Server : {SERVER_NAME}")
    print(f"Listen : http://{settings.listen_host}:{settings.listen_port}")
    print(f"Ollama : {client.chat_url}")
    print("=" * 70)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
