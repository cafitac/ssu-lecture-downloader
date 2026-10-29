from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

from ssu_lecture_downloader.core.progress import emit_progress
from ssu_lecture_downloader.core.stt_cues import TranscriptSegment, format_srt

if TYPE_CHECKING:
    from ssu_lecture_downloader.core.stt_remote import RemoteSTTService

STTProvider = Literal['fake', 'faster_whisper', 'whisper_cpp']
DEFAULT_STT_LANGUAGE = 'ko'
DEFAULT_STT_MODEL = 'large-v3'
DEFAULT_STT_BEAM_SIZE = 5
DEFAULT_STT_INITIAL_PROMPT = '다음은 숭실대학교 온라인 강의의 한국어 음성입니다. 강의 내용, 전공 용어, 숫자와 고유명사를 가능한 한 정확하게 받아쓰기합니다.'


class STTProviderStatus(BaseModel):
    provider: str
    installed: bool
    available: bool
    install_hint: str | None = None
    recommended_model: str | None = None
    quality_note: str | None = None
    # False when this machine cannot run the provider at all (no install can fix it).
    platform_supported: bool = True


class TranscriptionRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    audio_path: Path
    output_dir: Path | None = None
    provider: STTProvider = 'faster_whisper'
    language: str = DEFAULT_STT_LANGUAGE
    model: str = DEFAULT_STT_MODEL
    beam_size: int = DEFAULT_STT_BEAM_SIZE
    vad_filter: bool = True
    condition_on_previous_text: bool = False
    initial_prompt: str | None = DEFAULT_STT_INITIAL_PROMPT
    overwrite: bool = False


class TranscriptionResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    ok: bool
    audio_path: Path
    text_path: Path | None = None
    md_path: Path | None = None
    json_path: Path | None = None
    srt_path: Path | None = None
    segments: list[TranscriptSegment] = []
    error_code: str | None = None
    message: str | None = None
    install_hint: str | None = None
    # Non-fatal quality notes (for example a long silent stretch in the subtitles).
    warnings: list[str] = []


def faster_whisper_install_hint() -> str:
    return '앱 패키지에 포함된 faster-whisper를 찾지 못했습니다. 개발 모드에서는 uv sync 후 다시 실행하고, 배포 앱에서는 앱을 재설치해 주세요.'


def whisper_cpp_install_hint() -> str:
    return '앱 패키지에 포함된 whisper.cpp CLI를 찾지 못했습니다. 개발 모드에서는 별도 실험 기능으로만 설정하고, 배포 앱에서는 앱을 재설치해 주세요.'


def faster_whisper_platform_supported() -> bool:
    # faster-whisper's CTranslate2 wheels are not published for Intel Macs (see pyproject markers).
    return not (sys.platform == 'darwin' and platform.machine() == 'x86_64')


def _has_faster_whisper() -> bool:
    return importlib.util.find_spec('faster_whisper') is not None


def check_stt_provider(provider: str = 'faster_whisper') -> STTProviderStatus:
    if provider == 'fake':
        return STTProviderStatus(provider='fake', installed=True, available=True, recommended_model='test-only')
    if provider == 'faster_whisper':
        installed = _has_faster_whisper()
        supported = installed or faster_whisper_platform_supported()
        return STTProviderStatus(
            provider='faster_whisper',
            installed=installed,
            available=installed,
            install_hint=None if installed else (faster_whisper_install_hint() if supported else '이 Mac(Intel)에서는 자막 생성을 지원하지 않아요. Apple 실리콘 Mac에서 사용할 수 있어요.'),
            platform_supported=supported,
            recommended_model=DEFAULT_STT_MODEL,
            quality_note='한국어 강의 정확도를 우선해 Whisper large-v3를 기본값으로 사용합니다. 첫 실행 시 모델 다운로드/준비 시간이 길 수 있습니다.',
        )
    if provider == 'whisper_cpp':
        binary = shutil.which('whisper-cli') or shutil.which('whisper-cpp') or shutil.which('main')
        installed = binary is not None
        return STTProviderStatus(provider='whisper_cpp', installed=installed, available=installed, install_hint=None if installed else whisper_cpp_install_hint(), recommended_model=DEFAULT_STT_MODEL)
    return STTProviderStatus(provider=provider, installed=False, available=False, install_hint='지원하지 않는 STT provider입니다.')


def _format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f'{hours:02d}:{minutes:02d}:{secs:02d}'


