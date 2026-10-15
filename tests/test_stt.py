from __future__ import annotations

from pathlib import Path

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

    result = transcribe_audio(TranscriptionRequest(audio_path=audio, provider='faster_whisper'))

    assert result.ok is False
    assert result.error_code == 'stt_provider_not_installed'
    assert result.install_hint is not None
    assert '앱 패키지' in result.install_hint
