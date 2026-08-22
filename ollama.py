"""Ollama HTTP transport with consistent errors and response ownership."""

from contextlib import contextmanager
import http.client
import json
from queue import Empty, Queue
import socket
import threading
import time
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from common import json_bytes


class OllamaError(Exception):
    """Base error raised by the Ollama transport."""


class OllamaHTTPError(OllamaError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class OllamaConnectionError(OllamaError):
    pass


class OllamaInvalidResponse(OllamaError):
    pass


class _StreamCancelled(Exception):
    """Internal signal used when a heartbeat stream is deliberately closed."""


class _HeartbeatHTTPConnection:
    """Cancelable HTTP connection owned for one heartbeat-enabled stream."""

    def __init__(
        self,
        url: str,
        connect_timeout: float,
        read_timeout: float,
    ) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise OllamaConnectionError(f"Invalid Ollama URL: {url}")
        connection_class = (
            http.client.HTTPSConnection
            if parsed.scheme == "https"
            else http.client.HTTPConnection
        )
        self._connection = connection_class(
            parsed.hostname,
            parsed.port,
            timeout=connect_timeout,
        )
        self._path = parsed.path or "/"
        if parsed.query:
            self._path += "?" + parsed.query
        self._read_timeout = read_timeout
        self._lock = threading.Lock()
        self._cancelled = False
        self._response: Any = None
        self._response_socket: Any = None

    def _raise_if_cancelled(self) -> None:
        with self._lock:
            if self._cancelled:
                raise _StreamCancelled()

    def open(self, body: bytes) -> Any:
        try:
            self._raise_if_cancelled()
            self._connection.connect()
            self._raise_if_cancelled()
            sock = self._connection.sock
            if sock is not None:
                sock.settimeout(self._read_timeout)
            self._connection.request(
                "POST",
                self._path,
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/x-ndjson",
                    "Connection": "close",
                },
            )
            self._raise_if_cancelled()
            response = self._connection.getresponse()
            response_socket = getattr(
                getattr(getattr(response, "fp", None), "raw", None),
                "_sock",
                None,
            )
            with self._lock:
                if self._cancelled:
                    if response_socket is not None:
                        try:
                            response_socket.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                    response.close()
                    raise _StreamCancelled()
                self._response = response
                self._response_socket = response_socket
            if response.status >= 400:
                raw = response.read()
                raise OllamaHTTPError(
                    response.status,
                    raw.decode("utf-8", errors="replace"),
                )
            return response
        except (_StreamCancelled, OllamaError):
            raise
        except (socket.timeout, TimeoutError) as exc:
            raise OllamaConnectionError(
                str(exc) or "Ollama request timed out"
            ) from exc
        except (OSError, http.client.HTTPException) as exc:
            raise OllamaConnectionError(str(exc)) from exc

    def close(self) -> None:
        with self._lock:
            self._cancelled = True
            response = self._response
            sockets = (self._connection.sock, self._response_socket)
        for sock in {value for value in sockets if value is not None}:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        try:
            self._connection.close()
        except Exception:
            pass


