"""A small Chrome DevTools Protocol client over a replaceable transport.

The transport is `send(text)` / `recv() -> text | None` / `close()`; the real one is a
`websockets` sync connection, tests plug in an in-memory fake. Replies are matched to their
request by id on a reader thread; protocol events are handed to one callback on that same
thread, so the callback must only enqueue.
"""

from __future__ import annotations

import itertools
import json
import threading
from collections.abc import Callable
from typing import Any, Protocol


class CdpError(Exception):
    """The browser answered a command with an error."""

    def __init__(self, method: str, message: str, code: int | None = None) -> None:
        super().__init__(f"{method}: {message}")
        self.method = method
        self.message = message
        self.code = code


class CdpClosed(Exception):
    """The connection to the browser is gone."""


class CdpTimeout(Exception):
    """The browser did not answer in time."""


class Transport(Protocol):
    def send(self, text: str) -> None: ...

    def recv(self) -> str | None:
        """Next message, or None once the connection is closed."""

    def close(self) -> None: ...


class WebSocketTransport:
    """`Transport` over a `websockets` sync client connection."""

    def __init__(self, url: str, *, open_timeout: float = 10.0) -> None:
        from websockets.sync.client import connect

        # Chrome's debugging endpoint sends no ping frames worth answering late; keep ours off
        # and let the OS notice a dead peer.
        self._connection = connect(url, max_size=None, open_timeout=open_timeout, compression=None, ping_interval=None)

    def send(self, text: str) -> None:
        from websockets.exceptions import ConnectionClosed

        try:
            self._connection.send(text)
        except ConnectionClosed as exc:
            raise CdpClosed(str(exc)) from exc

    def recv(self) -> str | None:
        from websockets.exceptions import ConnectionClosed

        try:
            message = self._connection.recv()
        except ConnectionClosed:
            return None
        except OSError:
            return None
        return message if isinstance(message, str) else message.decode("utf-8", errors="replace")

    def close(self) -> None:
        try:
            self._connection.close()
        except Exception:  # noqa: BLE001 - closing must never raise
            pass


EventHandler = Callable[[str, dict[str, Any], str | None], None]


class CdpClient:
    def __init__(self, transport: Transport, on_event: EventHandler, *, default_timeout: float = 15.0) -> None:
        self._transport = transport
        self._on_event = on_event
        self._default_timeout = default_timeout
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._pending: dict[int, _Pending] = {}
        self._closed = False
        self._reader = threading.Thread(target=self._read_loop, name="cdp-reader", daemon=True)
        self._reader.start()

    @property
    def closed(self) -> bool:
        return self._closed

    def call(self, method: str, params: dict[str, Any] | None = None, *, session_id: str | None = None, timeout: float | None = None) -> dict[str, Any]:
        pending = _Pending()
        request_id = next(self._ids)
        message: dict[str, Any] = {"id": request_id, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        with self._lock:
            if self._closed:
                raise CdpClosed("connection closed")
            self._pending[request_id] = pending
        try:
            self._transport.send(json.dumps(message))
        except Exception as exc:
            with self._lock:
                self._pending.pop(request_id, None)
            if isinstance(exc, CdpClosed):
                raise
            raise CdpClosed(str(exc)) from exc
        if not pending.event.wait(self._default_timeout if timeout is None else timeout):
            with self._lock:
                self._pending.pop(request_id, None)
            raise CdpTimeout(method)
        if pending.closed:
            raise CdpClosed("connection closed")
        if pending.error is not None:
            raise CdpError(method, str(pending.error.get("message", "error")), pending.error.get("code"))
        return pending.result or {}

    def close(self) -> None:
        self._fail_all()
        self._transport.close()

    def _fail_all(self) -> None:
        with self._lock:
            self._closed = True
            pending = list(self._pending.values())
            self._pending.clear()
        for item in pending:
            item.closed = True
            item.event.set()

    def _read_loop(self) -> None:
        try:
            while True:
                text = self._transport.recv()
                if text is None:
                    break
                try:
                    message = json.loads(text)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                if "id" in message:
                    with self._lock:
                        pending = self._pending.pop(message["id"], None)
                    if pending is not None:
                        pending.result = message.get("result")
                        pending.error = message.get("error")
                        pending.event.set()
                elif "method" in message:
                    params = message.get("params")
                    try:
                        self._on_event(str(message["method"]), params if isinstance(params, dict) else {}, message.get("sessionId"))
                    except Exception:  # noqa: BLE001 - a bad handler must not kill the reader
                        pass
        finally:
            self._fail_all()
            try:
                self._on_event("CdpClient.closed", {}, None)
            except Exception:  # noqa: BLE001
                pass


class _Pending:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: dict[str, Any] | None = None
        self.error: dict[str, Any] | None = None
        self.closed = False
