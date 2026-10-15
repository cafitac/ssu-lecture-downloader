from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal

from pydantic import BaseModel, ConfigDict


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


def bundled_or_system_ffmpeg() -> str | None:
    system_ffmpeg = shutil.which('ffmpeg')
    if system_ffmpeg:
        return system_ffmpeg
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
    except Exception:
        return None
    try:
        bundled_ffmpeg = get_ffmpeg_exe()
    except Exception:
        return None
    return bundled_ffmpeg if Path(bundled_ffmpeg).exists() else None


def ffmpeg_status() -> dict[str, object]:
    system_ffmpeg = shutil.which('ffmpeg')
    if system_ffmpeg:
        return {
            'name': 'ffmpeg',
            'installed': True,
            'available': True,
            'source': 'system',
            'path': system_ffmpeg,
            'install_hint': None,
        }
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        bundled_ffmpeg = get_ffmpeg_exe()
    except Exception:
        bundled_ffmpeg = None
    available = bool(bundled_ffmpeg and Path(bundled_ffmpeg).exists())
    return {
        'name': 'ffmpeg',
        'installed': available,
        'available': available,
        'source': 'bundled' if available else 'missing',
        'path': bundled_ffmpeg if available else None,
        'install_hint': None if available else ffmpeg_install_hint(),
    }


def plan_audio_output_path(input_path: Path, output_dir: Path | None, format: AudioFormat) -> Path:
    base_dir = output_dir.expanduser() if output_dir else input_path.expanduser().parent / 'transcripts'
    return base_dir / f'{input_path.stem}.{format}'


