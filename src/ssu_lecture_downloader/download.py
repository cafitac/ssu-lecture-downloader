from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ssu_lecture_downloader.errors import response_status
from ssu_lecture_downloader.progress import emit_progress


class DownloadCancelled(Exception):
    """Raised when a caller requests cancellation during byte transfer."""


class IncompleteDownload(OSError):
    """Raised when the transferred byte count disagrees with the announced length."""


class DownloadHttpClient(Protocol):
    def get(self, step: str, url: str, **kwargs): ...


@dataclass(frozen=True)
class DownloadPlan:
    output_dir: Path
    course_dir: Path
    file_path: Path
    file_exists: bool = False
    conflict_strategy: str = 'rename'


_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]+')
_WHITESPACE = re.compile(r'\s+')
_VIDEO_EXTENSION_ALIASES = {
    'everlec': 'mp4',
    'movie': 'mp4',
    'readystream': 'mp4',
    'zoom': 'mp4',
    'video': 'mp4',
    'mp4': 'mp4',
}
MEDIA_DOWNLOAD_TIMEOUT_SECONDS = 3 * 60 * 60
# Pause before retrying when the request itself failed (connect error, timeout, 5xx)
# so a flapping network is not hammered N times in a row. Retries after a stream
# was interrupted mid-transfer resume immediately.
RETRY_BACKOFF_BASE_SECONDS = 0.5
RETRY_BACKOFF_MAX_SECONDS = 8.0
# 4xx answers are final except for these transient ones.
RETRYABLE_CLIENT_ERROR_STATUSES = frozenset({408, 425, 429})


def normalize_media_extension(extension: str | None) -> str:
    normalized = (extension or '').strip().lower().lstrip('.')
    if '/' in normalized:
        normalized = normalized.rsplit('/', 1)[-1]
    return _VIDEO_EXTENSION_ALIASES.get(normalized, normalized or 'mp4')


def safe_filename(value: str, fallback: str = 'lecture') -> str:
    cleaned = _UNSAFE_FILENAME_CHARS.sub('_', value)
    cleaned = _WHITESPACE.sub(' ', cleaned).strip(' ._')
    return cleaned or fallback


def _existing_with_content_id(course_dir: Path, content_id: str, extension: str) -> Path | None:
    if not content_id or content_id == 'content':
        return None
    marker = f'[{content_id}].{extension}'
    try:
        matches = sorted(entry for entry in course_dir.iterdir() if entry.is_file() and entry.name.endswith(marker))
    except OSError:
        return None
    return matches[0] if matches else None


def plan_download_path(
    *,
    output_dir: Path,
    course_name: str,
    lecture_title: str,
    content_id: str,
    extension: str,
    conflict_strategy: str = 'rename',
) -> DownloadPlan:
    if conflict_strategy not in {'rename', 'overwrite', 'skip'}:
        raise ValueError(f'unsupported conflict strategy: {conflict_strategy}')
    normalized_extension = normalize_media_extension(extension)
    course_dir = output_dir / safe_filename(course_name, fallback='course')
    stem = safe_filename(f'{lecture_title} [{content_id}]')
    original = course_dir / f'{stem}.{normalized_extension}'
    if conflict_strategy != 'rename' and not original.exists():
        # Files saved earlier under another title (e.g. the media server's name) are matched
        # by their `[content_id]`, so a re-download does not duplicate them.
        earlier = _existing_with_content_id(course_dir, content_id, normalized_extension)
        if earlier is not None:
            original = earlier
    original_exists = original.exists()
    candidate = original
    if conflict_strategy == 'rename':
        suffix = 2
        while candidate.exists():
            candidate = course_dir / f'{stem} ({suffix}).{normalized_extension}'
            suffix += 1
    return DownloadPlan(
        output_dir=output_dir,
        course_dir=course_dir,
        file_path=candidate,
        file_exists=original_exists if conflict_strategy != 'rename' else candidate.exists(),
        conflict_strategy=conflict_strategy,
    )


