"""Remote subtitle (STT) servers: providers, connection service and the transcription pipeline.

Two server kinds are supported behind one `transcribe(wav) -> cues` interface:

* `whisper_cpp_server`: whisper.cpp `whisper-server`, `POST {base}/inference`.
* `openai_compatible`: `POST {base}/v1/audio/transcriptions` (OpenAI and look-alikes).

The pipeline converts the audio to a 16 kHz mono WAV, transcribes it, re-transcribes
windows where Whisper looped (see `stt_cues`), cleans the cues and writes the same
transcript files as the local engine. Nothing here talks to a server unless the user
enabled and configured one.
"""

from __future__ import annotations

import http.client
import json
import socket
import subprocess
import tempfile
import threading
import time
import wave
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from contextlib import contextmanager, nullcontext
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from ssu_lecture_downloader.core import postprocess_media, stt_cues
from ssu_lecture_downloader.core.postprocess_media import MediaCancelled
from ssu_lecture_downloader.core.progress import emit_progress
from ssu_lecture_downloader.core.ssh_tunnel import SshTunnel, TunnelError
from ssu_lecture_downloader.core.errors import LibraryConflict, PathTooLong
from ssu_lecture_downloader.core.stt import MEDIA, TranscriptionCancelled, TranscriptionRequest, TranscriptionResult, WorkGates, write_transcript_files
from ssu_lecture_downloader.core.stt_cues import TranscriptSegment
from ssu_lecture_downloader.core.stt_remote_config import (
    DEFAULT_OPENAI_MODEL,
    ConfigError,
    RemoteSTTConfig,
    RemoteSTTSettingsStore,
    apply_patch,
)

# Response budget of ONE request when nothing is ahead of it on the server (also the legacy idle timeout).
REQUEST_TIMEOUT_SECONDS = 20 * 60
# Every further request of this app already sent to the same server adds one more budget, up to this cap.
RESPONSE_BUDGET_CAP_SECONDS = 4 * 60 * 60
# Connect and upload: a socket that stays silent this long is dead (the server has not even read the body).
IDLE_TIMEOUT_SECONDS = 60
# A server that rejects concurrent requests (HTTP 429/503) gets the request again after these waits.
BACKOFF_STATUSES = frozenset({429, 503})
BACKOFF_SECONDS = (5.0, 10.0, 20.0, 30.0)
PROBE_TIMEOUT_SECONDS = 5.0
CANCEL_POLL_SECONDS = 0.3
UPLOAD_CHUNK_BYTES = 1024 * 1024
REPAIR_TEMPERATURES = (0.2, 0.4)
# Share of the progress bar: upload + first transcription, then repairs up to REPAIR_END.
PROGRESS_FIRST_PASS_DONE = 70
PROGRESS_REPAIR_END = 95
FORMAT_REJECTED_STATUSES = frozenset({400, 415, 422})


class RemoteSTTError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ------------------------------------------------------------------------ HTTP


class MultipartBody:
    """multipart/form-data with one streamed file part, so a long WAV never sits in memory twice."""

    def __init__(self, fields: dict[str, str], file_field: str, file_path: Path) -> None:
        self.boundary = f'----ssu{uuid4().hex}'
        self.content_type = f'multipart/form-data; boundary={self.boundary}'
        head = ''.join(
            f'--{self.boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n' for name, value in fields.items()
        )
        head += (
            f'--{self.boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"\r\n'
            'Content-Type: audio/wav\r\n\r\n'
        )
        self._head = head.encode('utf-8')
        self._tail = f'\r\n--{self.boundary}--\r\n'.encode('ascii')
        self._file_path = file_path
        self.length = len(self._head) + file_path.stat().st_size + len(self._tail)

    def chunks(self) -> Iterator[bytes]:
        yield self._head
        with self._file_path.open('rb') as handle:
            while chunk := handle.read(UPLOAD_CHUNK_BYTES):
                yield chunk
        yield self._tail


