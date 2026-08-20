"""Ollama HTTP transport with consistent errors and response ownership."""

from contextlib import contextmanager
import json
import socket
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError
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


class OllamaClient:
    def __init__(
        self,
        host: str,
        *,
        connect_timeout: float,
        read_timeout: float,
        stream_idle_timeout: float,
        keep_alive: str,
        think: bool,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.host = host.rstrip("/")
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.stream_idle_timeout = stream_idle_timeout
        self.keep_alive = keep_alive
        self.think = think
        self._opener = opener

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
