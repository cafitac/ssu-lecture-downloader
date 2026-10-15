from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import xml.etree.ElementTree as ET
import zipfile
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from ssu_lecture_downloader.progress import emit_progress

AudioFormat = Literal['wav', 'm4a', 'mp3']
SUPPORTED_VIDEO_EXTENSIONS = {'.mp4', '.mov', '.m4v', '.mkv', '.webm'}
READY_STREAM_MEDIA_EXTENSIONS = {'.mp4', '.m4a', '.mov', '.m4v'}


class AudioExtractionRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    input_path: Path
    output_dir: Path | None = None
    format: AudioFormat = 'wav'
    overwrite: bool = False


class AudioExtractionResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    ok: bool
    input_path: Path
    output_path: Path | None = None
    file_size: int | None = None
    error_code: str | None = None
    message: str | None = None
    install_hint: str | None = None


class VideoReconstructionResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    ok: bool
    input_path: Path
    output_path: Path | None = None
    file_size: int | None = None
    reconstructed: bool = False
    source_kind: str | None = None
    error_code: str | None = None
    message: str | None = None
    install_hint: str | None = None


def ffmpeg_install_hint() -> str:
    return '앱 패키지에 포함된 ffmpeg를 찾지 못했습니다. 개발 모드에서는 uv sync 후 다시 실행하고, 배포 앱에서는 앱을 재설치해 주세요.'


def _locate_ffmpeg() -> tuple[str | None, str]:
    """(path, source) of the ffmpeg to use; source is 'system', 'bundled' or 'missing'."""
    system_ffmpeg = shutil.which('ffmpeg')
    if system_ffmpeg:
        return system_ffmpeg, 'system'
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        bundled_ffmpeg = get_ffmpeg_exe()
    except Exception:
        return None, 'missing'
    if bundled_ffmpeg and Path(bundled_ffmpeg).exists():
        return bundled_ffmpeg, 'bundled'
    return None, 'missing'


def bundled_or_system_ffmpeg() -> str | None:
    return _locate_ffmpeg()[0]


def ffmpeg_status() -> dict[str, object]:
    path, source = _locate_ffmpeg()
    available = path is not None
    return {
        'name': 'ffmpeg',
        'installed': available,
        'available': available,
        'source': source,
        'path': path,
        'install_hint': None if available else ffmpeg_install_hint(),
    }


def plan_audio_output_path(input_path: Path, output_dir: Path | None, format: AudioFormat) -> Path:
    base_dir = output_dir.expanduser() if output_dir else input_path.expanduser().parent / 'transcripts'
    return base_dir / f'{input_path.stem}.{format}'


# Every ffmpeg invocation is quiet (only real errors reach stderr) and never reads
# stdin, so a chatty build cannot fill the stderr pipe and an unattended process
# cannot block waiting for a keypress.
FFMPEG_QUIET_ARGS = ['-y', '-nostdin', '-loglevel', 'error']
FFMPEG_STDERR_TAIL_CHARS = 16 * 1024
FFMPEG_CANCEL_POLL_SECONDS = 0.2
FFPROBE_TIMEOUT_SECONDS = 120


class MediaCancelled(Exception):
    """Raised when a caller cancels while an ffmpeg process is running."""


