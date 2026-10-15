from __future__ import annotations

from pathlib import Path

import pytest

from ssu_lecture_downloader.stt import (
    DEFAULT_STT_BEAM_SIZE,
    DEFAULT_STT_INITIAL_PROMPT,
    DEFAULT_STT_MODEL,
    STTProviderStatus,
    TranscriptSegment,
    TranscriptionRequest,
    check_stt_provider,
    transcribe_audio,
    write_transcript_files,
)


def test_check_stt_provider_returns_install_help_when_whisper_missing(monkeypatch):
    monkeypatch.setattr('ssu_lecture_downloader.stt._has_faster_whisper', lambda: False)
    monkeypatch.setattr('ssu_lecture_downloader.stt.shutil.which', lambda name: None)
    monkeypatch.setattr('ssu_lecture_downloader.stt.faster_whisper_platform_supported', lambda: True)

    status = check_stt_provider('faster_whisper')

    assert status == STTProviderStatus(
        provider='faster_whisper',
        installed=False,
        available=False,
        install_hint='앱 패키지에 포함된 faster-whisper를 찾지 못했습니다. 개발 모드에서는 uv sync 후 다시 실행하고, 배포 앱에서는 앱을 재설치해 주세요.',
        recommended_model=DEFAULT_STT_MODEL,
        quality_note='한국어 강의 정확도를 우선해 Whisper large-v3를 기본값으로 사용합니다. 첫 실행 시 모델 다운로드/준비 시간이 길 수 있습니다.',
    )


def test_transcription_request_defaults_prioritize_korean_accuracy(tmp_path: Path):
    request = TranscriptionRequest(audio_path=tmp_path / 'lecture.wav')

    assert request.provider == 'faster_whisper'
    assert request.language == 'ko'
    assert request.model == DEFAULT_STT_MODEL
    assert request.beam_size == DEFAULT_STT_BEAM_SIZE
    assert request.vad_filter is True
    assert request.condition_on_previous_text is False
    assert request.initial_prompt == DEFAULT_STT_INITIAL_PROMPT


def test_write_transcript_files_creates_txt_md_json(tmp_path: Path):
    audio = tmp_path / 'lecture.wav'
    audio.write_bytes(b'audio')
    segments = [
        TranscriptSegment(start=0, end=2.5, text='안녕하세요.'),
        TranscriptSegment(start=62, end=65, text='UX 방법론을 설명합니다.'),
    ]

    result = write_transcript_files(
        audio_path=audio,
        segments=segments,
        output_dir=None,
        provider='fake',
        model='test-model',
        language='ko',
        overwrite=False,
    )

    assert result.ok is True
    assert result.text_path == tmp_path / 'lecture.transcript.txt'
    assert result.md_path == tmp_path / 'lecture.transcript.md'
    assert result.json_path == tmp_path / 'lecture.transcript.json'
    assert '[00:00:00] 안녕하세요.' in result.text_path.read_text()
    assert '## Transcript' in result.md_path.read_text()
    assert 'UX 방법론을 설명합니다.' in result.json_path.read_text()


def test_transcribe_audio_fake_provider_writes_transcript(tmp_path: Path):
    audio = tmp_path / 'lecture.wav'
    audio.write_bytes(b'audio')

    result = transcribe_audio(TranscriptionRequest(audio_path=audio, provider='fake', overwrite=True))

    assert result.ok is True
    assert result.text_path is not None
    assert result.text_path.exists()
    assert result.segments


def test_transcribe_audio_fake_provider_emits_progress(tmp_path: Path):
    audio = tmp_path / 'lecture.wav'
    audio.write_bytes(b'audio')
    events = []

    result = transcribe_audio(TranscriptionRequest(audio_path=audio, provider='fake', overwrite=True), on_progress=events.append)

    assert result.ok is True
    assert [event['stage'] for event in events] == ['transcript_started', 'transcript_progress']
    assert events[-1]['progress_percent'] == 100
    assert events[-1]['segment_count'] == 1


def test_transcribe_audio_returns_install_help_for_missing_whisper(tmp_path: Path, monkeypatch):
    audio = tmp_path / 'lecture.wav'
    audio.write_bytes(b'audio')
    monkeypatch.setattr('ssu_lecture_downloader.stt._has_faster_whisper', lambda: False)
    monkeypatch.setattr('ssu_lecture_downloader.stt.faster_whisper_platform_supported', lambda: True)

    result = transcribe_audio(TranscriptionRequest(audio_path=audio, provider='faster_whisper'))

    assert result.ok is False
    assert result.error_code == 'stt_provider_not_installed'
    assert result.install_hint is not None
    assert '앱 패키지' in result.install_hint