class OllamaClient:
    def __init__(
        self,
        host: str,
        *,
        connect_timeout: float,
        read_timeout: float,
        stream_idle_timeout: float,
        keep_alive: str,
        think: bool | str,
        opener: Callable[..., Any] = urlopen,
        heartbeat_connection_factory: Callable[[str, float, float], Any] = (
            _HeartbeatHTTPConnection
        ),
    ) -> None:
        self.host = host.rstrip("/")
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.stream_idle_timeout = stream_idle_timeout
        self.keep_alive = keep_alive
        self.think = think
        self._opener = opener
        self._heartbeat_connection_factory = heartbeat_connection_factory

    @property
    def chat_url(self) -> str:
        return self.host + "/api/chat"

    def _request_body(self, body: dict[str, Any]) -> dict[str, Any]:
        request_body = dict(body)
        request_body["think"] = self.think
        request_body["keep_alive"] = self.keep_alive
        return request_body

    def _open(self, request: Request, timeout: float):
        try:
            return self._opener(request, timeout=timeout)
        except HTTPError as exc:
            try:
                raw = exc.read()
            except Exception:
                raw = b""
            raise OllamaHTTPError(
                exc.code,
                raw.decode("utf-8", errors="replace"),
            ) from exc
        except URLError as exc:
            raise OllamaConnectionError(f"Cannot connect to Ollama: {exc}") from exc
        except (socket.timeout, TimeoutError) as exc:
            raise OllamaConnectionError(str(exc) or "Ollama request timed out") from exc
        except Exception as exc:
            raise OllamaConnectionError(str(exc)) from exc

    @staticmethod
    def _close(response: Any) -> None:
        try:
            response.close()
        except Exception:
            pass

    def list_models(self) -> dict[str, Any]:
        request = Request(self.host + "/api/tags", method="GET")
        response = self._open(request, self.connect_timeout)
        try:
            return self._decode_json(response.read())
        finally:
            self._close(response)

    def chat(self, body: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            self.chat_url,
            data=json_bytes(self._request_body(body)),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        response = self._open(request, self.read_timeout)
        try:
            return self._decode_json(response.read())
        finally:
            self._close(response)

    @contextmanager
    def stream_chat(
        self,
        body: dict[str, Any],
        *,
        skip_invalid: bool = False,
        use_idle_timeout: bool = True,
    ) -> Iterator[Iterator[dict[str, Any]]]:
        request = Request(
            self.chat_url,
            data=json_bytes(self._request_body(body)),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/x-ndjson",
            },
            method="POST",
        )
        response = self._open(request, self.read_timeout)
        if use_idle_timeout:
            self._set_stream_timeout(response)
        iterator = self._iter_ndjson(response, skip_invalid=skip_invalid)
        try:
            yield iterator
        finally:
            try:
                iterator.close()
            finally:
                self._close(response)

    @contextmanager
    def stream_chat_with_heartbeat(
        self,
        body: dict[str, Any],
        *,
        heartbeat_interval: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> Iterator[Iterator[dict[str, Any] | None]]:
        """Read Ollama in a worker and yield None at silent deadlines."""

        connection = self._heartbeat_connection_factory(
            self.chat_url,
            self.connect_timeout,
            self.read_timeout,
        )
        queue: Queue[tuple[str, Any]] = Queue()
        stopped = threading.Event()

        def read_items() -> None:
            response = None
            try:
                # Opening an Ollama stream can itself wait for model loading or
                # prompt evaluation. Keep it in the worker so the HTTP handler
                # can start Anthropic SSE and emit watchdog pings immediately.
                response = connection.open(
                    json_bytes(self._request_body(body))
                )
                if stopped.is_set():
                    return
                for item in self._iter_ndjson(response, skip_invalid=False):
                    if stopped.is_set():
                        break
                    queue.put(("item", item))
            except _StreamCancelled:
                pass
            except Exception as exc:
                if not stopped.is_set():
                    queue.put(("error", exc))
            finally:
                connection.close()
                queue.put(("done", None))

        reader = threading.Thread(
            target=read_items,
            name="ollama-stream-reader",
        )
        reader.start()

        def items() -> Iterator[dict[str, Any] | None]:
            next_heartbeat = clock() + heartbeat_interval
            while True:
                try:
                    remaining = max(0.0, next_heartbeat - clock())
                    kind, value = queue.get(timeout=remaining)
                except Empty:
                    now = clock()
                    if now < next_heartbeat:
                        continue
                    yield None
                    next_heartbeat = now + heartbeat_interval
                    continue
                if kind == "item":
                    next_heartbeat = clock() + heartbeat_interval
                    yield value
                elif kind == "error":
                    raise value
                else:
                    break

        iterator = items()
        try:
            yield iterator
        finally:
            stopped.set()
            connection.close()
            reader.join()

    def _set_stream_timeout(self, response: Any) -> None:
        sock = getattr(
            getattr(getattr(response, "fp", None), "raw", None),
            "_sock",
            None,
        )
        if sock is not None and self.stream_idle_timeout > 0:
            sock.settimeout(self.stream_idle_timeout)

    @staticmethod
    def _decode_json(raw: bytes) -> dict[str, Any]:
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise OllamaInvalidResponse(f"Invalid Ollama JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise OllamaInvalidResponse("Invalid Ollama JSON: expected an object")
        return data

    @staticmethod
    def _iter_ndjson(
        response: Any,
        *,
        skip_invalid: bool,
    ) -> Iterator[dict[str, Any]]:
        while True:
            line = response.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line.decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
            except Exception as exc:
                if skip_invalid:
                    continue
                raise OllamaInvalidResponse(
                    f"Invalid Ollama stream JSON: {exc}"
                ) from exc
            if data.get("error"):
                raise OllamaInvalidResponse(str(data["error"]))
            yield data