def find_downloaded_files(output_dir: Path, course_name: str, content_ids: Iterable[str]) -> dict[str, str]:
    """Already downloaded media per content id, found by the `[content_id]` in the file name.

    Looks only into the course folder `plan_download_path` would use; unfinished `.part`
    files do not count. Costs one directory listing, no LMS requests.
    """
    wanted = {content_id for content_id in content_ids if content_id}
    if not wanted:
        return {}
    course_dir = output_dir / safe_filename(course_name, fallback='course')
    found: dict[str, str] = {}
    try:
        entries = sorted(entry for entry in course_dir.iterdir() if entry.is_file() and entry.suffix != '.part')
    except OSError:
        return {}
    for entry in entries:
        for content_id in wanted:
            if content_id not in found and f'[{content_id}]' in entry.name:
                found[content_id] = str(entry)
    return found


def _response_chunks(response: Any, chunk_size: int) -> Iterable[bytes]:
    if hasattr(response, 'iter_content'):
        yield from response.iter_content(chunk_size=chunk_size)
        return
    content = getattr(response, 'content', b'')
    for offset in range(0, len(content), chunk_size):
        yield content[offset:offset + chunk_size]


def _header(response: Any, name: str) -> str | None:
    headers = getattr(response, 'headers', {}) or {}
    value = headers.get(name) or headers.get(name.title())
    return None if value is None else str(value)


def _content_length(response: Any) -> int | None:
    value = _header(response, 'content-length')
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _content_range_total(response: Any) -> int | None:
    """Total size from `Content-Range: bytes */N` or `bytes a-b/N`."""
    value = _header(response, 'content-range')
    if not value or '/' not in value:
        return None
    total = value.rsplit('/', 1)[-1].strip()
    return int(total) if total.isdigit() else None


def _is_identity_encoded(response: Any) -> bool:
    encoding = (_header(response, 'content-encoding') or 'identity').strip().lower()
    return encoding in {'', 'identity'}


