from contextlib import contextmanager
import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer

from ollama import OllamaConnectionError
from proxy import Settings, configured_handler, message_layout_lines


class FakeClient:
    def __init__(self):
        self.requests = []
        self.fail = False

    def list_models(self):
        if self.fail:
            raise OllamaConnectionError("offline")
        return {"models": [{"name": "qwen"}, {"name": ""}]}

    def chat(self, body):
        self.requests.append(body)
        if self.fail:
            raise OllamaConnectionError("offline")
        if body.get("model") == "explode":
            raise RuntimeError("unexpected")
        if body.get("model") == "tool-model":
            return {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"id": "call_1", "function": {"name": "f", "arguments": {"x": 1}}}
                    ],
                },
                "prompt_eval_count": 2,
                "eval_count": 1,
            }
        return {
            "message": {"content": "hello"},
            "prompt_eval_count": 2,
            "eval_count": 1,
            "done_reason": "stop",
        }

    @contextmanager
    def stream_chat(self, body, *, skip_invalid=False, use_idle_timeout=True):
        self.requests.append(body)
        if self.fail:
            raise OllamaConnectionError("offline")
        if body.get("model") == "timeout-model":
            def timeout_items():
                yield {"message": {"role": "assistant", "content": "partial"}}
                raise TimeoutError("idle")

            yield timeout_items()
        elif body.get("model") == "responses-model":
            yield iter(
                [
                    {"message": {"content": "hello"}},
                    {"message": {}, "done": True, "prompt_eval_count": 2, "eval_count": 1},
                ]
            )
        else:
            yield iter(
                [
                    {"message": {"role": "assistant", "content": "hello"}},
                    {"message": {}, "done": True, "prompt_eval_count": 2, "eval_count": 1},
                ]
            )

    @contextmanager
    def stream_chat_with_heartbeat(self, body, *, heartbeat_interval):
        self.requests.append(body)
        if self.fail:
            raise OllamaConnectionError("offline")
        yield iter(
            [
                None,
                {"message": {"thinking": "private"}},
                {"message": {"content": "hello"}},
                {
                    "message": {},
                    "done": True,
                    "prompt_eval_count": 2,
                    "eval_count": 1,
                    "done_reason": "stop",
                },
            ]
        )


