"""A fake whisper.cpp / OpenAI-style STT HTTP server for tests (never a real server)."""

from __future__ import annotations

import json
import re
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class FakeSttCapability:
    """An SttCapability (the platform's local-engine answer) with a fixed value."""

    def __init__(self, supported: bool) -> None:
        self.supported = supported

    def local_engine_supported(self) -> bool:
        return self.supported


SUPPORTS_LOCAL_STT = FakeSttCapability(True)


@dataclass
class RecordedRequest:
    method: str
    path: str
    headers: dict[str, str]
    fields: dict[str, str] = field(default_factory=dict)
    file_size: int = 0


Responder = Callable[[RecordedRequest], tuple[int, str, str]]


def parse_multipart(body: bytes, content_type: str) -> tuple[dict[str, str], int]:
    boundary = content_type.split('boundary=', 1)[1].encode()
    fields: dict[str, str] = {}
    file_size = 0
    for part in body.split(b'--' + boundary):
        header, _, payload = part.partition(b'\r\n\r\n')
        name = re.search(rb'name="([^"]+)"', header)
        if not name:
            continue
        payload = payload[:-2] if payload.endswith(b'\r\n') else payload
        if b'filename=' in header:
            file_size = len(payload)
        else:
            fields[name.group(1).decode()] = payload.decode()
    return fields, file_size


class FakeSttServer:
    def __init__(self, responder: Responder) -> None:
        self.requests: list[RecordedRequest] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length) if length else b''
                fields, size = ({}, 0)
                if body and 'multipart' in (self.headers.get('Content-Type') or ''):
                    fields, size = parse_multipart(body, self.headers['Content-Type'])
                request = RecordedRequest(self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, fields, size)
                outer.requests.append(request)
                status, content_type, text = responder(request)
                payload = text.encode()
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(payload)

            do_GET = do_POST = do_HEAD = _handle

            def log_message(self, *args) -> None:  # noqa: D401 - keep test output quiet
                pass

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self.url = f'http://127.0.0.1:{self.port}'
        self._thread = threading.Thread(target=lambda: self._server.serve_forever(poll_interval=0.01), daemon=True)

    def __enter__(self) -> FakeSttServer:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()


def srt(*cues: tuple[float, float, str]) -> str:
    def stamp(seconds: float) -> str:
        ms = round(seconds * 1000)
        return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'

    return '\n'.join(f'{i}\n{stamp(a)} --> {stamp(b)}\n{text}\n' for i, (a, b, text) in enumerate(cues, start=1))


def write_silent_wav(path: Path, seconds: float, rate: int = 100) -> None:
    """A tiny valid WAV of `seconds` (a low sample rate keeps it a few KB)."""
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b'\x00\x00' * int(seconds * rate))


def sleepy(seconds: float, inner: Responder) -> Responder:
    def respond(request: RecordedRequest) -> tuple[int, str, str]:
        time.sleep(seconds)
        return inner(request)

    return respond


def json_text(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False)