def _close_response(response: Any) -> None:
    close = getattr(response, 'close', None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _is_retryable(exc: BaseException) -> bool:
    status = response_status(exc)
    if status is not None and 400 <= status < 500:
        return status in RETRYABLE_CLIENT_ERROR_STATUSES
    return True


def _backoff(attempt: int, should_cancel: Callable[[], bool] | None) -> None:
    delay = min(RETRY_BACKOFF_MAX_SECONDS, RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
    deadline = time.monotonic() + delay
    while True:
        if should_cancel and should_cancel():
            raise DownloadCancelled('download cancelled')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


def download_to_plan(
    *,
    http: DownloadHttpClient,
    download_url: str,
    plan: DownloadPlan,
    referer: str | None = None,
    timeout: int = MEDIA_DOWNLOAD_TIMEOUT_SECONDS,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    chunk_size: int = 1024 * 1024,
    max_attempts: int = 1,
    resume: bool = True,
    should_cancel: Callable[[], bool] | None = None,
    expected_total_bytes: int | None = None,
) -> Path:
    plan.course_dir.mkdir(parents=True, exist_ok=True)
    part_path = plan.file_path.with_suffix(plan.file_path.suffix + '.part')
    attempts = max(1, max_attempts)
    last_error: BaseException | None = None

    def open_stream(resume_from: int) -> Any:
        headers: dict[str, str] = {}
        if referer:
            headers['Referer'] = referer
        if resume_from:
            headers['Range'] = f'bytes={resume_from}-'
        kwargs: dict[str, Any] = {'allow_redirects': True, 'timeout': timeout, 'stream': True}
        if headers:
            kwargs['headers'] = headers
        return http.get('download_media', download_url, **kwargs)

    def finalize() -> Path:
        part_path.replace(plan.file_path)
        size = plan.file_path.stat().st_size
        emit_progress(on_progress, {'stage': 'download_done', 'attempt': attempt, 'bytes_written': size, 'total_bytes': size if total_bytes is None else total_bytes, 'output_path': str(plan.file_path)})
        return plan.file_path

    for attempt in range(1, attempts + 1):
        resume_from = part_path.stat().st_size if resume and part_path.exists() else 0
        total_bytes: int | None = None
        streamed_any = False
        response: Any = None
        try:
            response = open_stream(resume_from)
            if resume_from and getattr(response, 'status_code', None) == 416:
                # The server cannot satisfy our Range: either the partial file is already
                # the whole file, or it is stale/corrupt. Never loop on the same Range.
                announced_total = _content_range_total(response) or expected_total_bytes
                _close_response(response)
                response = None
                if announced_total is not None and resume_from == announced_total:
                    total_bytes = announced_total
                    return finalize()
                part_path.unlink(missing_ok=True)
                resume_from = 0
                response = open_stream(0)
            response.raise_for_status()
            if resume_from and getattr(response, 'status_code', None) != 206:
                resume_from = 0
            response_total = _content_length(response)
            total_bytes = resume_from + response_total if response_total is not None else None
            bytes_written = resume_from
            verify_length = total_bytes is not None and _is_identity_encoded(response)
            emit_progress(
                on_progress,
                {
                    'stage': 'download_started',
                    'attempt': attempt,
                    'resume_from': resume_from,
                    'bytes_written': bytes_written,
                    'total_bytes': total_bytes,
                    'output_path': str(plan.file_path),
                },
            )

            mode = 'ab' if resume_from else 'wb'
            with part_path.open(mode) as handle:
                for chunk in _response_chunks(response, chunk_size):
                    if should_cancel and should_cancel():
                        raise DownloadCancelled('download cancelled')
                    if not chunk:
                        continue
                    streamed_any = True
                    handle.write(chunk)
                    bytes_written += len(chunk)
                    emit_progress(
                        on_progress,
                        {
                            'stage': 'download_progress',
                            'attempt': attempt,
                            'bytes_written': bytes_written,
                            'total_bytes': total_bytes,
                            'output_path': str(plan.file_path),
                        },
                    )
            if verify_length and bytes_written != total_bytes:
                if total_bytes is not None and bytes_written > total_bytes:
                    # More data than announced means the partial file is not trustworthy.
                    part_path.unlink(missing_ok=True)
                raise IncompleteDownload(f'expected {total_bytes} bytes but received {bytes_written}')
            return finalize()
        except BaseException as exc:
            last_error = exc
            partial_size = part_path.stat().st_size if part_path.exists() else 0
            if isinstance(exc, DownloadCancelled):
                emit_progress(
                    on_progress,
                    {
                        'stage': 'download_cancelled',
                        'attempt': attempt,
                        'bytes_written': partial_size,
                        'total_bytes': total_bytes,
                        'output_path': str(plan.file_path),
                    },
                )
                raise
            if not isinstance(exc, Exception):
                raise
            if attempt < attempts and _is_retryable(exc):
                emit_progress(
                    on_progress,
                    {
                        'stage': 'download_retrying',
                        'attempt': attempt,
                        'next_attempt': attempt + 1,
                        'bytes_written': partial_size,
                        'total_bytes': total_bytes,
                        'error_code': type(exc).__name__,
                        'output_path': str(plan.file_path),
                    },
                )
                if not streamed_any:
                    _backoff(attempt, should_cancel)
                continue
            emit_progress(
                on_progress,
                {
                    'stage': 'download_failed',
                    'attempt': attempt,
                    'bytes_written': partial_size,
                    'total_bytes': total_bytes,
                    'error_code': type(exc).__name__,
                    'output_path': str(plan.file_path),
                },
            )
            raise
        finally:
            _close_response(response)

    assert last_error is not None
    raise last_error
