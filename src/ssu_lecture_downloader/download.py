from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class DownloadCancelled(Exception):
    """Raised when a caller requests cancellation during byte transfer."""


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


def normalize_media_extension(extension: str | None) -> str:
    normalized = (extension or '').strip().lower().lstrip('.')
    if '/' in normalized:
        normalized = normalized.rsplit('/', 1)[-1]
    return _VIDEO_EXTENSION_ALIASES.get(normalized, normalized or 'mp4')


def safe_filename(value: str, fallback: str = 'lecture') -> str:
    cleaned = _UNSAFE_FILENAME_CHARS.sub('_', value)
    cleaned = _WHITESPACE.sub(' ', cleaned).strip(' ._')
    return cleaned or fallback


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


def _response_chunks(response: Any, chunk_size: int) -> Iterable[bytes]:
    if hasattr(response, 'iter_content'):
        yield from response.iter_content(chunk_size=chunk_size)
        return
    content = getattr(response, 'content', b'')
    for offset in range(0, len(content), chunk_size):
        yield content[offset:offset + chunk_size]


def _content_length(response: Any) -> int | None:
    headers = getattr(response, 'headers', {}) or {}
    value = headers.get('content-length') or headers.get('Content-Length')
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _emit(on_progress: Callable[[dict[str, Any]], None] | None, event: dict[str, Any]) -> None:
    if on_progress:
        on_progress(event)


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
) -> Path:
    plan.course_dir.mkdir(parents=True, exist_ok=True)
    part_path = plan.file_path.with_suffix(plan.file_path.suffix + '.part')
    attempts = max(1, max_attempts)
    last_error: BaseException | None = None

    for attempt in range(1, attempts + 1):
        resume_from = part_path.stat().st_size if resume and part_path.exists() else 0
        headers: dict[str, str] = {}
        if referer:
            headers['Referer'] = referer
        if resume_from:
            headers['Range'] = f'bytes={resume_from}-'
        kwargs: dict[str, Any] = {"allow_redirects": True, "timeout": timeout, "stream": True}
        if headers:
            kwargs['headers'] = headers

        response = http.get("download_media", download_url, **kwargs)
        response.raise_for_status()
        if resume_from and getattr(response, 'status_code', None) != 206:
            resume_from = 0
        response_total = _content_length(response)
        total_bytes = resume_from + response_total if response_total is not None else None
        bytes_written = resume_from
        _emit(
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
        try:
            with part_path.open(mode) as handle:
                for chunk in _response_chunks(response, chunk_size):
                    if should_cancel and should_cancel():
                        raise DownloadCancelled('download cancelled')
                    if not chunk:
                        continue
                    handle.write(chunk)
                    bytes_written += len(chunk)
                    _emit(
                        on_progress,
                        {
                            'stage': 'download_progress',
                            'attempt': attempt,
                            'bytes_written': bytes_written,
                            'total_bytes': total_bytes,
                            'output_path': str(plan.file_path),
                        },
                    )
            part_path.replace(plan.file_path)
            _emit(
                on_progress,
                {
                    'stage': 'download_done',
                    'attempt': attempt,
                    'bytes_written': plan.file_path.stat().st_size,
                    'total_bytes': plan.file_path.stat().st_size if total_bytes is None else total_bytes,
                    'output_path': str(plan.file_path),
                },
            )
            return plan.file_path
        except BaseException as exc:
            last_error = exc
            partial_size = part_path.stat().st_size if part_path.exists() else 0
            if isinstance(exc, DownloadCancelled):
                _emit(
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
            if attempt < attempts:
                _emit(
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
                continue
            _emit(
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

    assert last_error is not None
    raise last_error
