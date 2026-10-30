from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ssu_lecture_downloader.core.errors import response_status
from ssu_lecture_downloader.core.library_layout import (
    CourseLayout,
    LibraryIndex,
    LibraryTarget,
    LecturePaths,
    MaterialPaths,
    collision_name,
)
from ssu_lecture_downloader.core.progress import emit_progress


class DownloadCancelled(Exception):
    """Raised when a caller requests cancellation during byte transfer."""


class IncompleteDownload(OSError):
    """Raised when the transferred byte count disagrees with the announced length."""


class DownloadHttpClient(Protocol):
    def get(self, step: str, url: str, **kwargs): ...


@dataclass(frozen=True)
class DownloadPlan:
    """Where one download goes. `course_dir` is the folder that holds `file_path` (a lecture or week folder).

    `library` (a `LecturePaths` / `MaterialPaths`) is prepared right before the first byte is written:
    it creates the folder and records the item in its hidden `.ssu` metadata.
    """

    output_dir: Path
    course_dir: Path
    file_path: Path
    file_exists: bool = False
    conflict_strategy: str = 'rename'
    library: LecturePaths | MaterialPaths | None = field(default=None, compare=False, repr=False)


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


_VIDEO_KINDS = frozenset({'mp4', 'm4v', 'mov', 'mkv', 'webm'})


def plan_library_download(
    *,
    output_dir: Path,
    library: LecturePaths | MaterialPaths,
    extension: str,
    conflict_strategy: str = 'rename',
) -> DownloadPlan:
    """The download plan of one item whose folder was already decided (see `CourseLayout`).

    - `skip`: an existing file in either layout (new layout first, then the old `[content id]` name) is the answer.
    - `overwrite`: always the new-layout path; an old-layout file is never written to.
    - `rename`: the new-layout path, `<stem> (n).<ext>` next to the first copy when that exists.
    """
    if conflict_strategy not in {'rename', 'overwrite', 'skip'}:
        raise ValueError(f'unsupported conflict strategy: {conflict_strategy}')
    if isinstance(library, LecturePaths):
        library = library.with_video_extension(normalize_media_extension(extension))
        directory = library.lecture_dir
        target = library.targets['video']
        stem = library.stem
        ext = library.video_ext
        existing = library.source_video()
    else:
        directory = library.week_dir
        target = library.target
        stem, _, ext = library.file_name.rpartition('.')
        existing = library.source_video()
    chosen = target
    file_exists = target.exists()
    if conflict_strategy == 'skip' and existing is not None:
        chosen = existing
        file_exists = True
    elif conflict_strategy == 'rename':
        number = 2
        while chosen.exists():
            chosen = directory / f'{collision_name(stem, number)}.{ext}'
            number += 1
        file_exists = chosen.exists()
    return DownloadPlan(
        output_dir=output_dir,
        course_dir=directory,
        file_path=chosen,
        file_exists=file_exists,
        conflict_strategy=conflict_strategy,
        library=library,
    )


def plan_download_path(
    *,
    output_dir: Path,
    course_name: str,
    lecture_title: str,
    content_id: str,
    extension: str,
    conflict_strategy: str = 'rename',
    module_name: str | None = None,
    week_position: object = None,
) -> DownloadPlan:
    """Standalone entry (CLI, tests): plan one item without a course plan, in the week -> lecture layout.

    Without a week the item goes to `기타`. The service plans whole courses with `CourseLayout` instead, so
    that same-title lectures get stable `(n)` names.
    """
    if conflict_strategy not in {'rename', 'overwrite', 'skip'}:
        raise ValueError(f'unsupported conflict strategy: {conflict_strategy}')
    normalized_extension = normalize_media_extension(extension)
    course_dir = output_dir / safe_filename(course_name, fallback='course')
    is_canvas = content_id.startswith('canvas-')
    kind = 'canvas_file' if is_canvas else ('lecture' if normalized_extension in _VIDEO_KINDS else 'material')
    key = content_id or 'item'
    target = LibraryTarget(
        key=key,
        title=lecture_title,
        kind=kind,
        content_id=None if content_id == 'content' else content_id,
        week_position=week_position,
        module_name=module_name,
        extension=normalized_extension,
    )
    layout = CourseLayout.plan(course_dir, [target], course_id='')
    library = layout.lecture(key) if kind == 'lecture' else layout.material(key)
    if library is None:
        raise ValueError('item could not be placed')
    return plan_library_download(output_dir=output_dir, library=library, extension=normalized_extension, conflict_strategy=conflict_strategy)


def find_downloaded_files(output_dir: Path, course_name: str, content_ids: Iterable[str]) -> dict[str, str]:
    """Already downloaded files per content id, found in either layout.

    New layout: `.ssu/item.json` of a lecture folder / `.ssu/materials.json` of a week folder. Old flat
    layout (read only): the `[content id]` marker in the course folder. Unfinished `.part` files do not
    count. Costs one walk of the course folder, no LMS requests. The path shown prefers video, audio,
    `자막.txt`, material, `.srt`.
    """
    wanted = [content_id for content_id in dict.fromkeys(content_ids) if content_id]
    if not wanted:
        return {}
    course_dir = output_dir / safe_filename(course_name, fallback='course')
    index = LibraryIndex.scan(course_dir)
    found: dict[str, str] = {}
    for content_id in wanted:
        path = index.downloaded_path(content_id)
        if path is not None:
            found[content_id] = str(path)
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
    if plan.library is not None:
        plan.library.prepare()
    else:
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