class HTTPTests(unittest.TestCase):
    def test_message_layout_diagnostics_do_not_include_content(self):
        lines = message_layout_lines(
            [
                {"role": "system", "content": "top secret"},
                {"role": "developer", "content": [{"type": "text", "text": "private"}]},
                {"role": "user", "content": "hello"},
            ]
        )
        rendered = "\n".join(lines)
        self.assertIn("count=3", rendered)
        self.assertIn("system_count=1", rendered)
        self.assertIn("system_positions=[0]", rendered)
        self.assertIn("developer_count=1", rendered)
        self.assertIn("content_type=str", rendered)
        self.assertIn("content_type=list", rendered)
        self.assertIn("length=10", rendered)
        self.assertNotIn("top secret", rendered)
        self.assertNotIn("private", rendered)

    @classmethod
    def setUpClass(cls):
        cls.client = FakeClient()
        settings = Settings(listen_host="127.0.0.1", listen_port=0, max_request_bytes=128)
        cls.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), configured_handler(settings, cls.client)
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.client.fail = False
        self.client.requests.clear()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            headers = {"Content-Type": "application/json", **(headers or {})}
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        raw = response.read()
        result = (response.status, dict(response.getheaders()), raw)
        connection.close()
        return result

    def test_health_and_not_found(self):
        status, headers, raw = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), {"status": "ok"})
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        status, _, raw = self.request("GET", "/missing")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(raw)["error"]["type"], "not_found")

        status, _, raw = self.request("HEAD", "/api/hello")
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"")

    def test_models(self):
        status, _, raw = self.request("GET", "/v1/models")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["data"], [{"id": "qwen", "object": "model", "created": 0, "owned_by": "ollama"}])

    def test_chat_non_stream(self):
        status, _, raw = self.request(
            "POST",
            "/v1/chat/completions",
            {"model": "m", "messages": [{"role": "user", "content": "hi"}]},
        )
        payload = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(payload["object"], "chat.completion")
        self.assertEqual(payload["choices"][0]["message"]["content"], "hello")

    def test_chat_stream_wire_format(self):
        status, headers, raw = self.request(
            "POST",
            "/v1/chat/completions",
            {"model": "m", "messages": [], "stream": True},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/event-stream; charset=utf-8")
        self.assertTrue(raw.startswith(b"data: {"))
        self.assertTrue(raw.endswith(b"data: [DONE]\n\n"))
        self.assertIn(b'"finish_reason":"stop"', raw)

    def test_chat_stream_failure_still_terminates_sse(self):
        status, _, raw = self.request(
            "POST",
            "/v1/chat/completions",
            {"model": "timeout-model", "messages": [], "stream": True},
        )
        self.assertEqual(status, 200)
        self.assertIn(b'"content":"partial"', raw)
        self.assertTrue(raw.endswith(b"data: [DONE]\n\n"))

    def test_responses_non_stream_and_stream(self):
        status, _, raw = self.request(
            "POST", "/v1/responses", {"model": "responses-model", "input": "hi"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["object"], "response")

        status, _, raw = self.request(
            "POST",
            "/v1/responses",
            {"model": "responses-model", "input": "hi", "stream": True},
        )
        self.assertEqual(status, 200)
        self.assertIn(b'"type":"response.created"', raw)
        self.assertIn(b'"type":"response.completed"', raw)
        self.assertTrue(raw.endswith(b"data: [DONE]\n\n"))

    def test_messages_non_stream_and_named_stream(self):
        request = {
            "model": "messages-model",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "hi"}],
        }
        status, _, raw = self.request("POST", "/v1/messages?beta=true", request)
        self.assertEqual(status, 200)
        payload = json.loads(raw)
        self.assertEqual(payload["type"], "message")
        self.assertEqual(payload["content"], [{"type": "text", "text": "hello"}])

        status, headers, raw = self.request(
            "POST",
            "/v1/messages",
            {**request, "stream": True},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/event-stream; charset=utf-8")
        self.assertTrue(raw.startswith(b"event: message_start\n"))
        self.assertIn(b"event: ping\n", raw)
        self.assertIn(b"event: message_stop\n", raw)
        self.assertNotIn(b"private", raw)
        self.assertNotIn(b"[DONE]", raw)

    def test_messages_validation_and_upstream_error_shape(self):
        status, _, raw = self.request(
            "POST",
            "/v1/messages",
            {"model": "m", "messages": []},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["type"], "error")
        self.assertEqual(json.loads(raw)["error"]["type"], "invalid_request_error")

        self.client.fail = True
        status, _, raw = self.request(
            "POST",
            "/v1/messages",
            {"model": "m", "max_tokens": 1, "messages": []},
        )
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(raw)["type"], "error")
        self.assertEqual(json.loads(raw)["error"]["type"], "api_error")

        self.client.fail = False
        status, _, raw = self.request(
            "POST",
            "/v1/messages",
            b"x" * 129,
            {"Content-Length": "129"},
        )
        self.assertEqual(status, 413)
        self.assertEqual(json.loads(raw)["type"], "error")
        self.assertEqual(json.loads(raw)["error"]["type"], "request_too_large")

        status, _, raw = self.request(
            "POST",
            "/v1/messages/count_tokens?beta=true",
            {"model": "m", "messages": []},
        )
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(raw)["type"], "error")
        self.assertEqual(json.loads(raw)["error"]["type"], "not_found_error")

        status, _, raw = self.request(
            "POST",
            "/v1/messages",
            {
                "model": "explode",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        self.assertEqual(status, 500)
        self.assertEqual(json.loads(raw)["type"], "error")
        self.assertEqual(json.loads(raw)["error"]["type"], "api_error")

    def test_invalid_requests(self):
        status, _, raw = self.request(
            "POST", "/v1/responses", b"not-json", {"Content-Length": "8"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["error"]["type"], "invalid_request_error")

        status, _, raw = self.request("POST", "/v1/responses", {"input": "hi"})
        self.assertEqual(status, 400)
        self.assertIn("model", json.loads(raw)["error"]["message"])

        status, _, _ = self.request(
            "POST",
            "/v1/responses",
            b"x" * 129,
            {"Content-Length": "129"},
        )
        self.assertEqual(status, 413)

    def test_upstream_failure_formats(self):
        self.client.fail = True
        status, _, raw = self.request(
            "POST", "/v1/chat/completions", {"model": "m", "messages": []}
        )
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(raw)["error"]["type"], "connection_error")

        status, _, raw = self.request(
            "POST",
            "/v1/responses",
            {"model": "m", "input": "hi", "stream": True},
        )
        self.assertEqual(status, 200)
        self.assertIn(b'"type":"response.failed"', raw)
        self.assertTrue(raw.endswith(b"data: [DONE]\n\n"))


if __name__ == "__main__":
    unittest.main()