def _ffmpeg_command(ffmpeg: str, input_path: Path, output_path: Path, format: AudioFormat) -> list[str]:
    base = [ffmpeg, *FFMPEG_QUIET_ARGS, '-i', str(input_path), '-vn']
    if format == 'wav':
        return [*base, '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(output_path)]
    if format == 'mp3':
        return [*base, '-c:a', 'libmp3lame', '-b:a', '96k', str(output_path)]
    return [*base, '-c:a', 'aac', '-b:a', '64k', str(output_path)]


def _ffmpeg_progress_command(ffmpeg: str, input_path: Path, output_path: Path, format: AudioFormat) -> list[str]:
    command = _ffmpeg_command(ffmpeg, input_path, output_path, format)
    return [*command[:-1], '-progress', 'pipe:1', '-nostats', command[-1]]


def _run_ffmpeg(
    command: list[str],
    *,
    on_stdout_line: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    check: bool = True,
) -> str:
    """Run ffmpeg without risking a pipe deadlock and with a kill switch.

    stderr is drained by a dedicated thread while stdout is consumed here, so neither
    pipe can fill up. When `should_cancel` turns true the process is killed and
    MediaCancelled is raised. Returns the tail of stderr.
    """
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stderr_chunks: deque[str] = deque()
    stderr_size = 0
    cancelled = threading.Event()
    finished = threading.Event()

    def drain_stderr() -> None:
        nonlocal stderr_size
        stream = process.stderr
        if stream is None:
            return
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            stderr_chunks.append(chunk)
            stderr_size += len(chunk)
            while stderr_size > FFMPEG_STDERR_TAIL_CHARS and len(stderr_chunks) > 1:
                stderr_size -= len(stderr_chunks.popleft())

    def watch_cancel() -> None:
        assert should_cancel is not None
        while not finished.wait(FFMPEG_CANCEL_POLL_SECONDS):
            if should_cancel():
                cancelled.set()
                try:
                    process.kill()
                except OSError:
                    pass
                return

    drainer = threading.Thread(target=drain_stderr, name='ffmpeg-stderr', daemon=True)
    drainer.start()
    watcher = None
    if should_cancel is not None:
        watcher = threading.Thread(target=watch_cancel, name='ffmpeg-cancel', daemon=True)
        watcher.start()
    try:
        if process.stdout is not None:
            for raw_line in process.stdout:
                if on_stdout_line is not None:
                    on_stdout_line(raw_line)
        return_code = process.wait()
    except BaseException:
        try:
            process.kill()
        except OSError:
            pass
        process.wait()
        raise
    finally:
        finished.set()
        drainer.join(timeout=5)
        if watcher is not None:
            watcher.join(timeout=5)
    stderr_text = ''.join(stderr_chunks)[-FFMPEG_STDERR_TAIL_CHARS:]
    if cancelled.is_set():
        raise MediaCancelled('ffmpeg cancelled')
    if check and return_code != 0:
        raise subprocess.CalledProcessError(return_code, command, stderr=stderr_text)
    return stderr_text


def _temp_sibling(path: Path) -> Path:
    """Hidden-ish sibling that keeps the real extension so ffmpeg still infers the muxer."""
    return path.with_name(f'{path.stem}.__partial__{path.suffix}')


def _ffprobe_for_ffmpeg(ffmpeg: str) -> str | None:
    system_ffprobe = shutil.which('ffprobe')
    if system_ffprobe:
        return system_ffprobe
    ffmpeg_path = Path(ffmpeg)
    sibling = ffmpeg_path.with_name('ffprobe')
    if sibling.exists():
        return str(sibling)
    return None


def _media_duration_ms(ffmpeg: str, input_path: Path) -> int | None:
    ffprobe = _ffprobe_for_ffmpeg(ffmpeg)
    if not ffprobe:
        return None
    try:
        completed = subprocess.run(
            [ffprobe, '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(input_path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=FFPROBE_TIMEOUT_SECONDS,
        )
    except Exception:
        return None
    try:
        duration_seconds = float((completed.stdout or '').strip())
    except ValueError:
        return None
    if duration_seconds <= 0:
        return None
    return int(duration_seconds * 1000)


def _parse_ffmpeg_out_time_ms(value: str) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        return None
    # ffmpeg's out_time_ms is actually microseconds in common builds.
    return max(0, parsed // 1000)


def _parse_ffmpeg_out_time(value: str) -> int | None:
    match = re.match(r'(?P<h>\d+):(?P<m>\d+):(?P<s>\d+(?:\.\d+)?)', value)
    if not match:
        return None
    seconds = int(match.group('h')) * 3600 + int(match.group('m')) * 60 + float(match.group('s'))
    return int(seconds * 1000)


def _run_ffmpeg_with_progress(
    command: list[str],
    *,
    input_path: Path,
    output_path: Path,
    duration_ms: int | None,
    on_progress: Callable[[dict[str, Any]], None] | None,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    state = {'latest_ms': 0, 'last_percent': None}

    def handle_line(raw_line: str) -> None:
        line = raw_line.strip()
        if not line or '=' not in line:
            return
        key, value = line.split('=', 1)
        parsed_ms = None
        if key == 'out_time_ms':
            parsed_ms = _parse_ffmpeg_out_time_ms(value)
        elif key == 'out_time':
            parsed_ms = _parse_ffmpeg_out_time(value)
        if parsed_ms is None:
            return
        state['latest_ms'] = max(state['latest_ms'], parsed_ms)
        percent = min(99, max(0, round((state['latest_ms'] / duration_ms) * 100))) if duration_ms else None
        if percent == state['last_percent']:
            return
        emit_progress(
            on_progress,
            {
                'stage': 'audio_progress',
                'input_path': str(input_path),
                'output_path': str(output_path),
                'elapsed_ms': state['latest_ms'],
                'duration_ms': duration_ms,
                'progress_percent': percent,
            },
        )
        state['last_percent'] = percent

    _run_ffmpeg(command, on_stdout_line=handle_line, should_cancel=should_cancel)
    emit_progress(
        on_progress,
        {
            'stage': 'audio_progress',
            'input_path': str(input_path),
            'output_path': str(output_path),
            'elapsed_ms': duration_ms,
            'duration_ms': duration_ms,
            'progress_percent': 100,
        },
    )


def _is_ready_stream_package(path: Path) -> bool:
    if not zipfile.is_zipfile(path):
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            return 'contents/media_root.xml' in names or any(name.startswith('contents/media_files/') for name in names)
    except zipfile.BadZipFile:
        return False


def _ready_stream_media_members(path: Path) -> list[zipfile.ZipInfo]:
    with zipfile.ZipFile(path) as archive:
        candidates = [
            item
            for item in archive.infolist()
            if not item.is_dir()
            and item.filename.startswith('contents/media_files/')
            and Path(item.filename).suffix.lower() in READY_STREAM_MEDIA_EXTENSIONS
            and item.file_size > 0
        ]
    return sorted(candidates, key=lambda item: item.file_size, reverse=True)


def _has_video_stream(ffprobe: str, input_path: Path) -> bool:
    try:
        completed = subprocess.run(
            [ffprobe, '-v', 'error', '-show_entries', 'stream=codec_type', '-of', 'json', str(input_path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=FFPROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    try:
        payload = json.loads(completed.stdout or '{}')
    except json.JSONDecodeError:
        return False
    streams = payload.get('streams')
    if not isinstance(streams, list):
        return False
    return any(isinstance(stream, dict) and stream.get('codec_type') == 'video' for stream in streams)


def _decode_check_stderr(ffmpeg: str, input_path: Path, should_cancel: Callable[[], bool] | None = None) -> str:
    return _run_ffmpeg([ffmpeg, '-v', 'error', '-nostdin', '-i', str(input_path), '-f', 'null', '-'], should_cancel=should_cancel, check=False).strip()


def _remux_inner_video(ffmpeg: str, input_path: Path, output_path: Path, should_cancel: Callable[[], bool] | None = None) -> None:
    _run_ffmpeg(
        [
            ffmpeg,
            *FFMPEG_QUIET_ARGS,
            '-i',
            str(input_path),
            '-map',
            '0',
            '-c',
            'copy',
            '-movflags',
            '+faststart',
            '-avoid_negative_ts',
            'make_zero',
            str(output_path),
        ],
        should_cancel=should_cancel,
    )


def _transcode_inner_video_for_compatibility(ffmpeg: str, input_path: Path, output_path: Path, should_cancel: Callable[[], bool] | None = None) -> None:
    _run_ffmpeg(
        [
            ffmpeg,
            *FFMPEG_QUIET_ARGS,
            '-fflags',
            '+genpts',
            '-i',
            str(input_path),
            '-map',
            '0:v:0',
            '-map',
            '0:a?',
            '-vf',
            'fps=15,format=yuv420p',
            '-c:v',
            'libx264',
            '-preset',
            'ultrafast',
            '-crf',
            '20',
            '-c:a',
            'copy',
            '-movflags',
            '+faststart',
            '-avoid_negative_ts',
            'make_zero',
            str(output_path),
        ],
        should_cancel=should_cancel,
    )


def _extract_inner_video_if_available(
    archive: zipfile.ZipFile,
    *,
    input_path: Path,
    output_path: Path,
    temp_path: Path,
    ffmpeg: str,
    ffprobe: str,
    on_progress: Callable[[dict[str, Any]], None] | None,
    should_cancel: Callable[[], bool] | None = None,
) -> VideoReconstructionResult | None:
    video_members = [
        member
        for member in _ready_stream_media_members(input_path)
        if Path(member.filename).suffix.lower() in {'.mp4', '.mov', '.m4v'}
    ]
    for member in video_members:
        candidate_path = temp_path / f'inner-video{Path(member.filename).suffix.lower()}'
        with archive.open(member) as source, candidate_path.open('wb') as target:
            shutil.copyfileobj(source, target)
        if not _has_video_stream(ffprobe, candidate_path):
            candidate_path.unlink(missing_ok=True)
            continue
        output_path.parent.mkdir(parents=True, exist_ok=True)
        emit_progress(
            on_progress,
            {
                'stage': 'video_inner_media_extract_started',
                'input_path': str(input_path),
                'output_path': str(output_path),
                'source_member': member.filename,
            },
        )
        _remux_inner_video(ffmpeg, candidate_path, output_path, should_cancel)
        decode_warning = _decode_check_stderr(ffmpeg, output_path, should_cancel)
        if decode_warning:
            emit_progress(
                on_progress,
                {
                    'stage': 'video_inner_media_normalize_started',
                    'input_path': str(input_path),
                    'output_path': str(output_path),
                },
            )
            _transcode_inner_video_for_compatibility(ffmpeg, candidate_path, output_path, should_cancel)
            source_kind = 'readystream_inner_video_normalized'
        else:
            source_kind = 'readystream_inner_video'
        emit_progress(
            on_progress,
            {
                'stage': 'video_inner_media_extract_done',
                'input_path': str(input_path),
                'output_path': str(output_path),
                'file_size': output_path.stat().st_size,
                'progress_percent': 100,
            },
        )
        return VideoReconstructionResult(
            ok=True,
            input_path=input_path,
            output_path=output_path,
            file_size=output_path.stat().st_size,
            reconstructed=True,
            source_kind=source_kind,
        )
    return None


@contextmanager
def _extractable_media_input(input_path: Path) -> Iterator[Path]:
    if not _is_ready_stream_package(input_path):
        yield input_path
        return

    members = _ready_stream_media_members(input_path)
    if not members:
        yield input_path
        return

    with tempfile.TemporaryDirectory(prefix='ssu-readystream-') as temp_dir:
        member = members[0]
        extracted_path = Path(temp_dir) / f'{input_path.stem}{Path(member.filename).suffix.lower()}'
        with zipfile.ZipFile(input_path) as archive, archive.open(member) as source, extracted_path.open('wb') as target:
            shutil.copyfileobj(source, target)
        yield extracted_path


def _xml_root(data: bytes) -> ET.Element:
    return ET.fromstring(data.decode('utf-8-sig'))


def _tag_name(element: ET.Element) -> str:
    return element.tag.rsplit('}', 1)[-1]


def _plan_reconstructed_video_path(input_path: Path) -> Path:
    return input_path.with_name(f'{input_path.stem} reconstructed.mp4')


def _ready_stream_backup_path(input_path: Path) -> Path:
    return input_path.with_suffix('.readystream-package.zip')


def _restore_ready_stream_package(input_path: Path, backup_path: Path) -> None:
    """Put the original package back at its user-facing path after a failed conversion."""
    if input_path.exists() or not backup_path.exists():
        return
    try:
        backup_path.replace(input_path)
    except OSError:
        # Leave the backup in place; losing the package is worse than a stray rename.
        pass


def ensure_playable_mp4(
    input_path: Path,
    *,
    overwrite: bool = False,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> VideoReconstructionResult:
    input_path = input_path.expanduser()
    if not input_path.exists():
        return VideoReconstructionResult(ok=False, input_path=input_path, output_path=input_path, error_code='input_not_found', message='영상 파일을 찾을 수 없습니다.')
    if not _is_ready_stream_package(input_path):
        return VideoReconstructionResult(ok=True, input_path=input_path, output_path=input_path, file_size=input_path.stat().st_size, reconstructed=False, source_kind='mp4')

    backup_path = _ready_stream_backup_path(input_path)
    temp_output = input_path.with_name(f'{input_path.stem}.__reconstructed_tmp__.mp4')
    converted = False
    try:
        if backup_path.exists():
            # A previous failed/partial conversion may already have preserved the
            # ReadyStream package. Reuse it and remove the user-facing package path
            # before producing the playable MP4 so large lectures do not require an
            # extra ~1GB of free disk space during conversion.
            if _is_ready_stream_package(input_path):
                input_path.unlink()
        else:
            # Move instead of copy: the backup is the durable raw package, and the
            # original path is reserved for the final playable MP4. Copying keeps two
            # full package copies plus the normalized MP4 on disk, which fails on
            # near-full macOS disks for ~1GB ReadyStream lectures.
            input_path.replace(backup_path)
        result = reconstruct_ready_stream_video(backup_path, output_path=temp_output, overwrite=True, on_progress=on_progress, **_cancel_kwargs(should_cancel))
        if not result.ok:
            return VideoReconstructionResult(
                ok=False,
                input_path=input_path,
                output_path=input_path,
                error_code=result.error_code,
                message=result.message,
                install_hint=result.install_hint,
            )
        temp_output.replace(input_path)
        converted = True
        return VideoReconstructionResult(
            ok=True,
            input_path=input_path,
            output_path=input_path,
            file_size=input_path.stat().st_size,
            reconstructed=True,
            source_kind=result.source_kind or 'readystream_package',
        )
    except MediaCancelled:
        return VideoReconstructionResult(ok=False, input_path=input_path, output_path=input_path, error_code='cancelled', message='작업이 취소되었습니다.')
    except Exception as exc:
        return VideoReconstructionResult(ok=False, input_path=input_path, output_path=input_path, error_code='video_reconstruction_failed', message=f'{type(exc).__name__}: {exc}'[:500])
    finally:
        # Whatever went wrong (including BaseException), the user must still find
        # their original file at `input_path`.
        if not converted:
            temp_output.unlink(missing_ok=True)
            _restore_ready_stream_package(input_path, backup_path)


def _cancel_kwargs(should_cancel: Callable[[], bool] | None) -> dict[str, Any]:
    return {'should_cancel': should_cancel} if should_cancel is not None else {}


class _ReconstructionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class _SlideshowSource:
    audio_path: Path
    concat_path: Path
    slide_count: int
    total_duration: float


def _read_unit_media_refs(archive: zipfile.ZipFile) -> tuple[str, str]:
    """(MediaUri, EventListUri) from the package's unitmeta XML."""
    unit_name = next((name for name in archive.namelist() if name.startswith('contents/web_files/unitmeta_') and name.endswith('.xml')), None)
    if unit_name is None:
        raise _ReconstructionError('readystream_unitmeta_missing', 'ReadyStream 메타데이터를 찾을 수 없습니다.')
    unit_root = _xml_root(archive.read(unit_name))
    media_uri = None
    event_uri = None
    for part in unit_root.iter():
        if _tag_name(part) != 'part':
            continue
        if part.attrib.get('name') == 'MediaUri':
            media_uri = part.attrib.get('uri')
        elif part.attrib.get('name') == 'EventListUri':
            event_uri = part.attrib.get('uri')
    if not media_uri or not event_uri:
        raise _ReconstructionError('readystream_timeline_missing', 'ReadyStream 슬라이드/오디오 정보를 찾을 수 없습니다.')
    return media_uri, event_uri


def _extract_slideshow_audio(archive: zipfile.ZipFile, media_uri: str, temp_path: Path) -> Path:
    media_member = f'contents/media_files/media/{media_uri}'
    if media_member not in archive.namelist():
        raise _ReconstructionError('readystream_audio_missing', 'ReadyStream 내부 오디오를 찾을 수 없습니다.')
    audio_path = temp_path / f'audio{Path(media_member).suffix.lower() or ".m4a"}'
    with archive.open(media_member) as source, audio_path.open('wb') as target:
        shutil.copyfileobj(source, target)
    return audio_path


def _read_slide_timeline(archive: zipfile.ZipFile, event_uri: str) -> list[tuple[float, str]]:
    event_member = f'contents/web_files/{event_uri}'
    if event_member not in archive.namelist():
        raise _ReconstructionError('readystream_eventlist_missing', 'ReadyStream 슬라이드 타임라인을 찾을 수 없습니다.')
    event_root = _xml_root(archive.read(event_member))
    slide_refs: list[tuple[float, str]] = []
    for slide_ref in event_root.iter():
        if _tag_name(slide_ref) != 'slide_ref':
            continue
        sca_id = slide_ref.attrib.get('sca_idref')
        time_value = slide_ref.attrib.get('time')
        if not sca_id or time_value is None:
            continue
        slide_refs.append((float(time_value), sca_id))
    slide_refs.sort()
    if not slide_refs:
        raise _ReconstructionError('readystream_slides_missing', 'ReadyStream 슬라이드 정보를 찾을 수 없습니다.')
    return slide_refs


def _audio_duration_seconds(ffprobe: str, audio_path: Path) -> float:
    duration_output = subprocess.run(
        [ffprobe, '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(audio_path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=FFPROBE_TIMEOUT_SECONDS,
    )
    return float(duration_output.stdout.strip())


def _write_slide_frames(archive: zipfile.ZipFile, slide_refs: list[tuple[float, str]], total_duration: float, temp_path: Path) -> list[tuple[Path, float]]:
    frames_dir = temp_path / 'frames'
    frames_dir.mkdir()
    names = set(archive.namelist())
    entries: list[tuple[Path, float]] = []
    for index, (start, sca_id) in enumerate(slide_refs):
        end = slide_refs[index + 1][0] if index + 1 < len(slide_refs) else total_duration
        duration = max(0.1, end - start)
        thumbnail_member = f'contents/web_files/navi_thumbnail/large/{sca_id}.jpg'
        if thumbnail_member not in names:
            thumbnail_member = f'contents/web_files/navi_thumbnail/{sca_id}.jpg'
        if thumbnail_member not in names:
            continue
        frame_path = frames_dir / f'{index:04d}.jpg'
        frame_path.write_bytes(archive.read(thumbnail_member))
        entries.append((frame_path, duration))
    if not entries:
        raise _ReconstructionError('readystream_thumbnails_missing', 'ReadyStream 슬라이드 이미지를 찾을 수 없습니다.')
    return entries


def _write_concat_file(concat_path: Path, entries: list[tuple[Path, float]]) -> None:
    with concat_path.open('w', encoding='utf-8') as concat_file:
        for frame_path, duration in entries:
            concat_file.write(f"file {str(frame_path)!r}\n")
            concat_file.write(f"duration {duration:.6f}\n")
        concat_file.write(f"file {str(entries[-1][0])!r}\n")


def _prepare_slideshow_source(archive: zipfile.ZipFile, *, ffprobe: str, temp_path: Path) -> _SlideshowSource:
    media_uri, event_uri = _read_unit_media_refs(archive)
    audio_path = _extract_slideshow_audio(archive, media_uri, temp_path)
    slide_refs = _read_slide_timeline(archive, event_uri)
    total_duration = _audio_duration_seconds(ffprobe, audio_path)
    entries = _write_slide_frames(archive, slide_refs, total_duration, temp_path)
    concat_path = temp_path / 'concat.txt'
    _write_concat_file(concat_path, entries)
    return _SlideshowSource(audio_path=audio_path, concat_path=concat_path, slide_count=len(entries), total_duration=total_duration)


def _encode_slideshow(ffmpeg: str, source: _SlideshowSource, output_path: Path, should_cancel: Callable[[], bool] | None) -> None:
    _run_ffmpeg(
        [
            ffmpeg,
            *FFMPEG_QUIET_ARGS,
            '-f',
            'concat',
            '-safe',
            '0',
            '-i',
            str(source.concat_path),
            '-i',
            str(source.audio_path),
            '-vf',
            'scale=1280:960:force_original_aspect_ratio=decrease,pad=1280:960:(ow-iw)/2:(oh-ih)/2,fps=1',
            '-c:v',
            'libx264',
            '-preset',
            'ultrafast',
            '-crf',
            '28',
            '-pix_fmt',
            'yuv420p',
            '-c:a',
            'copy',
            '-shortest',
            str(output_path),
        ],
        should_cancel=should_cancel,
    )


def reconstruct_ready_stream_video(
    input_path: Path,
    *,
    output_path: Path | None = None,
    overwrite: bool = False,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> VideoReconstructionResult:
    input_path = input_path.expanduser()
    output_path = output_path.expanduser() if output_path else _plan_reconstructed_video_path(input_path)

    def failure(code: str, message: str, install_hint: str | None = None) -> VideoReconstructionResult:
        return VideoReconstructionResult(ok=False, input_path=input_path, output_path=output_path, error_code=code, message=message, install_hint=install_hint)

    if not input_path.exists():
        return failure('input_not_found', '영상 파일을 찾을 수 없습니다.')
    if output_path.exists() and not overwrite:
        return VideoReconstructionResult(ok=True, input_path=input_path, output_path=output_path, file_size=output_path.stat().st_size, reconstructed=True, source_kind='readystream_package')
    if not _is_ready_stream_package(input_path):
        return VideoReconstructionResult(ok=True, input_path=input_path, output_path=input_path, file_size=input_path.stat().st_size, reconstructed=False, source_kind='mp4')
    ffmpeg = bundled_or_system_ffmpeg()
    if not ffmpeg:
        return failure('ffmpeg_not_found', 'ffmpeg를 찾을 수 없습니다.', ffmpeg_install_hint())
    ffprobe = _ffprobe_for_ffmpeg(ffmpeg)
    if not ffprobe:
        return failure('ffprobe_not_found', 'ffprobe를 찾을 수 없습니다.')

    try:
        with tempfile.TemporaryDirectory(prefix='ssu-readystream-video-') as temp_dir, zipfile.ZipFile(input_path) as archive:
            temp_path = Path(temp_dir)
            inner_video_result = _extract_inner_video_if_available(
                archive,
                input_path=input_path,
                output_path=output_path,
                temp_path=temp_path,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                on_progress=on_progress,
                should_cancel=should_cancel,
            )
            if inner_video_result is not None:
                return inner_video_result

            source = _prepare_slideshow_source(archive, ffprobe=ffprobe, temp_path=temp_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            emit_progress(on_progress, {'stage': 'video_reconstruction_started', 'input_path': str(input_path), 'output_path': str(output_path), 'slide_count': source.slide_count, 'duration_ms': int(source.total_duration * 1000)})
            _encode_slideshow(ffmpeg, source, output_path, should_cancel)
            emit_progress(on_progress, {'stage': 'video_reconstruction_done', 'input_path': str(input_path), 'output_path': str(output_path), 'file_size': output_path.stat().st_size, 'progress_percent': 100})
    except _ReconstructionError as exc:
        return failure(exc.code, exc.message)
    except MediaCancelled:
        return failure('cancelled', '작업이 취소되었습니다.')
    except (zipfile.BadZipFile, ET.ParseError, StopIteration, ValueError, OSError, subprocess.SubprocessError) as exc:
        message = 'ReadyStream 영상을 MP4로 재구성하지 못했습니다.'
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or exc.stdout or '').strip().splitlines()[-1:]
            if detail:
                message = detail[0][:500]
        return failure('video_reconstruction_failed', message)
    if not output_path.exists():
        return failure('video_reconstruction_output_missing', 'MP4 재구성 후 파일이 생성되지 않았습니다.')
    return VideoReconstructionResult(ok=True, input_path=input_path, output_path=output_path, file_size=output_path.stat().st_size, reconstructed=True, source_kind='readystream_package')


def extract_audio(
    request: AudioExtractionRequest,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> AudioExtractionResult:
    input_path = request.input_path.expanduser()
    output_path = plan_audio_output_path(input_path, request.output_dir, request.format)

    def failure(code: str, message: str, install_hint: str | None = None) -> AudioExtractionResult:
        return AudioExtractionResult(ok=False, input_path=input_path, output_path=output_path, error_code=code, message=message, install_hint=install_hint)

    if not input_path.exists():
        return failure('input_not_found', '영상 파일을 찾을 수 없습니다.')
    if not input_path.is_file():
        return failure('input_not_file', '입력 경로가 파일이 아닙니다.')
    if input_path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
        return failure('unsupported_video_format', '지원하지 않는 영상 파일 형식입니다.')
    ffmpeg = bundled_or_system_ffmpeg()
    if not ffmpeg:
        return failure('ffmpeg_not_found', 'ffmpeg를 찾을 수 없습니다.', ffmpeg_install_hint())
    if output_path.exists() and not request.overwrite:
        return failure('audio_exists', '이미 추출된 음성 파일이 있습니다.')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # ffmpeg writes to a sibling temp file and the result is swapped in atomically, so
    # a crash or cancel never leaves a truncated file at the final path for later
    # "already exists" checks to mistake for a finished extraction.
    partial_path = _temp_sibling(output_path)
    partial_path.unlink(missing_ok=True)
    try:
        with _extractable_media_input(input_path) as ffmpeg_input_path:
            if on_progress:
                duration_ms = _media_duration_ms(ffmpeg, ffmpeg_input_path)
                emit_progress(on_progress, {'stage': 'audio_probe_done', 'input_path': str(input_path), 'duration_ms': duration_ms})
                command = _ffmpeg_progress_command(ffmpeg, ffmpeg_input_path, partial_path, request.format)
                _run_ffmpeg_with_progress(command, input_path=input_path, output_path=output_path, duration_ms=duration_ms, on_progress=on_progress, should_cancel=should_cancel)
            else:
                command = _ffmpeg_command(ffmpeg, ffmpeg_input_path, partial_path, request.format)
                _run_ffmpeg(command, should_cancel=should_cancel)
        if not partial_path.exists():
            return failure('audio_output_missing', 'ffmpeg 실행 후 음성 파일이 생성되지 않았습니다.')
        os.replace(partial_path, output_path)
    except MediaCancelled:
        return failure('cancelled', '작업이 취소되었습니다.')
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or '').strip().splitlines()[-1:] or ['ffmpeg 실행에 실패했습니다.']
        return failure('ffmpeg_failed', detail[0][:500])
    finally:
        partial_path.unlink(missing_ok=True)
    return AudioExtractionResult(ok=True, input_path=input_path, output_path=output_path, file_size=output_path.stat().st_size)