def http_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: MultipartBody | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    response_timeout: float | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[int, bytes]:
    """One HTTP request with no redirects and no proxies, abortable through `should_cancel`.

    `timeout` is the idle timeout of connect and upload. Once the whole request is sent the
    socket waits for the response up to `response_timeout` (default: `timeout`), which is how
    a request queued behind others on a one-at-a-time server is allowed to wait.
    """
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise RemoteSTTError('invalid_stt_base_url', '서버 주소가 올바르지 않아요.')
    connection_class = http.client.HTTPSConnection if parts.scheme == 'https' else http.client.HTTPConnection
    connection = connection_class(parts.hostname, parts.port, timeout=timeout)
    target = (parts.path or '/') + (f'?{parts.query}' if parts.query else '')
    outcome: dict[str, Any] = {}
    started = time.monotonic()

    def run() -> None:
        try:
            connection.putrequest(method, target, skip_accept_encoding=True)
            for name, value in (headers or {}).items():
                connection.putheader(name, value)
            if body is not None:
                connection.putheader('Content-Type', body.content_type)
                connection.putheader('Content-Length', str(body.length))
            connection.endheaders()
            if body is not None:
                for chunk in body.chunks():
                    connection.send(chunk)
            if response_timeout is not None and connection.sock is not None:
                connection.sock.settimeout(response_timeout)
            response = connection.getresponse()
            outcome['value'] = (response.status, response.read())
        except BaseException as exc:  # noqa: BLE001 - re-raised on the calling thread
            outcome['error'] = exc
        finally:
            connection.close()

    worker = threading.Thread(target=run, name='ssu-stt-request', daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(CANCEL_POLL_SECONDS)
        if should_cancel is not None and should_cancel():
            sock = connection.sock
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            connection.close()
            worker.join(2)
            raise TranscriptionCancelled('transcription cancelled')
    error = outcome.get('error')
    if error is not None:
        if isinstance(error, (TimeoutError, socket.timeout)):
            waited = round(time.monotonic() - started)
            raise RemoteSTTError('stt_remote_timeout', f'자막 서버가 시간 안에 응답하지 않았어요 ({waited}초 기다렸어요).') from error
        if isinstance(error, (OSError, http.client.HTTPException)):
            raise RemoteSTTError('stt_remote_unreachable', '자막 서버에 연결하지 못했어요. 서버와 SSH 터널을 확인해 주세요.') from error
        raise error
    return outcome['value']


# -------------------------------------------------------------------- providers


def response_budget_seconds(ahead_requests: int, *, base: float = REQUEST_TIMEOUT_SECONDS, cap: float = RESPONSE_BUDGET_CAP_SECONDS) -> float:
    """How long one request may wait for its response: `base` per request in front of it plus its own, capped."""
    return min(base * (1 + max(0, ahead_requests)), max(cap, base))


def _wait_cancellable(seconds: float, should_cancel: Callable[[], bool] | None) -> None:
    deadline = time.monotonic() + seconds
    while True:
        if should_cancel is not None and should_cancel():
            raise TranscriptionCancelled('transcription cancelled')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(CANCEL_POLL_SECONDS, remaining))


def post_audio(
    url: str,
    *,
    headers: dict[str, str] | None,
    fields: dict[str, str],
    wav_path: Path,
    idle_timeout: float,
    response_budget: float,
    backoff_seconds: Sequence[float],
    should_cancel: Callable[[], bool] | None,
    on_event: Callable[[dict[str, Any]], None] | None,
) -> tuple[int, bytes]:
    """POST the audio and wait up to `response_budget` for the answer (a queue-aware wait, not an idle one).

    A server that answers 429/503 instead of queueing is asked again after a short wait (the
    wait is cancel-aware and counts against the same budget); the last answer is returned when
    the budget cannot cover another wait.
    """
    deadline = time.monotonic() + response_budget
    attempt = 0
    while True:
        remaining = max(deadline - time.monotonic(), 1.0)
        status, body = http_request('POST', url, headers=headers, body=MultipartBody(fields, 'file', wav_path), timeout=idle_timeout, response_timeout=remaining, should_cancel=should_cancel)
        if status not in BACKOFF_STATUSES or not backoff_seconds:
            return status, body
        delay = backoff_seconds[min(attempt, len(backoff_seconds) - 1)]
        if time.monotonic() + delay >= deadline:
            return status, body
        if on_event is not None:
            on_event({'reason': 'server_deferred', 'retry_after_seconds': delay})
        _wait_cancellable(delay, should_cancel)
        attempt += 1


