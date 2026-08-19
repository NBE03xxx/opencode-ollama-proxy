#!/usr/bin/env python3

import json
import os
import time
import uuid
import traceback
import socket
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse


# ============================================================
# Configuration
# ============================================================

OLLAMA_HOST = os.environ.get(
    "OLLAMA_HOST",
    "http://127.0.0.1:11434",
)

OLLAMA_URL = OLLAMA_HOST.rstrip("/") + "/api/chat"

HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
PORT = int(os.environ.get("LISTEN_PORT", "8000"))

CONNECT_TIMEOUT = float(os.environ.get("CONNECT_TIMEOUT", "30"))
READ_TIMEOUT = float(os.environ.get("READ_TIMEOUT", str(60 * 60 * 6)))
STREAM_IDLE_TIMEOUT = float(os.environ.get("STREAM_IDLE_TIMEOUT", "600"))
MAX_REQUEST_BYTES = int(os.environ.get("MAX_REQUEST_BYTES", str(64 * 1024 * 1024)))
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")
OLLAMA_THINK = os.environ.get("OLLAMA_THINK", "0").lower() in ("1", "true", "yes")

SERVER_NAME = "Ollama Agent Proxy (OpenAI-compatible) v2"

DEBUG = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")


# ============================================================
# Utility
# ============================================================

def make_id():
    return "chatcmpl-" + uuid.uuid4().hex[:16]


def now_unix():
    return int(time.time())


def json_bytes(obj):
    return json.dumps(
        obj,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def openai_error(message, error_type="api_error"):
    return {
        "error": {
            "message": message,
            "type": error_type,
            "param": None,
            "code": None,
        }
    }


def normalize_content(content):
    """
    Normalize OpenAI message content into plain text.

    OpenAI content may be:
      - string
      - list of content parts
      - None
    """

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


def normalize_tool_arguments(arguments):
    """
    Ollama returns tool-call arguments as an object/dict.

    OpenAI Chat Completions expects function.arguments as a JSON
    encoded string.
    """

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


def responses_tools_to_ollama(tools):
    """Convert Responses API function definitions to Ollama /api/chat tools.

    Responses uses {type,function name,description,parameters}; Ollama uses
    {type,function:{name,description,parameters}}.  Chat Completions tools
    already use the latter form and are accepted as well.
    """
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
                "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
            }
        if not function.get("name"):
            continue
        converted.append({
            "type": "function",
            "function": {
                "name": function["name"],
                "description": function.get("description", ""),
                "parameters": function.get("parameters", {"type": "object", "properties": {}}),
            },
        })
    return converted


# ============================================================
# OpenAI -> Ollama message conversion
# ============================================================

def convert_message_to_ollama(msg):
    """
    Convert an OpenAI-compatible message to Ollama /api/chat format.
    """

    role = msg.get("role", "")
    content = normalize_content(msg.get("content"))

    result = {
        "role": role,
        "content": content,
    }

    # --------------------------------------------------------
    # Assistant tool calls
    # --------------------------------------------------------

    tool_calls = msg.get("tool_calls")

    if tool_calls:
        ollama_tool_calls = []

        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue

            function = tc.get("function", {})

            if not isinstance(function, dict):
                function = {}

            name = function.get("name", "")
            arguments = function.get("arguments", {})

            # OpenAI normally sends arguments as a JSON string.
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except Exception:
                    arguments = {}

            ollama_tool_call = {
                "function": {
                    "name": name,
                    "arguments": arguments,
                }
            }

            # Preserve a Responses call_id when replaying a tool-call history.
            # Ollama ignores it when unnecessary, but models that correlate a
            # following tool message can use it.
            if tc.get("id"):
                ollama_tool_call["id"] = tc["id"]

            # Preserve index when available.
            if "index" in tc:
                ollama_tool_call["index"] = tc["index"]

            ollama_tool_calls.append(ollama_tool_call)

        if ollama_tool_calls:
            result["tool_calls"] = ollama_tool_calls

    # --------------------------------------------------------
    # Tool result
    # --------------------------------------------------------

    # Ollama accepts role=tool and content directly.
    #
    # OpenAI tool messages may contain:
    #   tool_call_id
    #   name
    #
    # Ollama does not require tool_call_id in the message body,
    # but preserving name can be useful.
    # --------------------------------------------------------

    if role == "tool":
        if msg.get("tool_call_id"):
            result["tool_call_id"] = msg["tool_call_id"]
        if msg.get("name"):
            result["name"] = msg["name"]

    return result


# ============================================================
# Request Handler
# ============================================================

