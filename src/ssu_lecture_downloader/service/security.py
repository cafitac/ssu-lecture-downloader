"""Request-level protection for the local API.

The backend binds to loopback but still holds live LMS cookies and can write files,
so any web page the user visits must not be able to drive it: browsers are limited
by an exact CORS allow-list, DNS-rebinding style requests are stopped by a Host
check, and a launcher-provided token (`SSU_API_TOKEN`) gates every `/api/*` route
except the health probe.
"""

from __future__ import annotations

import hmac
import json
import os
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

API_TOKEN_ENV = "SSU_API_TOKEN"
API_TOKEN_HEADER = "X-SSU-Token"

ALLOWED_CORS_ORIGINS = [
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
]

ALLOWED_HOSTNAMES = frozenset({"127.0.0.1", "localhost"})
TOKEN_EXEMPT = frozenset({("GET", "/api/health")})


def is_allowed_host(host_header: str | None) -> bool:
    """True for 127.0.0.1 / localhost, with or without a numeric port."""
    if not host_header:
        return False
    hostname, separator, port = host_header.strip().lower().partition(":")
    if hostname not in ALLOWED_HOSTNAMES:
        return False
    return not separator or (port.isdigit() and 0 < int(port) < 65536)


def configured_api_token() -> str | None:
    token = os.environ.get(API_TOKEN_ENV, "").strip()
    return token or None


def _json_response_parts(status: int, error_code: str, message: str) -> tuple[list[tuple[bytes, bytes]], bytes, int]:
    body = json.dumps({"ok": False, "error_code": error_code, "message": message}, ensure_ascii=False).encode("utf-8")
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii"))]
    return headers, body, status


async def _send_json(send: Any, status: int, error_code: str, message: str) -> None:
    headers, body, status = _json_response_parts(status, error_code, message)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class LoopbackHostMiddleware:
    """Reject requests whose Host header is not 127.0.0.1[:port] or localhost[:port]."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] in {"http", "websocket"}:
            headers = dict(scope.get("headers") or [])
            host = headers.get(b"host", b"").decode("latin-1")
            if not is_allowed_host(host):
                if scope["type"] == "http":
                    await _send_json(send, 400, "invalid_host", "Host header is not a loopback address.")
                else:
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)


class ApiTokenMiddleware:
    """Require `X-SSU-Token` on /api/* when a token is configured (CORS preflights pass)."""

    def __init__(self, app: Any, *, token: str | None) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if self.token and scope["type"] == "http" and self._requires_token(scope):
            headers = dict(scope.get("headers") or [])
            supplied = headers.get(API_TOKEN_HEADER.lower().encode("ascii"), b"").decode("latin-1")
            if not hmac.compare_digest(supplied.encode("utf-8"), self.token.encode("utf-8")):
                await _send_json(send, 401, "api_token_invalid", "Missing or invalid API token.")
                return
        await self.app(scope, receive, send)

    @staticmethod
    def _requires_token(scope: dict[str, Any]) -> bool:
        path = scope.get("path", "")
        if not (path == "/api" or path.startswith("/api/")):
            return False
        method = scope.get("method", "GET").upper()
        if method == "OPTIONS":
            return False  # browsers send preflights without custom headers
        return (method, path.rstrip("/") or "/") not in TOKEN_EXEMPT


class MediaPathPolicy:
    """Which .mp4 files `ensure-playable` may touch.

    Allowed: files below a download root (the default one or one the user chose in a
    request), and files the app produced itself. Roots too broad to be a download
    folder (`/`, the home directory) are never registered.
    """

    def __init__(self, default_root: Path) -> None:
        self._lock = threading.Lock()
        self._roots: list[Path] = []
        self._produced: set[Path] = set()
        self.add_root(default_root)

    @staticmethod
    def _resolve(path: Path) -> Path:
        return Path(os.path.realpath(path.expanduser()))

    def add_root(self, root: Path | str | None) -> None:
        if root is None:
            return
        resolved = self._resolve(Path(root))
        if resolved == Path(resolved.anchor) or resolved == self._resolve(Path.home()):
            return
        with self._lock:
            if resolved not in self._roots:
                self._roots.append(resolved)

    def add_produced(self, paths: Iterable[Path | str | None]) -> None:
        with self._lock:
            for path in paths:
                if path:
                    self._produced.add(self._resolve(Path(path)))

    def is_allowed(self, path: Path) -> bool:
        if path.suffix.lower() != ".mp4":
            return False
        resolved = self._resolve(path)
        if resolved.suffix.lower() != ".mp4":
            return False
        with self._lock:
            if resolved in self._produced:
                return True
            return any(resolved == root or root in resolved.parents for root in self._roots)