def _ffmpeg_command(ffmpeg: str, input_path: Path, output_path: Path, format: AudioFormat) -> list[str]:
    if format == 'wav':
        return [ffmpeg, '-y', '-i', str(input_path), '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(output_path)]
    if format == 'mp3':
        return [ffmpeg, '-y', '-i', str(input_path), '-vn', '-c:a', 'libmp3lame', '-b:a', '96k', str(output_path)]
    return [ffmpeg, '-y', '-i', str(input_path), '-vn', '-c:a', 'aac', '-b:a', '64k', str(output_path)]


def _ffmpeg_progress_command(ffmpeg: str, input_path: Path, output_path: Path, format: AudioFormat) -> list[str]:
    command = _ffmpeg_command(ffmpeg, input_path, output_path, format)
    return [*command[:-1], '-progress', 'pipe:1', '-nostats', command[-1]]


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


def _emit_progress(on_progress: Callable[[dict[str, Any]], None] | None, event: dict[str, Any]) -> None:
    if on_progress:
        on_progress(event)


def _run_ffmpeg_with_progress(
    command: list[str],
    *,
    input_path: Path,
    output_path: Path,
    duration_ms: int | None,
    on_progress: Callable[[dict[str, Any]], None] | None,
) -> None:
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    latest_ms = 0
    last_percent: int | None = None
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.strip()
        if not line or '=' not in line:
            continue
        key, value = line.split('=', 1)
        parsed_ms = None
        if key == 'out_time_ms':
            parsed_ms = _parse_ffmpeg_out_time_ms(value)
        elif key == 'out_time':
            parsed_ms = _parse_ffmpeg_out_time(value)
        if parsed_ms is not None:
            latest_ms = max(latest_ms, parsed_ms)
            percent = min(99, max(0, round((latest_ms / duration_ms) * 100))) if duration_ms else None
            if percent == last_percent:
                continue
            _emit_progress(
                on_progress,
                {
                    'stage': 'audio_progress',
                    'input_path': str(input_path),
                    'output_path': str(output_path),
                    'elapsed_ms': latest_ms,
                    'duration_ms': duration_ms,
                    'progress_percent': percent,
                },
            )
            last_percent = percent
    stderr_text = process.stderr.read() if process.stderr is not None else ''
    return_code = process.wait()
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, command, stderr=stderr_text)
    _emit_progress(
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
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    try:
        payload = json.loads(completed.stdout or '{}')
    except json.JSONDecodeError:
        return False
    streams = payload.get('streams')
    if not isinstance(streams, list):
        return False
    return any(isinstance(stream, dict) and stream.get('codec_type') == 'video' for stream in streams)


def _decode_check_stderr(ffmpeg: str, input_path: Path) -> str:
    completed = subprocess.run(
        [ffmpeg, '-v', 'error', '-i', str(input_path), '-f', 'null', '-'],
        capture_output=True,
        text=True,
        check=False,
    )
    return (completed.stderr or completed.stdout or '').strip()


def _remux_inner_video(ffmpeg: str, input_path: Path, output_path: Path) -> None:
    subprocess.run(
        [
            ffmpeg,
            '-y',
            '-v',
            'error',
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
        capture_output=True,
        text=True,
        check=True,
    )


def _transcode_inner_video_for_compatibility(ffmpeg: str, input_path: Path, output_path: Path) -> None:
    subprocess.run(
        [
            ffmpeg,
            '-y',
            '-v',
            'error',
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
        capture_output=True,
        text=True,
        check=True,
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
        _emit_progress(
            on_progress,
            {
                'stage': 'video_inner_media_extract_started',
                'input_path': str(input_path),
                'output_path': str(output_path),
                'source_member': member.filename,
            },
        )
        _remux_inner_video(ffmpeg, candidate_path, output_path)
        decode_warning = _decode_check_stderr(ffmpeg, output_path)
        if decode_warning:
            _emit_progress(
                on_progress,
                {
                    'stage': 'video_inner_media_normalize_started',
                    'input_path': str(input_path),
                    'output_path': str(output_path),
                },
            )
            _transcode_inner_video_for_compatibility(ffmpeg, candidate_path, output_path)
            source_kind = 'readystream_inner_video_normalized'
        else:
            source_kind = 'readystream_inner_video'
        _emit_progress(
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


def ensure_playable_mp4(
    input_path: Path,
    *,
    overwrite: bool = False,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> VideoReconstructionResult:
    input_path = input_path.expanduser()
    if not input_path.exists():
        return VideoReconstructionResult(ok=False, input_path=input_path, output_path=input_path, error_code='input_not_found', message='영상 파일을 찾을 수 없습니다.')
    if not _is_ready_stream_package(input_path):
        return VideoReconstructionResult(ok=True, input_path=input_path, output_path=input_path, file_size=input_path.stat().st_size, reconstructed=False, source_kind='mp4')

    backup_path = _ready_stream_backup_path(input_path)
    reconstruction_input = input_path
    if backup_path.exists():
        # A previous failed/partial conversion may already have preserved the
        # ReadyStream package. Reuse it and remove the user-facing package path
        # before producing the playable MP4 so large lectures do not require an
        # extra ~1GB of free disk space during conversion.
        if input_path.exists() and _is_ready_stream_package(input_path):
            input_path.unlink()
        reconstruction_input = backup_path
    else:
        # Move instead of copy: the backup is the durable raw package, and the
        # original path is reserved for the final playable MP4. Copying keeps two
        # full package copies plus the normalized MP4 on disk, which fails on
        # near-full macOS disks for ~1GB ReadyStream lectures.
        input_path.replace(backup_path)
        reconstruction_input = backup_path
    temp_output = input_path.with_name(f'{input_path.stem}.__reconstructed_tmp__.mp4')
    result = reconstruct_ready_stream_video(reconstruction_input, output_path=temp_output, overwrite=True, on_progress=on_progress)
    if not result.ok:
        temp_output.unlink(missing_ok=True)
        return VideoReconstructionResult(
            ok=False,
            input_path=input_path,
            output_path=input_path,
            error_code=result.error_code,
            message=result.message,
            install_hint=result.install_hint,
        )
    temp_output.replace(input_path)
    return VideoReconstructionResult(
        ok=True,
        input_path=input_path,
        output_path=input_path,
        file_size=input_path.stat().st_size,
        reconstructed=True,
        source_kind=result.source_kind or 'readystream_package',
    )


def reconstruct_ready_stream_video(
    input_path: Path,
    *,
    output_path: Path | None = None,
    overwrite: bool = False,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> VideoReconstructionResult:
    input_path = input_path.expanduser()
    output_path = output_path.expanduser() if output_path else _plan_reconstructed_video_path(input_path)
    if not input_path.exists():
        return VideoReconstructionResult(ok=False, input_path=input_path, output_path=output_path, error_code='input_not_found', message='영상 파일을 찾을 수 없습니다.')
    if output_path.exists() and not overwrite:
        return VideoReconstructionResult(ok=True, input_path=input_path, output_path=output_path, file_size=output_path.stat().st_size, reconstructed=True, source_kind='readystream_package')
    if not _is_ready_stream_package(input_path):
        return VideoReconstructionResult(ok=True, input_path=input_path, output_path=input_path, file_size=input_path.stat().st_size, reconstructed=False, source_kind='mp4')
    ffmpeg = bundled_or_system_ffmpeg()
    if not ffmpeg:
        return VideoReconstructionResult(ok=False, input_path=input_path, output_path=output_path, error_code='ffmpeg_not_found', message='ffmpeg를 찾을 수 없습니다.', install_hint=ffmpeg_install_hint())
    ffprobe = _ffprobe_for_ffmpeg(ffmpeg)
    if not ffprobe:
        return VideoReconstructionResult(ok=False, input_path=input_path, output_path=output_path, error_code='ffprobe_not_found', message='ffprobe를 찾을 수 없습니다.')

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
            )
            if inner_video_result is not None:
                return inner_video_result

            unit_name = next((name for name in archive.namelist() if name.startswith('contents/web_files/unitmeta_') and name.endswith('.xml')), None)
            if unit_name is None:
                return VideoReconstructionResult(ok=False, input_path=input_path, output_path=output_path, error_code='readystream_unitmeta_missing', message='ReadyStream 메타데이터를 찾을 수 없습니다.')
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
                return VideoReconstructionResult(ok=False, input_path=input_path, output_path=output_path, error_code='readystream_timeline_missing', message='ReadyStream 슬라이드/오디오 정보를 찾을 수 없습니다.')

            media_member = f'contents/media_files/media/{media_uri}'
            if media_member not in archive.namelist():
                return VideoReconstructionResult(ok=False, input_path=input_path, output_path=output_path, error_code='readystream_audio_missing', message='ReadyStream 내부 오디오를 찾을 수 없습니다.')
            audio_path = temp_path / f'audio{Path(media_member).suffix.lower() or ".m4a"}'
            with archive.open(media_member) as source, audio_path.open('wb') as target:
                shutil.copyfileobj(source, target)

            event_member = f'contents/web_files/{event_uri}'
            if event_member not in archive.namelist():
                return VideoReconstructionResult(ok=False, input_path=input_path, output_path=output_path, error_code='readystream_eventlist_missing', message='ReadyStream 슬라이드 타임라인을 찾을 수 없습니다.')
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
                return VideoReconstructionResult(ok=False, input_path=input_path, output_path=output_path, error_code='readystream_slides_missing', message='ReadyStream 슬라이드 정보를 찾을 수 없습니다.')

            duration_output = subprocess.run(
                [ffprobe, '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(audio_path)],
                capture_output=True,
                text=True,
                check=True,
            )
            total_duration = float(duration_output.stdout.strip())
            frames_dir = temp_path / 'frames'
            frames_dir.mkdir()
            concat_path = temp_path / 'concat.txt'
            entries: list[tuple[Path, float]] = []
            names = set(archive.namelist())
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
                return VideoReconstructionResult(ok=False, input_path=input_path, output_path=output_path, error_code='readystream_thumbnails_missing', message='ReadyStream 슬라이드 이미지를 찾을 수 없습니다.')

            with concat_path.open('w', encoding='utf-8') as concat_file:
                for frame_path, duration in entries:
                    concat_file.write(f"file {str(frame_path)!r}\n")
                    concat_file.write(f"duration {duration:.6f}\n")
                concat_file.write(f"file {str(entries[-1][0])!r}\n")

            output_path.parent.mkdir(parents=True, exist_ok=True)
            _emit_progress(on_progress, {'stage': 'video_reconstruction_started', 'input_path': str(input_path), 'output_path': str(output_path), 'slide_count': len(entries), 'duration_ms': int(total_duration * 1000)})
            command = [
                ffmpeg,
                '-y',
                '-f',
                'concat',
                '-safe',
                '0',
                '-i',
                str(concat_path),
                '-i',
                str(audio_path),
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
            ]
            subprocess.run(command, capture_output=True, text=True, check=True)
            _emit_progress(on_progress, {'stage': 'video_reconstruction_done', 'input_path': str(input_path), 'output_path': str(output_path), 'file_size': output_path.stat().st_size, 'progress_percent': 100})
    except (zipfile.BadZipFile, ET.ParseError, StopIteration, ValueError, subprocess.CalledProcessError) as exc:
        message = 'ReadyStream 영상을 MP4로 재구성하지 못했습니다.'
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or exc.stdout or '').strip().splitlines()[-1:]
            if detail:
                message = detail[0][:500]
        return VideoReconstructionResult(ok=False, input_path=input_path, output_path=output_path, error_code='video_reconstruction_failed', message=message)
    if not output_path.exists():
        return VideoReconstructionResult(ok=False, input_path=input_path, output_path=output_path, error_code='video_reconstruction_output_missing', message='MP4 재구성 후 파일이 생성되지 않았습니다.')
    return VideoReconstructionResult(ok=True, input_path=input_path, output_path=output_path, file_size=output_path.stat().st_size, reconstructed=True, source_kind='readystream_package')


def extract_audio(request: AudioExtractionRequest, on_progress: Callable[[dict[str, Any]], None] | None = None) -> AudioExtractionResult:
    input_path = request.input_path.expanduser()
    output_path = plan_audio_output_path(input_path, request.output_dir, request.format)
    if not input_path.exists():
        return AudioExtractionResult(ok=False, input_path=input_path, output_path=output_path, error_code='input_not_found', message='영상 파일을 찾을 수 없습니다.')
    if not input_path.is_file():
        return AudioExtractionResult(ok=False, input_path=input_path, output_path=output_path, error_code='input_not_file', message='입력 경로가 파일이 아닙니다.')
    if input_path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
        return AudioExtractionResult(ok=False, input_path=input_path, output_path=output_path, error_code='unsupported_video_format', message='지원하지 않는 영상 파일 형식입니다.')
    ffmpeg = bundled_or_system_ffmpeg()
    if not ffmpeg:
        return AudioExtractionResult(ok=False, input_path=input_path, output_path=output_path, error_code='ffmpeg_not_found', message='ffmpeg를 찾을 수 없습니다.', install_hint=ffmpeg_install_hint())
    if output_path.exists() and not request.overwrite:
        return AudioExtractionResult(ok=False, input_path=input_path, output_path=output_path, error_code='audio_exists', message='이미 추출된 음성 파일이 있습니다.')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _extractable_media_input(input_path) as ffmpeg_input_path:
            if on_progress:
                duration_ms = _media_duration_ms(ffmpeg, ffmpeg_input_path)
                _emit_progress(on_progress, {'stage': 'audio_probe_done', 'input_path': str(input_path), 'duration_ms': duration_ms})
                command = _ffmpeg_progress_command(ffmpeg, ffmpeg_input_path, output_path, request.format)
                _run_ffmpeg_with_progress(command, input_path=input_path, output_path=output_path, duration_ms=duration_ms, on_progress=on_progress)
            else:
                command = _ffmpeg_command(ffmpeg, ffmpeg_input_path, output_path, request.format)
                subprocess.run(command, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or '').strip().splitlines()[-1:] or ['ffmpeg 실행에 실패했습니다.']
        return AudioExtractionResult(ok=False, input_path=input_path, output_path=output_path, error_code='ffmpeg_failed', message=detail[0][:500])
    if not output_path.exists():
        return AudioExtractionResult(ok=False, input_path=input_path, output_path=output_path, error_code='audio_output_missing', message='ffmpeg 실행 후 음성 파일이 생성되지 않았습니다.')
    return AudioExtractionResult(ok=True, input_path=input_path, output_path=output_path, file_size=output_path.stat().st_size)