class ProxyHandler(BaseHTTPRequestHandler):

    request_counter = 0

    # --------------------------------------------------------
    # HTTP logging
    # --------------------------------------------------------

    def log_message(self, fmt, *args):
        print(
            "[HTTP] %s - %s"
            % (
                self.address_string(),
                fmt % args,
            )
        )

    # --------------------------------------------------------
    # Common headers
    # --------------------------------------------------------

    def send_json_headers(self, status=200):
        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

    def send_sse_headers(self):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/event-stream; charset=utf-8",
        )
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    # --------------------------------------------------------
    # SSE
    # --------------------------------------------------------

    def send_sse(self, obj):
        data = (
            "data: "
            + json.dumps(
                obj,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n\n"
        ).encode("utf-8")

        self.wfile.write(data)
        self.wfile.flush()

    def send_done(self):
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    # --------------------------------------------------------
    # POST
    # --------------------------------------------------------

    def do_POST(self):

        path = urlparse(self.path).path
        if path == "/v1/chat/completions":
            self._dispatch("/v1/chat/completions", self.handle_chat_completion)
        elif path == "/v1/responses":
            self._dispatch("/v1/responses", self.handle_responses)
        else:
            self.send_json_headers(404)
            self.wfile.write(
                json_bytes(
                    openai_error(
                        "Not found",
                        "not_found",
                    )
                )
            )

    def _dispatch(self, path, handler):

        ProxyHandler.request_counter += 1
        request_no = ProxyHandler.request_counter

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

            try:
                if not self.wfile.closed:
                    self.send_json_headers(500)
                    self.wfile.write(
                        json_bytes(
                            openai_error(
                                "Internal proxy error",
                                "proxy_error",
                            )
                        )
                    )
            except Exception:
                pass

    # --------------------------------------------------------
    # GET
    # --------------------------------------------------------

    def do_GET(self):

        path = urlparse(self.path).path

        if path == "/v1/models":
            self.handle_models()
        elif path in ("/health", "/v1/health"):
            self.send_json_headers(200)
            self.wfile.write(json_bytes({"status": "ok"}))
        else:
            self.send_json_headers(404)
            self.wfile.write(
                json_bytes(
                    openai_error(
                        "Not found",
                        "not_found",
                    )
                )
            )

    def handle_models(self):

        try:
            req = Request(
                OLLAMA_HOST.rstrip("/") + "/api/tags",
                method="GET",
            )
            with urlopen(req, timeout=CONNECT_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            models = []
            for m in data.get("models", []):
                name = m.get("name", "")
                if name:
                    models.append({
                        "id": name,
                        "object": "model",
                        "created": 0,
                        "owned_by": "ollama",
                    })

            self.send_json_headers(200)
            self.wfile.write(json_bytes({
                "object": "list",
                "data": models,
            }))

        except Exception as e:
            print(f"[ERROR] /v1/models: {e}")
            self.send_json_headers(502)
            self.wfile.write(json_bytes(openai_error(str(e), "upstream_error")))

    # --------------------------------------------------------
    # Chat completion
    # --------------------------------------------------------

    def handle_chat_completion(self, request_no):

        content_length = int(
            self.headers.get("Content-Length", "0")
        )

        raw_body = self.rfile.read(content_length)

        try:
            body = json.loads(
                raw_body.decode("utf-8")
            )

        except Exception as e:
            self.send_json_headers(400)
            self.wfile.write(
                json_bytes(
                    openai_error(
                        f"Invalid JSON: {e}",
                        "invalid_request_error",
                    )
                )
            )
            return

        model = body.get("model", "")
        messages = body.get("messages", [])
        stream = bool(body.get("stream", False))
        max_tokens = body.get("max_tokens")

        tools = body.get("tools")
        tool_choice = body.get("tool_choice")

        print()
        print("[Request Parameters]")
        print(f"model = {model}")
        print(f"stream = {stream}")
        print(f"max_tokens = {max_tokens}")
        print(f"messages = {len(messages)}")
        print(
            f"tools = "
            f"{len(tools) if isinstance(tools, list) else 0}"
        )
        print(f"tool_choice = {tool_choice}")

        # ----------------------------------------------------
        # Log messages
        # ----------------------------------------------------

        if DEBUG:
            for i, msg in enumerate(messages):

                role = msg.get("role", "")
                content = normalize_content(
                    msg.get("content")
                )

                print(
                    f"\n[{i}] {role} {len(content)} chars"
                )

                preview = content[:300]

                if len(content) > 300:
                    preview += "..."

                if preview:
                    print(
                        "    "
                        + preview.replace("\n", " ")
                    )

                if msg.get("tool_calls"):
                    print(
                        f"    tool_calls = "
                        f"{len(msg['tool_calls'])}"
                    )

                if role == "tool":
                    print(
                        f"    tool_call_id = "
                        f"{msg.get('tool_call_id')}"
                    )

        # ----------------------------------------------------
        # Convert OpenAI messages -> Ollama messages
        # ----------------------------------------------------

        ollama_messages = []

        for msg in messages:
            ollama_messages.append(
                convert_message_to_ollama(msg)
            )

        # ----------------------------------------------------
        # Build Ollama request
        # ----------------------------------------------------

        ollama_body = {
            "model": model,
            "messages": ollama_messages,

            # Default remains off for compatibility; configurable for models
            # whose reasoning mode is useful and supported by Ollama.
            "think": OLLAMA_THINK,

            "stream": stream,
            "keep_alive": OLLAMA_KEEP_ALIVE,
        }

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # Pass OpenAI tools directly to Ollama.
        #
        # Ollama /api/chat uses the same basic tool definition
        # structure:
        #
        # {
        #   "type": "function",
        #   "function": {
        #       "name": "...",
        #       "description": "...",
        #       "parameters": {...}
        #   }
        # }
        # ----------------------------------------------------

        tools_enabled = True

        if tool_choice == "none":
            tools_enabled = False

        if isinstance(tools, list) and tools and tools_enabled:
            ollama_body["tools"] = tools

        # ----------------------------------------------------
        # tool_choice
        #
        # Ollama currently does not require us to force the
        # OpenAI tool_choice representation.
        #
        # For "auto" we simply omit it.
        #
        # For "none" we omit tools entirely so the model
        # cannot call any function.
        #
        # For explicit forcing, pass it only when it is a
        # representation Ollama can accept.
        # ----------------------------------------------------

        if tool_choice is not None:
            if tool_choice == "auto":
                pass
            elif tool_choice == "none":
                pass
            elif isinstance(tool_choice, dict):
                # Ollama accepts tool_choice in newer versions,
                # but do not blindly transform unknown formats.
                #
                # Keep it only for function selection.
                if (
                    tool_choice.get("type") == "function"
                    and isinstance(
                        tool_choice.get("function"),
                        dict,
                    )
                ):
                    ollama_body["tool_choice"] = tool_choice

        # ----------------------------------------------------
        # max_tokens -> Ollama options.num_predict
        # ----------------------------------------------------

        if max_tokens is not None:
            try:
                max_tokens_int = int(max_tokens)

                if max_tokens_int > 0:
                    ollama_body["options"] = {
                        "num_predict": max_tokens_int
                    }

            except (TypeError, ValueError):
                pass

        if DEBUG:
            print()
            print("[Ollama Request]")

            try:
                debug_body = json.dumps(
                    ollama_body,
                    ensure_ascii=False,
                    indent=2,
                )

                print(debug_body[:8000])

                if len(debug_body) > 8000:
                    print(
                        "[... request log truncated ...]"
                    )

            except Exception:
                print("[WARN] Could not log request")

        # ----------------------------------------------------
        # Upstream request
        # ----------------------------------------------------

        request_data = json_bytes(
            ollama_body
        )

        upstream_request = Request(
            OLLAMA_URL,
            data=request_data,
            headers={
                "Content-Type": "application/json",
                "Accept": (
                    "application/x-ndjson"
                    if stream
                    else "application/json"
                ),
            },
            method="POST",
        )

        start_time = time.monotonic()

        try:

            upstream = urlopen(
                upstream_request,
                timeout=READ_TIMEOUT,
            )

        except HTTPError as e:

            error_body = b""

            try:
                error_body = e.read()
            except Exception:
                pass

            error_text = error_body.decode(
                "utf-8",
                errors="replace",
            )

            print(
                f"[ERROR] Ollama HTTP {e.code}: "
                f"{error_text}"
            )

            if stream:
                self.send_sse_headers()

                self.send_sse(
                    openai_error(
                        error_text,
                        "upstream_error",
                    )
                )

                self.send_done()

            else:
                self.send_json_headers(502)

                self.wfile.write(
                    json_bytes(
                        openai_error(
                            error_text,
                            "upstream_error",
                        )
                    )
                )

            return

        except URLError as e:

            print(
                f"[ERROR] Cannot connect to Ollama: {e}"
            )

            if stream:
                self.send_sse_headers()

                self.send_sse(
                    openai_error(
                        f"Cannot connect to Ollama: {e}",
                        "connection_error",
                    )
                )

                self.send_done()

            else:
                self.send_json_headers(502)

                self.wfile.write(
                    json_bytes(
                        openai_error(
                            f"Cannot connect to Ollama: {e}",
                            "connection_error",
                        )
                    )
                )

            return

        except Exception as e:

            print(
                f"[ERROR] Upstream connection error: {e}"
            )

            if stream:
                self.send_sse_headers()

                self.send_sse(
                    openai_error(
                        str(e),
                        "connection_error",
                    )
                )

                self.send_done()

            else:
                self.send_json_headers(502)

                self.wfile.write(
                    json_bytes(
                        openai_error(
                            str(e),
                            "connection_error",
                        )
                    )
                )

            return

        # ----------------------------------------------------
        # Response
        # ----------------------------------------------------

        if stream:
            self.handle_stream(
                upstream,
                model,
                start_time,
                request_no,
            )

        else:
            self.handle_non_stream(
                upstream,
                model,
                start_time,
                request_no,
            )

    # ========================================================
    # Non-stream response
    # ========================================================

    def handle_non_stream(
        self,
        upstream,
        model,
        start_time,
        request_no,
    ):

        raw = upstream.read()

        elapsed = (
            time.monotonic()
            - start_time
        )

        print()
        print(
            f"[Ollama Response] "
            f"{len(raw)} bytes, "
            f"{elapsed:.3f}s"
        )

        try:
            data = json.loads(
                raw.decode("utf-8")
            )

        except Exception as e:

            self.send_json_headers(502)

            self.wfile.write(
                json_bytes(
                    openai_error(
                        f"Invalid Ollama JSON: {e}",
                        "upstream_error",
                    )
                )
            )

            return

        message = data.get(
            "message",
            {},
        )

        if not isinstance(message, dict):
            message = {}

        content = message.get(
            "content",
            "",
        )

        thinking = message.get(
            "thinking",
            "",
        )

        ollama_tool_calls = message.get(
            "tool_calls",
            [],
        )

        # ----------------------------------------------------
        # Convert Ollama tool_calls -> OpenAI tool_calls
        # ----------------------------------------------------

        openai_tool_calls = []

        if isinstance(
            ollama_tool_calls,
            list,
        ):

            for index, tc in enumerate(
                ollama_tool_calls
            ):

                if not isinstance(tc, dict):
                    continue

                function = tc.get(
                    "function",
                    {},
                )

                if not isinstance(
                    function,
                    dict,
                ):
                    function = {}

                call_id = tc.get(
                    "id"
                )

                if not call_id:
                    call_id = (
                        "call_"
                        + uuid.uuid4().hex[:16]
                    )

                name = function.get(
                    "name",
                    "",
                )

                arguments = normalize_tool_arguments(
                    function.get(
                        "arguments",
                        {},
                    )
                )

                openai_tool_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "index": tc.get(
                            "index",
                            index,
                        ),
                        "function": {
                            "name": name,
                            "arguments": arguments,
                        },
                    }
                )

        # ----------------------------------------------------
        # Usage
        # ----------------------------------------------------

        usage = {
            "prompt_tokens": data.get(
                "prompt_eval_count",
                0,
            ),
            "completion_tokens": data.get(
                "eval_count",
                0,
            ),
        }

        usage["total_tokens"] = (
            usage["prompt_tokens"]
            + usage["completion_tokens"]
        )

        # ----------------------------------------------------
        # Finish reason
        # ----------------------------------------------------

        if openai_tool_calls:
            finish_reason = "tool_calls"
        else:
            finish_reason = data.get(
                "done_reason",
                "stop",
            )

        response_message = {
            "role": "assistant",
            "content": (
                content
                if content != ""
                else None
            ),
        }

        if openai_tool_calls:
            response_message[
                "tool_calls"
            ] = openai_tool_calls

        response = {
            "id": make_id(),
            "object": "chat.completion",
            "created": now_unix(),
            "model": model,
            "system_fingerprint": "fp_ollama",
            "choices": [
                {
                    "index": 0,
                    "message": response_message,
                    "finish_reason": finish_reason,
                }
            ],
            "usage": usage,
        }

        self.send_json_headers(200)

        self.wfile.write(
            json_bytes(response)
        )

        self.wfile.flush()

        print(
            f"[Complete] Request #{request_no} "
            f"{elapsed:.3f}s "
            f"tool_calls={len(openai_tool_calls)}"
        )

    # ========================================================
    # Streaming response
    # ========================================================

    def handle_stream(
        self,
        upstream,
        model,
        start_time,
        request_no,
    ):

        self.send_sse_headers()

        completion_id = make_id()
        created = now_unix()

        first_chunk = True

        total_prompt_tokens = 0
        total_completion_tokens = 0

        finish_reason = None

        saw_tool_call = False

        print()
        print("[Stream] started")

        try:

            while True:

                line = upstream.readline()

                if not line:
                    break

                line = line.strip()

                if not line:
                    continue

                try:
                    data = json.loads(
                        line.decode("utf-8")
                    )

                except Exception as e:

                    print(
                        "[WARN] Invalid Ollama "
                        "stream JSON:",
                        e,
                    )

                    continue

                message = data.get(
                    "message",
                    {},
                )

                if not isinstance(
                    message,
                    dict,
                ):
                    message = {}

                role = message.get(
                    "role",
                    "assistant",
                )

                content = message.get(
                    "content",
                    "",
                )

                # ------------------------------------------------
                # Usage
                # ------------------------------------------------

                if (
                    data.get(
                        "prompt_eval_count"
                    )
                    is not None
                ):

                    total_prompt_tokens = (
                        data.get(
                            "prompt_eval_count",
                            total_prompt_tokens,
                        )
                    )

                if (
                    data.get(
                        "eval_count"
                    )
                    is not None
                ):

                    total_completion_tokens = (
                        data.get(
                            "eval_count",
                            total_completion_tokens,
                        )
                    )

                # ------------------------------------------------
                # First chunk
                # ------------------------------------------------

                if first_chunk:

                    chunk = {
                        "id": completion_id,
                        "object":
                            "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "role": role,
                                },
                                "finish_reason": None,
                            }
                        ],
                    }

                    self.send_sse(chunk)

                    first_chunk = False

                # ------------------------------------------------
                # Content
                # ------------------------------------------------

                if content:

                    chunk = {
                        "id": completion_id,
                        "object":
                            "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "content": content,
                                },
                                "finish_reason": None,
                            }
                        ],
                    }

                    self.send_sse(chunk)

                # ------------------------------------------------
                # Tool calls
                # ------------------------------------------------

                tool_calls = message.get(
                    "tool_calls",
                    [],
                )

                if isinstance(
                    tool_calls,
                    list,
                ):

                    for index, tc in enumerate(
                        tool_calls
                    ):

                        if not isinstance(
                            tc,
                            dict,
                        ):
                            continue

                        function = tc.get(
                            "function",
                            {},
                        )

                        if not isinstance(
                            function,
                            dict,
                        ):
                            function = {}

                        call_id = tc.get(
                            "id"
                        )

                        if not call_id:
                            call_id = (
                                "call_"
                                + uuid.uuid4().hex[:16]
                            )

                        tool_index = tc.get(
                            "index",
                            index,
                        )

                        name = function.get(
                            "name",
                            "",
                        )

                        arguments = (
                            normalize_tool_arguments(
                                function.get(
                                    "arguments",
                                    {},
                                )
                            )
                        )

                        tool_chunk = {
                            "id": completion_id,
                            "object":
                                "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [
                                            {
                                                "index":
                                                    tool_index,
                                                "id":
                                                    call_id,
                                                "type":
                                                    "function",
                                                "function": {
                                                    "name":
                                                        name,
                                                    "arguments":
                                                        arguments,
                                                },
                                            }
                                        ]
                                    },
                                    "finish_reason": None,
                                }
                            ],
                        }

                        self.send_sse(
                            tool_chunk
                        )

                        saw_tool_call = True

                        print(
                            "[Stream] tool_call: "
                            f"{name} "
                            f"{arguments}"
                        )

                # ------------------------------------------------
                # Done
                # ------------------------------------------------

                if data.get("done"):

                    if saw_tool_call:
                        finish_reason = (
                            "tool_calls"
                        )
                    else:
                        finish_reason = data.get(
                            "done_reason",
                            "stop",
                        )

                    usage = {
                        "prompt_tokens":
                            total_prompt_tokens,
                        "completion_tokens":
                            total_completion_tokens,
                        "total_tokens":
                            (
                                total_prompt_tokens
                                + total_completion_tokens
                            ),
                    }

                    final_chunk = {
                        "id": completion_id,
                        "object":
                            "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason":
                                    finish_reason,
                            }
                        ],
                        "usage": usage,
                    }

                    self.send_sse(
                        final_chunk
                    )

                    break

        except BrokenPipeError:
            print(
                "[WARN] Streaming client disconnected"
            )

        except Exception:
            print(
                "[ERROR] Streaming exception:"
            )
            traceback.print_exc()

        finally:

            try:
                upstream.close()
            except Exception:
                pass

            try:
                self.send_done()
            except Exception:
                pass

            elapsed = (
                time.monotonic()
                - start_time
            )

            print(
                f"[Stream] finished "
                f"Request #{request_no} "
                f"{elapsed:.3f}s"
            )


    # ========================================================
    # Responses API (OpenAI /v1/responses)
    # ========================================================

    def handle_responses(self, request_no):

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = -1
        if content_length < 0 or content_length > MAX_REQUEST_BYTES:
            self.send_json_headers(413)
            self.wfile.write(json_bytes(openai_error(
                "Request body is too large", "invalid_request_error"
            )))
            return
        raw_body = self.rfile.read(content_length)

        try:
            body = json.loads(raw_body.decode("utf-8"))
        except Exception as e:
            self.send_json_headers(400)
            self.wfile.write(json_bytes(openai_error(f"Invalid JSON: {e}", "invalid_request_error")))
            return

        model = body.get("model", "")
        stream = bool(body.get("stream", False))
        max_output_tokens = body.get("max_output_tokens")
        instructions = body.get("instructions")
        input_data = body.get("input", [])
        tools = body.get("tools")
        tool_choice = body.get("tool_choice")

        if not isinstance(model, str) or not model:
            self.send_json_headers(400)
            self.wfile.write(json_bytes(openai_error(
                "model must be a non-empty string", "invalid_request_error"
            )))
            return

        print()
        print("[Responses Request Parameters]")
        print(f"model = {model}")
        print(f"stream = {stream}")
        print(f"max_output_tokens = {max_output_tokens}")
        print(f"input items = {len(input_data) if isinstance(input_data, list) else 1}")
        print(f"tools = {len(tools) if isinstance(tools, list) else 0}")
        print(f"tool_choice = {tool_choice}")

        messages = self._responses_input_to_messages(instructions, input_data)

        if DEBUG:
            for i, msg in enumerate(messages):
                role = msg.get("role", "")
                content = normalize_content(msg.get("content"))
                print(f"\n[{i}] {role} {len(content)} chars")
                preview = content[:300]
                if len(content) > 300:
                    preview += "..."
                if preview:
                    print("    " + preview.replace("\n", " "))
                if msg.get("tool_calls"):
                    print(f"    tool_calls = {len(msg['tool_calls'])}")
                if role == "tool":
                    print(f"    tool_call_id = {msg.get('tool_call_id')}")

        ollama_messages = [convert_message_to_ollama(m) for m in messages]

        ollama_body = {
            "model": model,
            "messages": ollama_messages,
            "think": OLLAMA_THINK,
            "stream": stream,
            "keep_alive": OLLAMA_KEEP_ALIVE,
        }

        tools_enabled = True
        if tool_choice == "none":
            tools_enabled = False

        if isinstance(tools, list) and tools and tools_enabled:
            # Responses API tools are flat; Ollama requires the definition
            # under a "function" key.  The helper also keeps already-nested
            # Chat Completions-style tools compatible.
            ollama_tools = responses_tools_to_ollama(tools)
            if ollama_tools:
                ollama_body["tools"] = ollama_tools

        if tool_choice is not None:
            if tool_choice == "auto":
                pass
            elif tool_choice == "none":
                pass
            elif isinstance(tool_choice, str) and tool_choice != "required":
                pass
            elif isinstance(tool_choice, dict):
                if tool_choice.get("type") == "function":
                    # Chat Completions has function.name; Responses has name.
                    # Normalize both to the form accepted by newer Ollama.
                    selected = tool_choice.get("function")
                    if not isinstance(selected, dict):
                        selected = {"name": tool_choice.get("name", "")}
                    if selected.get("name"):
                        ollama_body["tool_choice"] = {
                            "type": "function",
                            "function": {"name": selected["name"]},
                        }

        if max_output_tokens is not None:
            try:
                mt = int(max_output_tokens)
                if mt > 0:
                    ollama_body["options"] = {"num_predict": mt}
            except (TypeError, ValueError):
                pass

        if DEBUG:
            print()
            print("[Ollama Request]")
            try:
                debug_body = json.dumps(ollama_body, ensure_ascii=False, indent=2)
                print(debug_body[:8000])
                if len(debug_body) > 8000:
                    print("[... request log truncated ...]")
            except Exception:
                print("[WARN] Could not log request")

        request_data = json_bytes(ollama_body)

        upstream_request = Request(
            OLLAMA_URL,
            data=request_data,
            headers={
                "Content-Type": "application/json",
                "Accept": (
                    "application/x-ndjson" if stream else "application/json"
                ),
            },
            method="POST",
        )

        start_time = time.monotonic()

        def send_responses_failure(error_code, error_message):
            self.send_sse_headers()
            response_id = "resp_" + uuid.uuid4().hex[:16]
            created_at = now_unix()

            def response_object(status, error=None):
                return {
                    "id": response_id,
                    "object": "response",
                    "created_at": created_at,
                    "model": model,
                    "status": status,
                    "output": [],
                    "usage": None,
                    "error": error,
                    "incomplete_details": None,
                }

            self.send_sse({
                "type": "response.created",
                "sequence_number": 0,
                "response": response_object("in_progress"),
            })
            self.send_sse({
                "type": "response.failed",
                "sequence_number": 1,
                "response": response_object("failed", {
                    "code": error_code,
                    "message": error_message,
                }),
            })
            self.send_done()

        try:
            upstream = urlopen(upstream_request, timeout=READ_TIMEOUT)
        except HTTPError as e:
            error_body = b""
            try:
                error_body = e.read()
            except Exception:
                pass
            error_text = error_body.decode("utf-8", errors="replace")
            print(f"[ERROR] Ollama HTTP {e.code}: {error_text}")
            if stream:
                send_responses_failure("upstream_error", error_text)
            else:
                self.send_json_headers(502)
                self.wfile.write(json_bytes(openai_error(error_text, "upstream_error")))
            return
        except URLError as e:
            print(f"[ERROR] Cannot connect to Ollama: {e}")
            if stream:
                send_responses_failure(
                    "connection_error", f"Cannot connect to Ollama: {e}"
                )
            else:
                self.send_json_headers(502)
                self.wfile.write(json_bytes(openai_error(f"Cannot connect to Ollama: {e}", "connection_error")))
            return
        except Exception as e:
            print(f"[ERROR] Upstream connection error: {e}")
            if stream:
                send_responses_failure("connection_error", str(e))
            else:
                self.send_json_headers(502)
                self.wfile.write(json_bytes(openai_error(str(e), "connection_error")))
            return

        if stream:
            self.handle_responses_stream_v2(upstream, model, start_time, request_no)
        else:
            self.handle_responses_non_stream(upstream, model, start_time, request_no)

    def _responses_input_to_messages(self, instructions, input_data):

        messages = []
        system_parts = []

        if instructions:
            system_parts.append(self._responses_content_to_text(instructions))

        if isinstance(input_data, str):
            messages.append({"role": "user", "content": input_data})
            if system_parts:
                messages.insert(0, {"role": "system", "content": "\n\n".join(system_parts)})
            return messages

        if not isinstance(input_data, list):
            messages.append({"role": "user", "content": str(input_data)})
            if system_parts:
                messages.insert(0, {"role": "system", "content": "\n\n".join(system_parts)})
            return messages

        for item in input_data:
            if isinstance(item, str):
                messages.append({"role": "user", "content": item})
                continue

            if not isinstance(item, dict):
                continue

            item_type = item.get("type", "")

            if item_type == "message":
                role = item.get("role", "user")
                content = self._responses_content_to_text(item.get("content"))
                if role in ("system", "developer"):
                    system_parts.append(content)
                    continue
                messages.append({"role": role, "content": content})

            elif item_type == "function_call":
                call_id = item.get("call_id", "")
                if not call_id:
                    call_id = "call_" + uuid.uuid4().hex[:16]
                name = item.get("name", "")
                arguments = item.get("arguments", "{}")
                if not isinstance(arguments, str):
                    try:
                        arguments = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
                    except Exception:
                        arguments = "{}"
                if not messages or messages[-1].get("role") != "assistant" or not messages[-1].get("tool_calls"):
                    messages.append({"role": "assistant", "content": "", "tool_calls": []})
                index = len(messages[-1]["tool_calls"])
                messages[-1].setdefault("tool_calls", []).append({
                    "id": call_id,
                    "type": "function",
                    "index": index,
                    "function": {
                        "name": name,
                        "arguments": arguments,
                    },
                })

            elif item_type == "function_call_output":
                call_id = item.get("call_id", "")
                output = item.get("output", "")
                if not isinstance(output, str):
                    try:
                        output = json.dumps(output, ensure_ascii=False)
                    except Exception:
                        output = str(output)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": output,
                })

            elif item_type == "reasoning":
                continue

            else:
                content = item.get("content", "")
                if isinstance(content, str) and content:
                    messages.append({"role": "user", "content": content})

        if system_parts:
            messages.insert(0, {"role": "system", "content": "\n\n".join(system_parts)})

        return messages

    def _responses_content_to_text(self, content):

        if content is None:
            return ""

        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    part_type = part.get("type", "")
                    if part_type == "input_text":
                        parts.append(part.get("text", ""))
                    elif part_type == "output_text":
                        parts.append(part.get("text", ""))
                    elif part_type == "text":
                        parts.append(part.get("text", ""))
            return "".join(parts)

        return str(content)

    def handle_responses_non_stream(
        self,
        upstream,
        model,
        start_time,
        request_no,
    ):
        """Translate a non-streaming Ollama response to a Responses object."""
        try:
            data = json.loads(upstream.read().decode("utf-8"))
        except Exception as e:
            self.send_json_headers(502)
            self.wfile.write(json_bytes(openai_error(
                f"Invalid Ollama JSON: {e}", "upstream_error"
            )))
            return
        finally:
            try:
                upstream.close()
            except Exception:
                pass

        response_id = "resp_" + uuid.uuid4().hex[:16]
        message = data.get("message", {})
        if not isinstance(message, dict):
            message = {}
        output = []
        content = message.get("content", "")
        if content:
            output.append({
                "type": "message",
                "id": "msg_" + uuid.uuid4().hex[:16],
                "status": "completed",
                "role": "assistant",
                "content": [{
                    "type": "output_text",
                    "text": content,
                    "annotations": [],
                }],
            })
        for index, tc in enumerate(message.get("tool_calls", []) or []):
            if not isinstance(tc, dict):
                continue
            function = tc.get("function", {})
            if not isinstance(function, dict) or not function.get("name"):
                continue
            output.append({
                "type": "function_call",
                "id": "fc_" + uuid.uuid4().hex[:16],
                "status": "completed",
                "call_id": tc.get("id") or "call_%d_%s" % (index, uuid.uuid4().hex[:12]),
                "name": function["name"],
                "arguments": normalize_tool_arguments(function.get("arguments", {})),
            })
        prompt_tokens = data.get("prompt_eval_count", 0)
        output_tokens = data.get("eval_count", 0)
        response = {
            "id": response_id,
            "object": "response",
            "created_at": now_unix(),
            "model": model,
            "status": "completed",
            "output": output,
            "usage": {
                "input_tokens": prompt_tokens,
                "output_tokens": output_tokens,
                "total_tokens": prompt_tokens + output_tokens,
            },
            "error": None,
            "incomplete_details": None,
        }
        self.send_json_headers(200)
        self.wfile.write(json_bytes(response))
        self.wfile.flush()
        print("[Responses Complete] Request #%s %.3fs tool_calls=%s" % (
            request_no, time.monotonic() - start_time,
            len([item for item in output if item["type"] == "function_call"])
        ))

    def handle_responses_stream(
        self,
        upstream,
        model,
        start_time,
        request_no,
    ):

        self.send_sse_headers()

        response_id = "resp_" + uuid.uuid4().hex[:16]
        created_at = now_unix()

        total_prompt_tokens = 0
        total_completion_tokens = 0

        # Responses API output items.
        #
        # These are retained and included in response.completed.
        response_output = []

        # Current message output item.
        message_item = None
        message_item_id = None
        message_content = ""

        # Function-call state.
        function_call_items = {}

        output_index = 0

        print()
        print("[Responses Stream] started")

        def sse_event(event_type, payload):
            payload["type"] = event_type
            self.send_sse(payload)

        try:

            # ------------------------------------------------
            # response.created
            # ------------------------------------------------

            sse_event(
                "response.created",
                {
                    "response": {
                        "id": response_id,
                        "object": "response",
                        "created_at": created_at,
                        "model": model,
                        "status": "in_progress",
                        "output": [],
                        "usage": None,
                        "error": None,
                        "incomplete_details": None,
                    }
                },
            )

            # ------------------------------------------------
            # Read Ollama NDJSON stream
            # ------------------------------------------------

            while True:

                line = upstream.readline()

                if not line:
                    break

                line = line.strip()

                if not line:
                    continue

                try:
                    data = json.loads(
                        line.decode("utf-8")
                    )

                except Exception as e:
                    print(
                        "[WARN] Invalid Ollama "
                        f"stream JSON: {e}"
                    )
                    continue

                # ------------------------------------------------
                # Usage
                # ------------------------------------------------

                if data.get("prompt_eval_count") is not None:
                    total_prompt_tokens = data.get(
                        "prompt_eval_count",
                        total_prompt_tokens,
                    )

                if data.get("eval_count") is not None:
                    total_completion_tokens = data.get(
                        "eval_count",
                        total_completion_tokens,
                    )

                message = data.get(
                    "message",
                    {},
                )

                if not isinstance(message, dict):
                    message = {}

                content = message.get(
                    "content",
                    "",
                )

                # ------------------------------------------------
                # Assistant text
                # ------------------------------------------------

                if content:

                    # Create message output item once.
                    if message_item is None:

                        message_item_id = (
                            "msg_"
                            + uuid.uuid4().hex[:16]
                        )

                        message_item = {
                            "type": "message",
                            "id": message_item_id,
                            "status": "in_progress",
                            "role": "assistant",
                            "content": [],
                        }

                        response_output.append(
                            message_item
                        )

                        sse_event(
                            "response.output_item.added",
                            {
                                "output_index": output_index,
                                "item": message_item.copy(),
                            },
                        )

                        sse_event(
                            "response.content_part.added",
                            {
                                "item_id":
                                    message_item_id,
                                "output_index":
                                    output_index,
                                "content_index": 0,
                                "part": {
                                    "type":
                                        "output_text",
                                    "text": "",
                                    "annotations": [],
                                },
                            },
                        )

                        # The message occupies this output slot.  Function
                        # calls that follow must not reuse its output_index.
                        output_index += 1

                    message_content += content

                    sse_event(
                        "response.output_text.delta",
                        {
                            "item_id":
                                message_item_id,
                            "output_index":
                                output_index,
                            "content_index": 0,
                            "delta": content,
                        },
                    )

                # ------------------------------------------------
                # Tool calls
                # ------------------------------------------------

                tool_calls = message.get(
                    "tool_calls",
                    [],
                )

                if isinstance(tool_calls, list):

                    for index, tc in enumerate(
                        tool_calls
                    ):

                        if not isinstance(
                            tc,
                            dict,
                        ):
                            continue

                        function = tc.get(
                            "function",
                            {},
                        )

                        if not isinstance(
                            function,
                            dict,
                        ):
                            function = {}

                        # ----------------------------------------
                        # Preserve Ollama call ID.
                        # ----------------------------------------

                        call_id = tc.get("id")

                        if not call_id:
                            # Some Ollama versions omit the ID in streamed
                            # tool chunks.  A deterministic fallback prevents
                            # one logical call becoming many Responses items.
                            call_id = "call_stream_%d" % index

                        name = function.get(
                            "name",
                            "",
                        )

                        arguments = (
                            normalize_tool_arguments(
                                function.get(
                                    "arguments",
                                    {},
                                )
                            )
                        )

                        # ----------------------------------------
                        # Use one Responses item per call_id.
                        # ----------------------------------------

                        state = function_call_items.get(
                            call_id
                        )

                        if state is None:

                            fc_item_id = (
                                "fc_"
                                + uuid.uuid4().hex[:16]
                            )

                            state = {
                                "id":
                                    fc_item_id,
                                "call_id":
                                    call_id,
                                "name":
                                    name,
                                "arguments":
                                    "",
                                "completed":
                                    False,
                                "output_index":
                                    output_index,
                            }

                            function_call_items[
                                call_id
                            ] = state

                            function_call_output = {
                                "type":
                                    "function_call",
                                "id":
                                    fc_item_id,
                                "status":
                                    "in_progress",
                                "call_id":
                                    call_id,
                                "name":
                                    name,
                                "arguments":
                                    "",
                            }

                            response_output.append(
                                function_call_output
                            )

                            sse_event(
                                "response.output_item.added",
                                {
                                    "output_index":
                                        output_index,
                                    "item":
                                        function_call_output.copy(),
                                },
                            )

                            output_index += 1

                            print(
                                "[Responses Stream] "
                                "tool_call started: "
                                f"{name} "
                                f"call_id={call_id}"
                            )

                        # ----------------------------------------
                        # Arguments
                        #
                        # Ollama currently returns the complete
                        # arguments object in one stream chunk.
                        #
                        # If arguments arrive repeatedly for the
                        # same call, do not create another item.
                        # ----------------------------------------

                        if arguments and not state["completed"]:

                            state["arguments"] = arguments

                            # Update retained output item.
                            for item in response_output:

                                if (
                                    item.get("type")
                                    == "function_call"
                                    and item.get("id")
                                    == state["id"]
                                ):
                                    item["arguments"] = (
                                        arguments
                                    )
                                    break

                            sse_event(
                                "response.function_call_arguments.delta",
                                {
                                    "item_id":
                                        state["id"],
                                    "output_index":
                                        state["output_index"],
                                    "delta":
                                        arguments,
                                },
                            )

                            sse_event(
                                "response.function_call_arguments.done",
                                {
                                    "item_id":
                                        state["id"],
                                    "output_index":
                                        state["output_index"],
                                    "arguments":
                                        arguments,
                                },
                            )

                            # ------------------------------------
                            # Complete function call item.
                            # ------------------------------------

                            for item in response_output:

                                if (
                                    item.get("type")
                                    == "function_call"
                                    and item.get("id")
                                    == state["id"]
                                ):
                                    item["status"] = (
                                        "completed"
                                    )
                                    break

                            state["completed"] = True

                            sse_event(
                                "response.output_item.done",
                                {
                                    "output_index":
                                        state["output_index"],
                                    "item": {
                                        "type":
                                            "function_call",
                                        "id":
                                            state["id"],
                                        "status":
                                            "completed",
                                        "call_id":
                                            call_id,
                                        "name":
                                            name,
                                        "arguments":
                                            arguments,
                                    },
                                },
                            )

                            print(
                                "[Responses Stream] "
                                "tool_call completed: "
                                f"{name} "
                                f"arguments={arguments}"
                            )

                # ------------------------------------------------
                # Ollama done
                # ------------------------------------------------

                if data.get("done"):

                    # --------------------------------------------
                    # Complete assistant message if present.
                    # --------------------------------------------

                    if message_item is not None:

                        message_item["status"] = (
                            "completed"
                        )

                        message_item["content"] = [
                            {
                                "type":
                                    "output_text",
                                "text":
                                    message_content,
                                "annotations": [],
                            }
                        ]

                        message_output_index = 0

                        # Find the actual output index.
                        for i, item in enumerate(
                            response_output
                        ):
                            if (
                                item.get("id")
                                == message_item_id
                            ):
                                message_output_index = i
                                break

                        sse_event(
                            "response.output_item.done",
                            {
                                "output_index":
                                    message_output_index,
                                "item":
                                    message_item,
                            },
                        )

                        sse_event(
                            "response.output_text.done",
                            {
                                "item_id": message_item_id,
                                "output_index": message_output_index,
                                "content_index": 0,
                                "text": message_content,
                            },
                        )

                        sse_event(
                            "response.content_part.done",
                            {
                                "item_id": message_item_id,
                                "output_index": message_output_index,
                                "content_index": 0,
                                "part": message_item["content"][0],
                            },
                        )

                    # --------------------------------------------
                    # Usage
                    # --------------------------------------------

                    usage = {
                        "input_tokens":
                            total_prompt_tokens,
                        "output_tokens":
                            total_completion_tokens,
                        "total_tokens":
                            (
                                total_prompt_tokens
                                + total_completion_tokens
                            ),
                    }

                    # --------------------------------------------
                    # Final response
                    #
                    # IMPORTANT:
                    # Do NOT return output=[].
                    #
                    # Codex needs the function_call item here.
                    # --------------------------------------------

                    sse_event(
                        "response.completed",
                        {
                            "response": {
                                "id":
                                    response_id,
                                "object":
                                    "response",
                                "created_at":
                                    created_at,
                                "model":
                                    model,
                                "status":
                                    "completed",
                                "output":
                                    response_output,
                                "usage":
                                    usage,
                                "error":
                                    None,
                                "incomplete_details":
                                    None,
                            }
                        },
                    )

                    break

        except BrokenPipeError:

            print(
                "[WARN] Responses streaming "
                "client disconnected"
            )

        except Exception:

            print(
                "[ERROR] Responses streaming exception:"
            )

            traceback.print_exc()

        finally:

            try:
                upstream.close()
            except Exception:
                pass

            try:
                self.send_done()
            except Exception:
                pass

            elapsed = (
                time.monotonic()
                - start_time
            )

            print(
                "[Responses Stream] finished "
                f"Request #{request_no} "
                f"{elapsed:.3f}s"
            )


    def handle_responses_stream_v2(self, upstream, model, start_time, request_no):
        """Emit a coherent Responses SSE stream from Ollama NDJSON.

        All intermediate events and the final response are produced from one
        retained state.  Tool calls are keyed by Ollama id when available and
        by their stable stream slot otherwise.  They are only finalized after
        Ollama's done marker, so repeated/full argument chunks cannot create
        duplicate or prematurely completed calls.
        """
        self.send_sse_headers()
        response_id = "resp_" + uuid.uuid4().hex[:16]
        created_at = now_unix()
        output = []
        message_state = None
        calls_by_key = {}
        calls_in_order = []
        prompt_tokens = 0
        completion_tokens = 0
        sequence_number = 0
        saw_done = False
        terminal_sent = False

        def event(event_type, **fields):
            nonlocal sequence_number
            payload = {"type": event_type, "sequence_number": sequence_number}
            sequence_number += 1
            payload.update(fields)
            if DEBUG:
                print("[Responses SSE]", event_type, fields.get("output_index", ""))
            self.send_sse(payload)

        def response_object(status, error=None, incomplete_details=None):
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
                "created_at": created_at,
                "model": model,
                "status": status,
                "output": output,
                "usage": usage,
                "error": error,
                "incomplete_details": incomplete_details,
            }

        def ensure_message():
            nonlocal message_state
            if message_state is not None:
                return message_state
            index = len(output)
            item = {
                "type": "message",
                "id": "msg_" + uuid.uuid4().hex[:16],
                "status": "in_progress",
                "role": "assistant",
                "content": [],
            }
            message_state = {"index": index, "item": item, "text": ""}
            output.append(item)
            event("response.output_item.added", output_index=index, item=dict(item))
            event(
                "response.content_part.added",
                item_id=item["id"], output_index=index, content_index=0,
                part={"type": "output_text", "text": "", "annotations": []},
            )
            return message_state

        def ensure_call(tc, stream_index):
            function = tc.get("function")
            if not isinstance(function, dict):
                function = {}
            upstream_id = tc.get("id")
            upstream_index = tc.get("index", stream_index)
            key = ("id", str(upstream_id)) if upstream_id else ("index", upstream_index)
            state = calls_by_key.get(key)
            if state is None:
                index = len(output)
                call_id = str(upstream_id) if upstream_id else "call_" + uuid.uuid4().hex[:24]
                item = {
                    "type": "function_call",
                    "id": "fc_" + uuid.uuid4().hex[:16],
                    "status": "in_progress",
                    "call_id": call_id,
                    "name": str(function.get("name") or ""),
                    "arguments": "",
                }
                state = {"key": key, "index": index, "item": item, "arguments": ""}
                calls_by_key[key] = state
                calls_in_order.append(state)
                output.append(item)
                event("response.output_item.added", output_index=index, item=dict(item))
                print("[Responses] tool call started name=%s call_id=%s" % (
                    item["name"], call_id
                ))
            if function.get("name"):
                state["item"]["name"] = str(function["name"])
            if "arguments" in function:
                state["arguments"] = normalize_tool_arguments(function.get("arguments"))
            return state

        def finalize_message():
            if message_state is None or message_state["item"]["status"] == "completed":
                return
            item = message_state["item"]
            index = message_state["index"]
            text = message_state["text"]
            part = {"type": "output_text", "text": text, "annotations": []}
            item["content"] = [part]
            event(
                "response.output_text.done", item_id=item["id"],
                output_index=index, content_index=0, text=text,
            )
            event(
                "response.content_part.done", item_id=item["id"],
                output_index=index, content_index=0, part=part,
            )
            item["status"] = "completed"
            event("response.output_item.done", output_index=index, item=dict(item))

        def finalize_calls():
            for state in calls_in_order:
                item = state["item"]
                if item["status"] == "completed":
                    continue
                arguments = state["arguments"] or "{}"
                item["arguments"] = arguments
                event(
                    "response.function_call_arguments.delta",
                    item_id=item["id"], output_index=state["index"], delta=arguments,
                )
                event(
                    "response.function_call_arguments.done",
                    item_id=item["id"], output_index=state["index"], arguments=arguments,
                )
                item["status"] = "completed"
                event("response.output_item.done", output_index=state["index"], item=dict(item))
                print("[Responses] tool call completed name=%s call_id=%s" % (
                    item["name"], item["call_id"]
                ))

        try:
            # urllib does not expose a public read-timeout setter.  Walk the
            # known response wrappers and set the underlying socket when it is
            # available; otherwise READ_TIMEOUT remains the safe fallback.
            sock = getattr(getattr(getattr(upstream, "fp", None), "raw", None), "_sock", None)
            if sock is not None and STREAM_IDLE_TIMEOUT > 0:
                sock.settimeout(STREAM_IDLE_TIMEOUT)

            event("response.created", response=response_object("in_progress"))
            event("response.in_progress", response=response_object("in_progress"))

            while True:
                line = upstream.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RuntimeError("Invalid Ollama stream JSON: %s" % exc) from exc
                if not isinstance(data, dict):
                    raise RuntimeError("Invalid Ollama stream item")
                if data.get("error"):
                    raise RuntimeError(str(data["error"]))

                if data.get("prompt_eval_count") is not None:
                    prompt_tokens = int(data.get("prompt_eval_count") or 0)
                if data.get("eval_count") is not None:
                    completion_tokens = int(data.get("eval_count") or 0)

                message = data.get("message")
                if not isinstance(message, dict):
                    message = {}
                content = message.get("content")
                if content:
                    state = ensure_message()
                    delta = str(content)
                    state["text"] += delta
                    event(
                        "response.output_text.delta", item_id=state["item"]["id"],
                        output_index=state["index"], content_index=0, delta=delta,
                    )

                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list):
                    for stream_index, tc in enumerate(tool_calls):
                        if isinstance(tc, dict):
                            ensure_call(tc, stream_index)

                if data.get("done"):
                    saw_done = True
                    break

            if not saw_done:
                raise RuntimeError("Ollama stream ended before its done marker")

            finalize_message()
            finalize_calls()
            event("response.completed", response=response_object("completed"))
            terminal_sent = True

        except (socket.timeout, TimeoutError) as exc:
            error = {"code": "stream_timeout", "message": str(exc) or "Ollama stream timed out"}
            try:
                event("response.failed", response=response_object("failed", error=error))
                terminal_sent = True
            except (BrokenPipeError, ConnectionResetError):
                pass
            print("[ERROR] Responses stream idle timeout:", exc)
        except (BrokenPipeError, ConnectionResetError):
            print("[WARN] Responses streaming client disconnected")
        except Exception as exc:
            print("[ERROR] Responses streaming exception:", exc)
            if DEBUG:
                traceback.print_exc()
            error = {"code": "upstream_error", "message": str(exc)}
            try:
                event("response.failed", response=response_object("failed", error=error))
                terminal_sent = True
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            try:
                upstream.close()
            except Exception:
                pass
            if terminal_sent:
                try:
                    self.send_done()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            print("[Responses Stream] finished Request #%s %.3fs" % (
                request_no, time.monotonic() - start_time
            ))


# ============================================================
# Server
# ============================================================

def main():

    server = ThreadingHTTPServer(
        (HOST, PORT),
        ProxyHandler,
    )

    print("=" * 70)
    print("Ollama Agent Proxy")
    print("=" * 70)
    print(f"Server : {SERVER_NAME}")
    print(f"Listen : http://{HOST}:{PORT}")
    print(f"Ollama : {OLLAMA_URL}")
    print("=" * 70)

    try:
        server.serve_forever()

    except KeyboardInterrupt:
        print()
        print("[INFO] Shutting down")

    finally:
        server.server_close()


if __name__ == "__main__":
    main()