class RemoteProvider(Protocol):
    kind: str

    def transcribe(
        self,
        wav_path: Path,
        *,
        temperature: float = 0.0,
        should_cancel: Callable[[], bool] | None = None,
        ahead_requests: int = 0,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[TranscriptSegment]: ...

    def probe(self) -> None: ...


def _decode(body: bytes) -> str:
    return body.decode('utf-8', 'replace')


def _error_text(status: int, body: bytes) -> str:
    detail = ''
    try:
        payload = json.loads(_decode(body))
        error = payload.get('error') if isinstance(payload, dict) else None
        detail = str(error.get('message') if isinstance(error, dict) else error or '')
    except ValueError:
        detail = _decode(body).strip()
    return f'HTTP {status}' + (f': {detail[:200]}' if detail else '')


class WhisperCppServerProvider:
    kind = 'whisper_cpp_server'

    def __init__(
        self,
        base_url: str,
        *,
        language: str = 'ko',
        timeout: float | None = None,
        idle_timeout: float | None = None,
        budget_cap: float | None = None,
        backoff_seconds: Sequence[float] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip('/')
        self.language = language
        # `timeout` is the response budget of one request; each request ahead of it adds one more (see post_audio).
        self.timeout = REQUEST_TIMEOUT_SECONDS if timeout is None else timeout
        self.idle_timeout = min(IDLE_TIMEOUT_SECONDS if idle_timeout is None else idle_timeout, self.timeout)
        self.budget_cap = RESPONSE_BUDGET_CAP_SECONDS if budget_cap is None else budget_cap
        self.backoff_seconds = BACKOFF_SECONDS if backoff_seconds is None else tuple(backoff_seconds)

    def transcribe(
        self,
        wav_path: Path,
        *,
        temperature: float = 0.0,
        should_cancel: Callable[[], bool] | None = None,
        ahead_requests: int = 0,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[TranscriptSegment]:
        fields = {'temperature': str(temperature), 'response_format': 'srt', 'language': self.language}
        status, body = post_audio(
            f'{self.base_url}/inference',
            headers=None,
            fields=fields,
            wav_path=wav_path,
            idle_timeout=self.idle_timeout,
            response_budget=response_budget_seconds(ahead_requests, base=self.timeout, cap=self.budget_cap),
            backoff_seconds=self.backoff_seconds,
            should_cancel=should_cancel,
            on_event=on_event,
        )
        if status != 200:
            raise RemoteSTTError('stt_remote_http_error', f'자막 서버가 오류를 돌려줬어요 ({_error_text(status, body)}).')
        text = _decode(body)
        if text.lstrip().startswith('{'):
            # whisper-server answers 200 with a JSON {"error": ...} for a request it could not run.
            try:
                payload = json.loads(text)
            except ValueError as exc:
                raise RemoteSTTError('stt_remote_bad_response', '자막 서버의 응답을 읽지 못했어요.') from exc
            if isinstance(payload, dict) and payload.get('error'):
                raise RemoteSTTError('stt_remote_http_error', f'자막 서버가 오류를 돌려줬어요 ({str(payload["error"])[:200]}).')
            return stt_cues.segments_from_verbose_json(payload)
        return stt_cues.parse_srt(text)

    def probe(self) -> None:
        status, _body = http_request('GET', f'{self.base_url}/', timeout=PROBE_TIMEOUT_SECONDS)
        if status != 200:
            raise RemoteSTTError('stt_remote_http_error', f'자막 서버가 정상 응답을 주지 않았어요 (HTTP {status}).')


class OpenAICompatibleProvider:
    kind = 'openai_compatible'

    def __init__(
        self,
        base_url: str,
        *,
        model: str = DEFAULT_OPENAI_MODEL,
        api_key: str = '',
        language: str = 'ko',
        timeout: float | None = None,
        idle_timeout: float | None = None,
        budget_cap: float | None = None,
        backoff_seconds: Sequence[float] | None = None,
    ) -> None:
        root = base_url.rstrip('/')
        # Users often paste ".../v1"; the paths below add it themselves.
        self.root = root[: -len('/v1')] if root.endswith('/v1') else root
        self.model = model or DEFAULT_OPENAI_MODEL
        self.api_key = api_key
        self.language = language
        self.timeout = REQUEST_TIMEOUT_SECONDS if timeout is None else timeout
        self.idle_timeout = min(IDLE_TIMEOUT_SECONDS if idle_timeout is None else idle_timeout, self.timeout)
        self.budget_cap = RESPONSE_BUDGET_CAP_SECONDS if budget_cap is None else budget_cap
        self.backoff_seconds = BACKOFF_SECONDS if backoff_seconds is None else tuple(backoff_seconds)
        self._response_format = 'srt'

    def _headers(self) -> dict[str, str]:
        return {'Authorization': f'Bearer {self.api_key}'} if self.api_key else {}

    def transcribe(
        self,
        wav_path: Path,
        *,
        temperature: float = 0.0,
        should_cancel: Callable[[], bool] | None = None,
        ahead_requests: int = 0,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[TranscriptSegment]:
        url = f'{self.root}/v1/audio/transcriptions'
        for _attempt in range(2):
            fields = {'model': self.model, 'language': self.language, 'response_format': self._response_format, 'temperature': str(temperature)}
            status, body = post_audio(
                url,
                headers=self._headers(),
                fields=fields,
                wav_path=wav_path,
                idle_timeout=self.idle_timeout,
                response_budget=response_budget_seconds(ahead_requests, base=self.timeout, cap=self.budget_cap),
                backoff_seconds=self.backoff_seconds,
                should_cancel=should_cancel,
                on_event=on_event,
            )
            if status in (401, 403):
                raise RemoteSTTError('stt_remote_auth_failed', 'API 키가 올바르지 않거나 권한이 없어요.')
            if status in FORMAT_REJECTED_STATUSES and self._response_format == 'srt':
                # Some servers only know json formats; remember that and retry once.
                self._response_format = 'verbose_json'
                continue
            if status != 200:
                raise RemoteSTTError('stt_remote_http_error', f'자막 서버가 오류를 돌려줬어요 ({_error_text(status, body)}).')
            text = _decode(body)
            if text.lstrip().startswith('{'):
                try:
                    return stt_cues.segments_from_verbose_json(json.loads(text))
                except ValueError as exc:
                    raise RemoteSTTError('stt_remote_bad_response', '자막 서버의 응답을 읽지 못했어요.') from exc
            return stt_cues.parse_srt(text)
        raise RemoteSTTError('stt_remote_http_error', '자막 서버가 요청한 형식을 지원하지 않아요.')

    def probe(self) -> None:
        status, _body = http_request('GET', f'{self.root}/v1/models', headers=self._headers(), timeout=PROBE_TIMEOUT_SECONDS)
        if status in (401, 403):
            raise RemoteSTTError('stt_remote_auth_failed', 'API 키가 올바르지 않거나 권한이 없어요.')
        if 200 <= status < 300:
            return
        if status in (404, 405):
            # No model listing: any answer from the server still proves it is reachable.
            head_status, _ = http_request('HEAD', f'{self.root}/', headers=self._headers(), timeout=PROBE_TIMEOUT_SECONDS)
            if head_status < 500:
                return
        raise RemoteSTTError('stt_remote_http_error', f'자막 서버가 정상 응답을 주지 않았어요 (HTTP {status}).')


# ---------------------------------------------------------------------- service


class RemoteSTTService:
    """Settings + managed SSH tunnel + provider construction for the one configured remote server."""

    def __init__(self, store: RemoteSTTSettingsStore, tunnel: SshTunnel | None = None) -> None:
        self.store = store
        self.tunnel = tunnel or SshTunnel()
        self._lock = threading.RLock()
        self._in_flight = 0
        self._in_flight_lock = threading.Lock()
        self._last_check: dict[str, Any] = {'reachable': None, 'last_error': None, 'error_code': None, 'checked_at': None}

    # queue awareness --------------------------------------------------------

    @property
    def in_flight(self) -> int:
        with self._in_flight_lock:
            return self._in_flight

    @contextmanager
    def request_slot(self) -> Iterator[int]:
        """Count one request of this process on the server; yields how many were already in flight (ahead of it)."""
        with self._in_flight_lock:
            ahead = self._in_flight
            self._in_flight += 1
        try:
            yield ahead
        finally:
            with self._in_flight_lock:
                self._in_flight -= 1

    # settings -------------------------------------------------------------

    def config(self) -> RemoteSTTConfig:
        return self.store.load()

    def is_enabled(self) -> bool:
        config = self.config()
        return config.enabled and bool(config.tunnel or config.base_url)

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = self.config()
            updated = apply_patch(current, patch)
            self.store.save(updated)
            if updated.model_dump(exclude={'enabled'}) != current.model_dump(exclude={'enabled'}):
                self.tunnel.close()
                self._last_check = {'reachable': None, 'last_error': None, 'error_code': None, 'checked_at': None}
            return self.status()

    # connection -----------------------------------------------------------

    def _base_url(self, config: RemoteSTTConfig) -> str:
        if config.tunnel:
            port = self.tunnel.ensure(config.tunnel.host, config.tunnel.remote_port, config.tunnel.local_port)
            return f'http://127.0.0.1:{port}'
        if not config.base_url:
            raise RemoteSTTError('stt_remote_not_configured', '원격 자막 서버 주소를 먼저 설정해 주세요.')
        return config.base_url

    def provider(self, *, language: str = 'ko') -> RemoteProvider:
        """The provider for the saved settings, opening the SSH tunnel first when one is configured."""
        config = self.config()
        base_url = self._base_url(config)
        if config.kind == 'openai_compatible':
            return OpenAICompatibleProvider(base_url, model=config.model, api_key=config.api_key, language=language)
        return WhisperCppServerProvider(base_url, language=language)

    def check(self) -> dict[str, Any]:
        """Open the tunnel if needed, probe the server and remember the outcome."""
        config = self.config()
        result: dict[str, Any]
        try:
            if not (config.tunnel or config.base_url):
                raise RemoteSTTError('stt_remote_not_configured', '원격 자막 서버 주소를 먼저 설정해 주세요.')
            self.provider().probe()
            result = {'reachable': True, 'last_error': None, 'error_code': None}
        except (TunnelError, RemoteSTTError) as exc:
            result = {'reachable': False, 'last_error': exc.message, 'error_code': exc.code}
        self._last_check = {**result, 'checked_at': time.time()}
        return self.status()

    def record_failure(self, exc: TunnelError | RemoteSTTError) -> None:
        self._last_check = {'reachable': False, 'last_error': exc.message, 'error_code': exc.code, 'checked_at': time.time()}

    def status(self) -> dict[str, Any]:
        config = self.config()
        payload = config.public_view()
        tunnel_port = self.tunnel.active_port()
        payload['tunnel_state'] = {'running': tunnel_port is not None, 'local_port': tunnel_port} if config.tunnel else None
        payload['effective_base_url'] = (
            f'http://127.0.0.1:{config.tunnel.local_port or tunnel_port}' if config.tunnel and (config.tunnel.local_port or tunnel_port) else config.base_url or None
        )
        payload.update(self._last_check)
        return payload

    def provider_status(self) -> dict[str, Any]:
        """Shape of `STTProviderStatus` so the generic provider list can include the remote engine."""
        status = self.status()
        enabled = self.is_enabled()
        return {
            'provider': 'remote',
            'installed': enabled,
            'available': enabled and status['reachable'] is True,
            'install_hint': status['last_error'] if enabled else '설정에서 원격 자막 서버를 켜 주세요.',
            'recommended_model': status['model'] or None,
            'quality_note': '원격 서버로 음성을 보내 자막을 만들어요.',
            'platform_supported': True,
        }

    def close(self) -> None:
        self.tunnel.close()


# --------------------------------------------------------------------- pipeline


def wav_duration_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), 'rb') as handle:
            return handle.getnframes() / float(handle.getframerate() or 1)
    except (wave.Error, EOFError, OSError):
        return 0.0


Retranscribe = Callable[[float, float, float], list[TranscriptSegment]]


def repair_repeated_cues(
    cues: list[TranscriptSegment],
    retranscribe: Retranscribe,
    *,
    duration: float | None = None,
    pad: float = stt_cues.REPAIR_PAD_SECONDS,
    temperatures: Sequence[float] = REPAIR_TEMPERATURES,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    event_context: dict[str, Any] | None = None,
) -> tuple[list[TranscriptSegment], list[str]]:
    """Re-transcribe windows around looped cues until none is left or `temperatures` run out.

    `retranscribe(start, end, temperature)` returns cues relative to `start`. A window the
    server fails on keeps its original cues and adds a warning; cancellation propagates.
    """
    warnings: list[str] = []
    for attempt, temperature in enumerate(temperatures):
        runs = stt_cues.find_repeat_runs(cues)
        if not runs:
            break
        windows = stt_cues.repair_windows(cues, runs, pad=pad, duration=duration)
        for index, (start, end) in enumerate(windows, start=1):
            if should_cancel and should_cancel():
                raise TranscriptionCancelled('transcription cancelled')
            span = PROGRESS_REPAIR_END - PROGRESS_FIRST_PASS_DONE
            percent = PROGRESS_FIRST_PASS_DONE + round(span * ((attempt + (index - 1) / len(windows)) / len(temperatures)))
            emit_progress(
                on_progress,
                {
                    **(event_context or {}),
                    'stage': 'transcript_repair',
                    'repair_index': index,
                    'repair_total': len(windows),
                    'repair_pass': attempt + 1,
                    'window_start': round(start, 1),
                    'window_end': round(end, 1),
                    'progress_percent': percent,
                },
            )
            try:
                replacement = retranscribe(start, end, temperature)
            except RemoteSTTError as exc:
                warnings.append(f'반복 구간 {stt_cues.format_clock(start)}–{stt_cues.format_clock(end)}을 다시 인식하지 못했어요: {exc.message}')
                continue
            cues = stt_cues.replace_window(cues, start, end, replacement)
    return cues, warnings


def _model_label(config: RemoteSTTConfig) -> str:
    return config.model or ('whisper.cpp' if config.kind == 'whisper_cpp_server' else DEFAULT_OPENAI_MODEL)


def transcribe_with_remote(
    request: TranscriptionRequest,
    service: RemoteSTTService,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    gates: WorkGates | None = None,
) -> TranscriptionResult:
    audio_path = request.audio_path.expanduser()
    config = service.config()
    model = _model_label(config)
    base_event = {'provider': 'remote', 'model': model, 'audio_path': str(audio_path), 'remote': True}

    def failure(code: str, message: str) -> TranscriptionResult:
        return TranscriptionResult(ok=False, audio_path=audio_path, error_code=code, message=message[:500])

    def check_cancel() -> None:
        if should_cancel and should_cancel():
            raise TranscriptionCancelled('transcription cancelled')

    def media_slot() -> Any:
        """ffmpeg-class work (WAV conversion, repair cuts) shares the process-wide media limit; the HTTP calls never hold it."""
        if gates is None:
            return nullcontext()
        # This wait happens inside the transcript stage, so it is a transcript wait (never `audio_waiting`).
        return gates.hold(MEDIA, should_cancel=should_cancel, on_wait=lambda: emit_progress(on_progress, {**base_event, 'stage': 'transcript_waiting', 'reason': 'media'}))

    def send(path: Path, temperature: float) -> list[TranscriptSegment]:
        """One request. The app does not queue remote work; it only tells the user how many of its own requests are ahead."""
        with service.request_slot() as ahead:
            if ahead > 0:
                emit_progress(on_progress, {**base_event, 'stage': 'transcript_waiting', 'reason': 'server_busy', 'ahead_requests': ahead})
            return provider.transcribe(
                path,
                temperature=temperature,
                should_cancel=should_cancel,
                ahead_requests=ahead,
                on_event=lambda event: emit_progress(on_progress, {**base_event, 'stage': 'transcript_waiting', 'ahead_requests': ahead, **event}),
            )

    try:
        emit_progress(on_progress, {**base_event, 'stage': 'transcript_connecting', 'progress_percent': 2})
        provider = service.provider(language=request.language)
        check_cancel()
        with tempfile.TemporaryDirectory(prefix='ssu-stt-') as scratch:
            wav = Path(scratch) / 'audio.wav'
            with media_slot():
                postprocess_media.convert_to_stt_wav(audio_path, wav, should_cancel)
            duration = wav_duration_seconds(wav)
            emit_progress(on_progress, {**base_event, 'stage': 'transcript_started', 'duration': duration, 'progress_percent': 5})
            check_cancel()
            cues = send(wav, 0.0)
            emit_progress(on_progress, {**base_event, 'stage': 'transcript_progress', 'segment_count': len(cues), 'duration': duration, 'progress_percent': PROGRESS_FIRST_PASS_DONE})

            def retranscribe(start: float, end: float, temperature: float) -> list[TranscriptSegment]:
                piece = Path(scratch) / f'repair-{int(start * 1000)}-{int(end * 1000)}.wav'
                with media_slot():
                    postprocess_media.cut_audio_segment(wav, piece, start, end, should_cancel)
                try:
                    return send(piece, temperature)
                finally:
                    piece.unlink(missing_ok=True)

            cues, warnings = repair_repeated_cues(
                cues, retranscribe, duration=duration or None, on_progress=on_progress, should_cancel=should_cancel, event_context=base_event
            )
        cues = stt_cues.clean_cues(cues)
        if not cues:
            return failure('stt_empty_result', '자막 서버가 아무 내용도 돌려주지 않았어요.')
        longest = stt_cues.max_repeat_run(cues)
        if longest > 1:  # pragma: no cover - clean_cues guarantees 1
            warnings.append(f'같은 문장이 연속으로 {longest}번 남아 있어요.')
        for gap_start, gap_end in stt_cues.find_gaps(cues, duration=duration or None):
            warnings.append(f'자막이 {stt_cues.format_clock(gap_start)}–{stt_cues.format_clock(gap_end)} ({round(gap_end - gap_start)}초) 동안 비어 있어요.')
        emit_progress(on_progress, {**base_event, 'stage': 'transcript_progress', 'segment_count': len(cues), 'duration': duration, 'progress_percent': 99, 'warning_count': len(warnings)})
    except (TranscriptionCancelled, MediaCancelled):
        return failure('cancelled', '작업이 취소되었습니다.')
    except (TunnelError, RemoteSTTError) as exc:
        service.record_failure(exc)
        return failure(exc.code, exc.message)
    except ConfigError as exc:
        return failure(exc.code, '원격 자막 서버 설정이 올바르지 않아요.')
    except FileNotFoundError:
        return failure('ffmpeg_not_found', 'ffmpeg를 찾을 수 없습니다.')
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or '').strip().splitlines()[-1:] or ['ffmpeg 실행에 실패했습니다.']
        return failure('ffmpeg_failed', detail[0])
    except Exception as exc:  # noqa: BLE001 - surfaced as a stable error code
        return failure(type(exc).__name__, str(exc))

    try:
        result = write_transcript_files(
            audio_path=audio_path,
            segments=cues,
            output_dir=request.output_dir,
            provider=f'remote:{config.kind}',
            model=model,
            language=request.language,
            overwrite=request.overwrite,
            paths=request.paths,
        )
    except OSError as exc:
        # A taken lecture folder / path budget has its own stable code; any other write problem stays `transcript_write_failed`.
        code = exc.error_code if isinstance(exc, (LibraryConflict, PathTooLong)) else 'transcript_write_failed'
        return TranscriptionResult(ok=False, audio_path=audio_path, segments=cues, error_code=code, message=str(exc)[:500])
    result.warnings = warnings
    return result


__all__ = [
    'OpenAICompatibleProvider',
    'RemoteSTTError',
    'RemoteSTTService',
    'WhisperCppServerProvider',
    'http_request',
    'repair_repeated_cues',
    'transcribe_with_remote',
]
