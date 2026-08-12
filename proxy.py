#!/usr/bin/env python3

import json
import os
import time
import uuid
import traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


# ============================================================
# Configuration
# ============================================================

OLLAMA_HOST = os.environ.get(
    "OLLAMA_HOST",
    "http://192.168.1.253:11434",
)

OLLAMA_URL = OLLAMA_HOST.rstrip("/") + "/api/chat"

HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
PORT = int(os.environ.get("LISTEN_PORT", "8000"))

CONNECT_TIMEOUT = 30
READ_TIMEOUT = 60 * 60 * 6  # 6 hours

SERVER_NAME = "OpenCode/Ollama native tool-calling proxy"


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

        if self.path != "/v1/chat/completions":
            self.send_json_headers(404)
            self.wfile.write(
                json_bytes(
                    openai_error(
                        "Not found",
                        "not_found",
                    )
                )
            )
            return

        ProxyHandler.request_counter += 1
        request_no = ProxyHandler.request_counter

        print()
        print("=" * 70)
        print(f"Request #{request_no}")
        print("=" * 70)

        try:
            self.handle_chat_completion(request_no)

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

            # Current environment intentionally disables
            # Qwen thinking.
            "think": False,

            "stream": stream,
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

        if isinstance(tools, list) and tools:
            ollama_body["tools"] = tools

        # ----------------------------------------------------
        # tool_choice
        #
        # Ollama currently does not require us to force the
        # OpenAI tool_choice representation.
        #
        # For "auto" we simply omit it.
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

                        print(
                            "[Stream] tool_call: "
                            f"{name} "
                            f"{arguments}"
                        )

                # ------------------------------------------------
                # Done
                # ------------------------------------------------

                if data.get("done"):

                    if tool_calls:
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


# ============================================================
# Server
# ============================================================

def main():

    server = ThreadingHTTPServer(
        (HOST, PORT),
        ProxyHandler,
    )

    print("=" * 70)
    print("OpenCode / Ollama proxy")
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
