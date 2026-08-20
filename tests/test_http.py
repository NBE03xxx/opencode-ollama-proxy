from contextlib import contextmanager
import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer

from ollama import OllamaConnectionError
from proxy import Settings, configured_handler


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


class HTTPTests(unittest.TestCase):
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