def transcript_output_paths(audio_path: Path, output_dir: Path | None) -> tuple[Path, Path, Path]:
    base_dir = output_dir.expanduser() if output_dir else audio_path.expanduser().parent
    stem = audio_path.stem
    return (
        base_dir / f'{stem}.transcript.txt',
        base_dir / f'{stem}.transcript.md',
        base_dir / f'{stem}.transcript.json',
    )


def transcript_srt_path(audio_path: Path, output_dir: Path | None) -> Path:
    """`<name>.srt` next to the video/audio, which is where players pick subtitles up automatically."""
    base_dir = output_dir.expanduser() if output_dir else audio_path.expanduser().parent
    return base_dir / f'{audio_path.stem}.srt'


def write_transcript_files(
    *,
    audio_path: Path,
    segments: list[TranscriptSegment],
    output_dir: Path | None,
    provider: str,
    model: str,
    language: str,
    overwrite: bool,
) -> TranscriptionResult:
    audio_path = audio_path.expanduser()
    text_path, md_path, json_path = transcript_output_paths(audio_path, output_dir)
    srt_path = transcript_srt_path(audio_path, output_dir)
    existing = [path for path in (text_path, md_path, json_path) if path.exists()]
    if existing and not overwrite:
        return TranscriptionResult(ok=False, audio_path=audio_path, text_path=text_path, md_path=md_path, json_path=json_path, srt_path=srt_path, segments=segments, error_code='transcript_exists', message='이미 생성된 STT 파일이 있습니다.')
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text = '\n'.join(f'[{_format_timestamp(segment.start)}] {segment.text.strip()}' for segment in segments if segment.text.strip()).strip() + '\n'
    md = '\n'.join([
        f'# {audio_path.stem} transcript',
        '',
        f'- Source audio: `{audio_path.name}`',
        f'- Provider: `{provider}`',
        f'- Model: `{model}`',
        f'- Language: `{language}`',
        '',
        '## Transcript',
        '',
        *[f'### {_format_timestamp(segment.start)}\n{segment.text.strip()}\n' for segment in segments if segment.text.strip()],
    ])
    payload = {
        'audio_path': str(audio_path),
        'provider': provider,
        'model': model,
        'language': language,
        'segments': [segment.model_dump() for segment in segments],
    }
    # Each file is written to a temp sibling and swapped in with os.replace. The
    # user-facing .txt goes last, so its presence means the whole set is complete.
    _write_text_atomically(srt_path, format_srt(segments))
    _write_text_atomically(md_path, md)
    _write_text_atomically(json_path, json.dumps(payload, ensure_ascii=False, indent=2))
    _write_text_atomically(text_path, text)
    return TranscriptionResult(ok=True, audio_path=audio_path, text_path=text_path, md_path=md_path, json_path=json_path, srt_path=srt_path, segments=segments)


def _write_text_atomically(path: Path, content: str) -> None:
    partial = path.with_name(f'{path.name}.part')
    try:
        partial.write_text(content, encoding='utf-8')
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def _fake_segments(audio_path: Path) -> list[TranscriptSegment]:
    return [TranscriptSegment(start=0, end=1, text=f'{audio_path.stem} 음성 파일의 STT 테스트 결과입니다.')]


class TranscriptionCancelled(Exception):
    """Raised inside the segment loop when the caller asked to stop."""


_WHISPER_MODELS: dict[tuple[str, str, str], Any] = {}
_WHISPER_MODELS_LOCK = threading.Lock()


def _load_whisper_model(model_size: str, device: str = 'auto', compute_type: str = 'auto') -> Any:
    """Load a WhisperModel once per (size, device, compute_type) for the process lifetime."""
    key = (model_size, device, compute_type)
    with _WHISPER_MODELS_LOCK:
        model = _WHISPER_MODELS.get(key)
        if model is None:
            from faster_whisper import WhisperModel

            model = WhisperModel(model_size, device=device, compute_type=compute_type)
            _WHISPER_MODELS[key] = model
        return model


def clear_whisper_model_cache() -> None:
    with _WHISPER_MODELS_LOCK:
        _WHISPER_MODELS.clear()


