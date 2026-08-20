import io
import json
import unittest
from urllib.error import URLError

from ollama import OllamaClient, OllamaConnectionError, OllamaInvalidResponse


class FakeResponse:
    def __init__(self, raw=b"", lines=None):
        self.raw = raw
        self.lines = list(lines or [])
        self.closed = False

    def read(self):
        return self.raw

    def readline(self):
        if not self.lines:
            return b""
        return self.lines.pop(0)

    def close(self):
        self.closed = True


def make_client(opener):
    return OllamaClient(
        "http://ollama.test/",
        connect_timeout=2,
        read_timeout=3,
        stream_idle_timeout=4,
        keep_alive="30m",
        think=False,
        opener=opener,
    )


class OllamaTests(unittest.TestCase):
    def test_list_models_closes_response(self):
        response = FakeResponse(b'{"models":[{"name":"m"}]}')
        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            return response

        data = make_client(opener).list_models()
        self.assertEqual(data["models"][0]["name"], "m")
        self.assertTrue(response.closed)
        self.assertEqual(calls[0][0].full_url, "http://ollama.test/api/tags")
        self.assertEqual(calls[0][1], 2)

    def test_chat_adds_common_options(self):
        response = FakeResponse(b'{"message":{"content":"ok"}}')
        captured = {}

        def opener(request, timeout):
            captured["body"] = json.loads(request.data.decode())
            captured["accept"] = request.get_header("Accept")
            captured["timeout"] = timeout
            return response

        make_client(opener).chat({"model": "m", "stream": False})
        self.assertEqual(captured["body"]["keep_alive"], "30m")
        self.assertFalse(captured["body"]["think"])
        self.assertEqual(captured["accept"], "application/json")
        self.assertEqual(captured["timeout"], 3)
        self.assertTrue(response.closed)

    def test_stream_closes_on_early_exit(self):
        response = FakeResponse(
            lines=[
                b'{"message":{"content":"a"}}\n',
                b'{"message":{"content":"b"}}\n',
            ]
        )
        with make_client(lambda request, timeout: response).stream_chat({"model": "m"}) as items:
            self.assertEqual(next(items)["message"]["content"], "a")
        self.assertTrue(response.closed)

    def test_invalid_json_and_skip_policy(self):
        response = FakeResponse(lines=[b"bad\n", b'{"done":true}\n'])
        with make_client(lambda request, timeout: response).stream_chat(
            {"model": "m"}, skip_invalid=True
        ) as items:
            self.assertEqual(list(items), [{"done": True}])

        response = FakeResponse(lines=[b"bad\n"])
        with self.assertRaises(OllamaInvalidResponse):
            with make_client(lambda request, timeout: response).stream_chat({"model": "m"}) as items:
                list(items)
        self.assertTrue(response.closed)

    def test_url_error_is_normalized(self):
        def opener(request, timeout):
            raise URLError("offline")

        with self.assertRaises(OllamaConnectionError) as caught:
            make_client(opener).chat({"model": "m"})
        self.assertIn("Cannot connect to Ollama", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