class FakeWhisperSegment:
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


def install_fake_faster_whisper(monkeypatch, segments, constructed):
    import sys
    import types

    from ssu_lecture_downloader import stt

    class FakeWhisperModel:
        def __init__(self, size, device='auto', compute_type='auto'):
            constructed.append((size, device, compute_type))

        def transcribe(self, path, **kwargs):
            return iter(segments), types.SimpleNamespace(duration=10.0)

    module = types.ModuleType('faster_whisper')
    module.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, 'faster_whisper', module)
    monkeypatch.setattr(stt, '_has_faster_whisper', lambda: True)
    stt.clear_whisper_model_cache()


def test_faster_whisper_model_is_loaded_once_per_size_device_and_compute_type(tmp_path: Path, monkeypatch):
    from ssu_lecture_downloader import stt

    constructed = []
    install_fake_faster_whisper(monkeypatch, [FakeWhisperSegment(0, 2, '안녕')], constructed)
    audio = tmp_path / 'a.wav'
    audio.write_bytes(b'audio')
    other = tmp_path / 'b.wav'
    other.write_bytes(b'audio')

    try:
        assert transcribe_audio(TranscriptionRequest(audio_path=audio, overwrite=True)).ok is True
        assert transcribe_audio(TranscriptionRequest(audio_path=other, overwrite=True)).ok is True
        assert constructed == [('large-v3', 'auto', 'auto')]
        assert transcribe_audio(TranscriptionRequest(audio_path=audio, model='small', overwrite=True)).ok is True
        assert constructed == [('large-v3', 'auto', 'auto'), ('small', 'auto', 'auto')]
    finally:
        stt.clear_whisper_model_cache()


def test_faster_whisper_segment_loop_stops_when_cancel_is_requested(tmp_path: Path, monkeypatch):
    from ssu_lecture_downloader import stt

    constructed = []
    install_fake_faster_whisper(monkeypatch, [FakeWhisperSegment(0, 1, 'one'), FakeWhisperSegment(1, 2, 'two'), FakeWhisperSegment(2, 3, 'three')], constructed)
    audio = tmp_path / 'a.wav'
    audio.write_bytes(b'audio')
    checks = {'count': 0}

    def should_cancel():
        checks['count'] += 1
        return checks['count'] > 2

    try:
        result = transcribe_audio(TranscriptionRequest(audio_path=audio, overwrite=True), should_cancel=should_cancel)
    finally:
        stt.clear_whisper_model_cache()

    assert result.ok is False
    assert result.error_code == 'cancelled'
    assert not any(tmp_path.glob('*.transcript.*'))


def test_write_transcript_files_leaves_no_partial_files_when_a_write_fails(tmp_path: Path, monkeypatch):
    from ssu_lecture_downloader import stt

    audio = tmp_path / 'lecture.wav'
    audio.write_bytes(b'audio')
    real_replace = stt.os.replace
    calls = {'count': 0}

    def flaky_replace(src, dst):
        calls['count'] += 1
        if calls['count'] == 2:
            raise OSError('disk full')
        return real_replace(src, dst)

    monkeypatch.setattr(stt.os, 'replace', flaky_replace)

    with pytest.raises(OSError):
        write_transcript_files(audio_path=audio, segments=[TranscriptSegment(start=0, end=1, text='hi')], output_dir=None, provider='fake', model='m', language='ko', overwrite=False)

    # The .txt file is committed last, so an interrupted write never looks complete.
    assert not (tmp_path / 'lecture.transcript.txt').exists()
    assert not list(tmp_path.glob('*.part'))


def test_faster_whisper_reports_intel_mac_as_unsupported(monkeypatch):
    from ssu_lecture_downloader import stt

    monkeypatch.setattr(stt, '_has_faster_whisper', lambda: False)
    monkeypatch.setattr(stt.sys, 'platform', 'darwin')
    monkeypatch.setattr(stt.platform, 'machine', lambda: 'x86_64')
    status = stt.check_stt_provider('faster_whisper')
    assert status.available is False
    assert status.platform_supported is False
    assert 'Intel' in (status.install_hint or '')

    monkeypatch.setattr(stt.platform, 'machine', lambda: 'arm64')
    assert stt.check_stt_provider('faster_whisper').platform_supported is True