def _faster_whisper_segments(
    request: TranscriptionRequest,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[TranscriptSegment]:
    try:
        import faster_whisper  # noqa: F401 - fail early with a stable error code
    except Exception as exc:  # pragma: no cover - guarded by provider check in normal path
        raise RuntimeError('faster_whisper_import_failed') from exc
    emit_progress(on_progress, {'stage': 'transcript_model_loading', 'provider': request.provider, 'model': request.model, 'audio_path': str(request.audio_path)})
    model = _load_whisper_model(request.model, 'auto', 'auto')
    if should_cancel and should_cancel():
        raise TranscriptionCancelled('transcription cancelled')
    segments, _info = model.transcribe(
        str(request.audio_path),
        language=request.language,
        beam_size=request.beam_size,
        vad_filter=request.vad_filter,
        condition_on_previous_text=request.condition_on_previous_text,
        initial_prompt=request.initial_prompt,
        temperature=0.0,
    )
    duration = float(getattr(_info, 'duration', 0) or 0)
    emit_progress(on_progress, {'stage': 'transcript_started', 'provider': request.provider, 'model': request.model, 'audio_path': str(request.audio_path), 'duration': duration})
    collected: list[TranscriptSegment] = []
    last_percent = -1
    for segment in segments:
        if should_cancel and should_cancel():
            raise TranscriptionCancelled('transcription cancelled')
        item = TranscriptSegment(start=float(segment.start), end=float(segment.end), text=segment.text.strip())
        collected.append(item)
        percent = None
        if duration > 0:
            percent = min(99, max(0, round((item.end / duration) * 100)))
        if percent is None or percent != last_percent:
            emit_progress(
                on_progress,
                {
                    'stage': 'transcript_progress',
                    'provider': request.provider,
                    'model': request.model,
                    'audio_path': str(request.audio_path),
                    'segment_count': len(collected),
                    'elapsed_seconds': item.end,
                    'duration': duration,
                    'progress_percent': percent,
                },
            )
            if percent is not None:
                last_percent = percent
    emit_progress(on_progress, {'stage': 'transcript_progress', 'provider': request.provider, 'model': request.model, 'audio_path': str(request.audio_path), 'segment_count': len(collected), 'duration': duration, 'progress_percent': 100})
    return collected


def transcribe_audio(
    request: TranscriptionRequest,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    remote: RemoteSTTService | None = None,
) -> TranscriptionResult:
    audio_path = request.audio_path.expanduser()
    if not audio_path.exists():
        return TranscriptionResult(ok=False, audio_path=audio_path, error_code='audio_not_found', message='음성 파일을 찾을 수 없습니다.')
    if not audio_path.is_file():
        return TranscriptionResult(ok=False, audio_path=audio_path, error_code='audio_not_file', message='입력 경로가 파일이 아닙니다.')
    if remote is not None and request.provider != 'fake' and remote.is_enabled():
        # A configured remote server wins over the local engine (which may not even run on this Mac).
        from ssu_lecture_downloader.core.stt_remote import transcribe_with_remote

        return transcribe_with_remote(request.model_copy(update={'audio_path': audio_path}), remote, on_progress=on_progress, should_cancel=should_cancel)
    status = check_stt_provider(request.provider)
    if not status.available:
        return TranscriptionResult(ok=False, audio_path=audio_path, error_code='stt_provider_not_installed', message='STT 엔진이 설치되어 있지 않습니다.', install_hint=status.install_hint)
    try:
        if request.provider == 'fake':
            emit_progress(on_progress, {'stage': 'transcript_started', 'provider': request.provider, 'model': request.model, 'audio_path': str(audio_path), 'duration': 1})
            segments = _fake_segments(audio_path)
            emit_progress(on_progress, {'stage': 'transcript_progress', 'provider': request.provider, 'model': request.model, 'audio_path': str(audio_path), 'segment_count': len(segments), 'duration': 1, 'progress_percent': 100})
        elif request.provider == 'faster_whisper':
            segments = _faster_whisper_segments(request, on_progress=on_progress, should_cancel=should_cancel)
        else:
            return TranscriptionResult(ok=False, audio_path=audio_path, error_code='stt_provider_not_implemented', message='이 STT provider는 아직 앱에서 실행하지 않습니다.', install_hint=status.install_hint)
    except TranscriptionCancelled:
        return TranscriptionResult(ok=False, audio_path=audio_path, error_code='cancelled', message='작업이 취소되었습니다.')
    except Exception as exc:
        return TranscriptionResult(ok=False, audio_path=audio_path, error_code=type(exc).__name__, message=str(exc)[:500])
    try:
        return write_transcript_files(audio_path=audio_path, segments=segments, output_dir=request.output_dir, provider=request.provider, model=request.model, language=request.language, overwrite=request.overwrite)
    except OSError as exc:
        return TranscriptionResult(ok=False, audio_path=audio_path, segments=segments, error_code='transcript_write_failed', message=str(exc)[:500])
