import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
import unittest
from urllib.error import URLError
from urllib.request import Request

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


class FakeHeartbeatConnection:
    def __init__(self, opener, url, read_timeout):
        self.opener = opener
        self.url = url
        self.read_timeout = read_timeout
        self.response = None
        self.closed = False

    def open(self, body):
        request = Request(
            self.url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/x-ndjson",
            },
            method="POST",
        )
        self.response = self.opener(request, self.read_timeout)
        return self.response

    def close(self):
        self.closed = True
        if self.response is not None:
            self.response.close()


def make_client(opener, *, think=False, heartbeat_connection_factory=None):
    if heartbeat_connection_factory is None:
        heartbeat_connection_factory = lambda url, _connect, read: (
            FakeHeartbeatConnection(opener, url, read)
        )
    return OllamaClient(
        "http://ollama.test/",
        connect_timeout=2,
        read_timeout=3,
        stream_idle_timeout=4,
        keep_alive="30m",
        think=think,
        opener=opener,
        heartbeat_connection_factory=heartbeat_connection_factory,
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

    def test_chat_preserves_thinking_level(self):
        response = FakeResponse(b'{"message":{"content":"ok"}}')
        captured = {}

        def opener(request, timeout):
            captured["body"] = json.loads(request.data.decode())
            return response

        make_client(opener, think="high").chat({"model": "m", "stream": False})
        self.assertEqual(captured["body"]["think"], "high")

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

    def test_heartbeat_stream_yields_during_silence(self):
        class SlowResponse(FakeResponse):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def readline(self):
                self.calls += 1
                if self.calls == 1:
                    time.sleep(0.04)
                    return b'{"message":{"content":"a"}}\n'
                return b""

        response = SlowResponse()
        with make_client(lambda request, timeout: response).stream_chat_with_heartbeat(
            {"model": "m"}, heartbeat_interval=0.01
        ) as items:
            self.assertIsNone(next(items))
            item = next(items)
            while item is None:
                item = next(items)
            self.assertEqual(item["message"]["content"], "a")
        self.assertTrue(response.closed)

    def test_heartbeat_repeats_and_resets_after_an_item(self):
        release = threading.Event()

        class ControlledResponse(FakeResponse):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def readline(self):
                self.calls += 1
                if self.calls == 1:
                    release.wait()
                    return b'{"message":{"content":"a"}}\n'
                if self.calls == 2:
                    time.sleep(0.025)
                    return b'{"message":{},"done":true}\n'
                return b""

        response = ControlledResponse()
        with make_client(
            lambda request, timeout: response
        ).stream_chat_with_heartbeat(
            {"model": "m"}, heartbeat_interval=0.01
        ) as items:
            self.assertIsNone(next(items))
            self.assertIsNone(next(items))
            release.set()
            item = next(items)
            while item is None:
                item = next(items)
            self.assertEqual(item["message"]["content"], "a")
            started = time.monotonic()
            self.assertIsNone(next(items))
            self.assertGreaterEqual(time.monotonic() - started, 0.007)

    def test_heartbeat_cleanup_unblocks_open_and_joins_reader(self):
        created = []

        class BlockingConnection:
            def __init__(self):
                self.opened = threading.Event()
                self.cancelled = threading.Event()
                created.append(self)

            def open(self, body):
                self.opened.set()
                self.cancelled.wait(timeout=1)
                raise OSError("cancelled")

            def close(self):
                self.cancelled.set()

        before = set(threading.enumerate())
        with make_client(
            lambda request, timeout: None,
            heartbeat_connection_factory=lambda *_args: BlockingConnection(),
        ).stream_chat_with_heartbeat(
            {"model": "m"}, heartbeat_interval=0.01
        ) as items:
            self.assertTrue(created[0].opened.wait(timeout=1))
            self.assertIsNone(next(items))
        self.assertTrue(created[0].cancelled.is_set())
        remaining = [
            thread
            for thread in threading.enumerate()
            if thread not in before and thread.name == "ollama-stream-reader"
        ]
        self.assertEqual(remaining, [])

    def test_heartbeat_cleanup_unblocks_read_and_joins_reader(self):
        created = []

        class BlockingResponse(FakeResponse):
            def __init__(self):
                super().__init__()
                self.reading = threading.Event()
                self.cancelled = threading.Event()

            def readline(self):
                self.reading.set()
                self.cancelled.wait(timeout=1)
                return b""

            def close(self):
                super().close()
                self.cancelled.set()

        class BlockingConnection:
            def __init__(self):
                self.response = BlockingResponse()
                created.append(self)

            def open(self, body):
                return self.response

            def close(self):
                self.response.close()

        before = set(threading.enumerate())
        with make_client(
            lambda request, timeout: None,
            heartbeat_connection_factory=lambda *_args: BlockingConnection(),
        ).stream_chat_with_heartbeat(
            {"model": "m"}, heartbeat_interval=0.01
        ) as items:
            self.assertTrue(created[0].response.reading.wait(timeout=1))
            self.assertIsNone(next(items))
        self.assertTrue(created[0].response.closed)
        remaining = [
            thread
            for thread in threading.enumerate()
            if thread not in before and thread.name == "ollama-stream-reader"
        ]
        self.assertEqual(remaining, [])

    def test_heartbeat_starts_before_upstream_open_completes(self):
        response = FakeResponse(lines=[b'{"message":{},"done":true}\n'])

        def slow_opener(request, timeout):
            time.sleep(0.04)
            return response

        with make_client(slow_opener).stream_chat_with_heartbeat(
            {"model": "m"}, heartbeat_interval=0.01
        ) as items:
            self.assertIsNone(next(items))
            item = next(items)
            while item is None:
                item = next(items)
            self.assertTrue(item["done"])
        self.assertTrue(response.closed)

    def test_real_heartbeat_connection_streams_and_sends_options(self):
        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                length = int(self.headers["Content-Length"])
                captured["body"] = json.loads(self.rfile.read(length))
                captured["connection"] = self.headers["Connection"]
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                self.wfile.write(b'{"message":{"content":"a"}}\n')
                self.wfile.write(b'{"message":{},"done":true}\n')
                self.wfile.flush()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            client = OllamaClient(
                f"http://127.0.0.1:{server.server_address[1]}",
                connect_timeout=1,
                read_timeout=1,
                stream_idle_timeout=1,
                keep_alive="30m",
                think=False,
            )
            with client.stream_chat_with_heartbeat(
                {"model": "m"}, heartbeat_interval=0.1
            ) as items:
                received = [item for item in items if item is not None]
            self.assertEqual(received[-1]["done"], True)
            self.assertEqual(captured["body"]["keep_alive"], "30m")
            self.assertFalse(captured["body"]["think"])
            self.assertEqual(captured["connection"], "close")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_real_heartbeat_connection_cancels_response_header_wait(self):
        request_received = threading.Event()
        release_handler = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                length = int(self.headers["Content-Length"])
                self.rfile.read(length)
                request_received.set()
                release_handler.wait(timeout=3)
                try:
                    self.send_response(200)
                    self.end_headers()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        before = set(threading.enumerate())
        try:
            client = OllamaClient(
                f"http://127.0.0.1:{server.server_address[1]}",
                connect_timeout=1,
                read_timeout=2,
                stream_idle_timeout=1,
                keep_alive="30m",
                think=False,
            )
            started = time.monotonic()
            with client.stream_chat_with_heartbeat(
                {"model": "m"}, heartbeat_interval=0.01
            ) as items:
                self.assertTrue(request_received.wait(timeout=1))
                self.assertIsNone(next(items))
            self.assertLess(time.monotonic() - started, 1.5)
            remaining = [
                current
                for current in threading.enumerate()
                if current not in before and current.name == "ollama-stream-reader"
            ]
            self.assertEqual(remaining, [])
        finally:
            release_handler.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_real_heartbeat_connection_cancels_ndjson_read_wait(self):
        response_started = threading.Event()
        release_handler = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                length = int(self.headers["Content-Length"])
                self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                self.wfile.flush()
                response_started.set()
                release_handler.wait(timeout=3)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        before = set(threading.enumerate())
        try:
            client = OllamaClient(
                f"http://127.0.0.1:{server.server_address[1]}",
                connect_timeout=1,
                read_timeout=2,
                stream_idle_timeout=1,
                keep_alive="30m",
                think=False,
            )
            started = time.monotonic()
            with client.stream_chat_with_heartbeat(
                {"model": "m"}, heartbeat_interval=0.01
            ) as items:
                self.assertTrue(response_started.wait(timeout=1))
                self.assertIsNone(next(items))
            self.assertLess(time.monotonic() - started, 1.5)
            remaining = [
                current
                for current in threading.enumerate()
                if current not in before and current.name == "ollama-stream-reader"
            ]
            self.assertEqual(remaining, [])
        finally:
            release_handler.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_heartbeat_invalid_json_propagates_and_joins_reader(self):
        response = FakeResponse(lines=[b"bad\n"])
        before = set(threading.enumerate())
        with self.assertRaises(OllamaInvalidResponse):
            with make_client(
                lambda request, timeout: response
            ).stream_chat_with_heartbeat(
                {"model": "m"}, heartbeat_interval=0.01
            ) as items:
                list(items)
        self.assertTrue(response.closed)
        remaining = [
            thread
            for thread in threading.enumerate()
            if thread not in before and thread.name == "ollama-stream-reader"
        ]
        self.assertEqual(remaining, [])

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
