from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import io
import sys
import threading
import time

import pytest

from ssu_lecture_downloader import postprocess_media
from ssu_lecture_downloader.media import AudioExtractionRequest, extract_audio, plan_audio_output_path
from ssu_lecture_downloader.postprocess_media import MediaCancelled, _run_ffmpeg, ensure_playable_mp4, reconstruct_ready_stream_video


def input_of(command):
    return Path(command[command.index('-i') + 1])


def install_fake_ffmpeg(monkeypatch, handler):
    """Replace ffmpeg processes: handler(command) -> (returncode, stdout, stderr)."""
    launched = []

    class FakeProcess:
        def __init__(self, command, *, stdout, stderr, text):
            launched.append(command)
            self.returncode, out, err = handler(command)
            self.stdout = io.StringIO(out)
            self.stderr = io.StringIO(err)

        def wait(self):
            return self.returncode

        def kill(self):
            pass

    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.subprocess.Popen', FakeProcess)
    return launched


def test_plan_audio_output_path_uses_transcripts_directory(tmp_path: Path):
    video = tmp_path / 'UX 1주차.mp4'
    video.write_bytes(b'video')

    output = plan_audio_output_path(video, None, 'wav')

    assert output == tmp_path / 'transcripts' / 'UX 1주차.wav'


def test_extract_audio_reports_missing_ffmpeg(tmp_path: Path, monkeypatch):
    video = tmp_path / 'lecture.mp4'
    video.write_bytes(b'video')
    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.bundled_or_system_ffmpeg', lambda: None)

    result = extract_audio(AudioExtractionRequest(input_path=video))

    assert result.ok is False
    assert result.error_code == 'ffmpeg_not_found'
    assert result.install_hint is not None
    assert '앱 패키지' in result.install_hint


def test_extract_audio_runs_ffmpeg_and_writes_output(tmp_path: Path, monkeypatch):
    video = tmp_path / 'lecture.mp4'
    video.write_bytes(b'video')
    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.shutil.which', lambda name: '/opt/homebrew/bin/ffmpeg')

    def handler(command):
        output_path = Path(command[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b'audio')
        return 0, '', ''

    launched = install_fake_ffmpeg(monkeypatch, handler)

    result = extract_audio(AudioExtractionRequest(input_path=video, format='wav'))

    assert result.ok is True
    assert result.output_path == tmp_path / 'transcripts' / 'lecture.wav'
    assert result.file_size == 5
    command = launched[0]
    assert command[0] == '/opt/homebrew/bin/ffmpeg'
    assert command[1:5] == ['-y', '-nostdin', '-loglevel', 'error']
    assert input_of(command) == video
    # ffmpeg never targets the final path directly.
    assert Path(command[-1]) != result.output_path
    assert sorted(path.name for path in result.output_path.parent.iterdir()) == ['lecture.wav']


def test_extract_audio_emits_ffmpeg_progress_percent(tmp_path: Path, monkeypatch):
    video = tmp_path / 'lecture.mp4'
    video.write_bytes(b'video')
    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.shutil.which', lambda name: f'/opt/homebrew/bin/{name}')

    def fake_run(command, *, capture_output, text, check, timeout=None):
        assert command[0].endswith('ffprobe')
        return subprocess.CompletedProcess(command, 0, stdout='10.0\n', stderr='')

    def handler(command):
        assert '-progress' in command
        output_path = Path(command[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b'audio')
        return 0, 'out_time_ms=2500000\nprogress=continue\nout_time_ms=10000000\nprogress=end\n', ''

    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.subprocess.run', fake_run)
    install_fake_ffmpeg(monkeypatch, handler)
    events = []

    result = extract_audio(AudioExtractionRequest(input_path=video, format='mp3'), on_progress=events.append)

    assert result.ok is True
    progress_events = [event for event in events if event['stage'] == 'audio_progress']
    assert progress_events[0]['progress_percent'] == 25
    assert progress_events[-1]['progress_percent'] == 100
    assert progress_events[-1]['duration_ms'] == 10000
    assert progress_events[0]['output_path'] == str(result.output_path)


def test_extract_audio_uses_inner_media_from_readystream_package(tmp_path: Path, monkeypatch):
    package = tmp_path / 'readystream.mp4'
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr('AppManifest.xaml', '<Deployment />')
        archive.writestr('contents/media_root.xml', '<media_root />')
        archive.writestr('contents/media_files/media/(small).mp4', b'small')
        archive.writestr('contents/media_files/media/(large).mp4', b'large media bytes')
    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.shutil.which', lambda name: '/opt/homebrew/bin/ffmpeg')
    recorded = {}

    def handler(command):
        recorded['command'] = command
        ffmpeg_input = input_of(command)
        assert ffmpeg_input.name == 'readystream.mp4'
        assert ffmpeg_input.read_bytes() == b'large media bytes'
        output_path = Path(command[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b'audio')
        return 0, '', ''

    install_fake_ffmpeg(monkeypatch, handler)

    result = extract_audio(AudioExtractionRequest(input_path=package, format='mp3'))

    assert result.ok is True
    assert result.output_path == tmp_path / 'transcripts' / 'readystream.mp3'
    assert input_of(recorded['command']) != package


def test_reconstruct_ready_stream_video_extracts_inner_video_before_slide_rebuild(tmp_path: Path, monkeypatch):
    package = tmp_path / 'readystream.mp4'
    inner_video = b'inner playable mp4 bytes'
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr('AppManifest.xaml', '<Deployment />')
        archive.writestr('contents/media_root.xml', '<media_root />')
        archive.writestr('contents/media_files/media/(video).mp4', inner_video)
        archive.writestr(
            'contents/web_files/unitmeta_(unit).xml',
            '''<?xml version="1.0" encoding="utf-8"?>
<unit_meta><part_list>
  <part name="EventListUri" uri="eventlist_(unit).xml" />
  <part name="MediaUri" uri="(video).mp4" />
</part_list></unit_meta>''',
        )
    output = tmp_path / 'out.mp4'
    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.bundled_or_system_ffmpeg', lambda: '/opt/ffmpeg')
    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media._ffprobe_for_ffmpeg', lambda ffmpeg: '/opt/ffprobe')

    def fake_run(command, *, capture_output, text, check, timeout=None):
        assert command[0] == '/opt/ffprobe'
        return subprocess.CompletedProcess(command, 0, stdout='{"streams":[{"codec_type":"video"},{"codec_type":"audio"}]}', stderr='')

    def handler(command):
        if command[:3] == ['/opt/ffmpeg', '-v', 'error'] and command[-2:] == ['null', '-']:
            return 0, '', ''
        Path(command[-1]).write_bytes(inner_video)
        return 0, '', ''

    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.subprocess.run', fake_run)
    install_fake_ffmpeg(monkeypatch, handler)

    result = reconstruct_ready_stream_video(package, output_path=output, overwrite=True)

    assert result.ok is True
    assert result.output_path == output
    assert output.read_bytes() == inner_video
    assert result.source_kind == 'readystream_inner_video'


def test_reconstruct_ready_stream_video_normalizes_inner_video_when_decode_warnings_exist(tmp_path: Path, monkeypatch):
    package = tmp_path / 'readystream.mp4'
    inner_video = b'inner playable mp4 bytes'
    normalized_video = b'normalized playable mp4 bytes'
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr('AppManifest.xaml', '<Deployment />')
        archive.writestr('contents/media_root.xml', '<media_root />')
        archive.writestr('contents/media_files/media/(video).mp4', inner_video)
    output = tmp_path / 'out.mp4'
    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.bundled_or_system_ffmpeg', lambda: '/opt/ffmpeg')
    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media._ffprobe_for_ffmpeg', lambda ffmpeg: '/opt/ffprobe')

    def fake_run(command, *, capture_output, text, check, timeout=None):
        return subprocess.CompletedProcess(command, 0, stdout='{"streams":[{"codec_type":"video"}]}', stderr='')

    def handler(command):
        if command[:3] == ['/opt/ffmpeg', '-v', 'error'] and command[-2:] == ['null', '-']:
            return 0, '', 'non monotonically increasing dts'
        output_path = Path(command[-1])
        output_path.write_bytes(normalized_video if '-vf' in command else inner_video)
        return 0, '', ''

    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.subprocess.run', fake_run)
    launched = install_fake_ffmpeg(monkeypatch, handler)

    result = reconstruct_ready_stream_video(package, output_path=output, overwrite=True)

    assert result.ok is True
    assert output.read_bytes() == normalized_video
    assert result.source_kind == 'readystream_inner_video_normalized'
    assert any('-vf' in command for command in launched)


def test_reconstruct_ready_stream_video_uses_slides_audio_and_timeline(tmp_path: Path, monkeypatch):
    package = tmp_path / 'readystream.mp4'
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr('AppManifest.xaml', '<Deployment />')
        archive.writestr('contents/media_root.xml', '<media_root />')
        archive.writestr(
            'contents/web_files/unitmeta_(unit).xml',
            '''<?xml version="1.0" encoding="utf-8"?>
<unit_meta><part_list>
  <part name="EventListUri" uri="eventlist_(unit).xml" />
  <part name="MediaUri" uri="(audio).m4a" />
</part_list></unit_meta>''',
        )
        archive.writestr(
            'contents/web_files/eventlist_(unit).xml',
            '''<?xml version="1.0" encoding="utf-8"?>
<event_list><slide_ref_list>
  <slide_ref sca_idref="slide_(one)" time="0.00"><slide_inner_event_list /></slide_ref>
  <slide_ref sca_idref="slide_(two)" time="5.00"><slide_inner_event_list /></slide_ref>
</slide_ref_list></event_list>''',
        )
        archive.writestr('contents/media_files/media/(audio).m4a', b'audio bytes')
        archive.writestr('contents/web_files/navi_thumbnail/large/slide_(one).jpg', b'jpeg one')
        archive.writestr('contents/web_files/navi_thumbnail/large/slide_(two).jpg', b'jpeg two')
    output = tmp_path / 'out.mp4'
    recorded = {}
    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.bundled_or_system_ffmpeg', lambda: '/opt/ffmpeg')
    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media._ffprobe_for_ffmpeg', lambda ffmpeg: '/opt/ffprobe')

    def fake_run(command, *, capture_output, text, check, timeout=None):
        assert command[0] == '/opt/ffprobe'
        assert Path(command[-1]).read_bytes() == b'audio bytes'
        return subprocess.CompletedProcess(command, 0, stdout='10.0\n', stderr='')

    def handler(command):
        recorded['command'] = command
        concat_text = input_of(command).read_text()
        assert 'duration 5.000000' in concat_text
        assert concat_text.count('file ') == 3
        Path(command[-1]).write_bytes(b'playable mp4')
        return 0, '', ''

    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.subprocess.run', fake_run)
    install_fake_ffmpeg(monkeypatch, handler)

    result = reconstruct_ready_stream_video(package, output_path=output, overwrite=True)

    assert result.ok is True
    assert result.output_path == output
    assert result.file_size == len(b'playable mp4')
    assert result.reconstructed is True
    assert result.source_kind == 'readystream_package'
    assert recorded['command'][0] == '/opt/ffmpeg'


def test_ensure_playable_mp4_replaces_readystream_package_with_backup(tmp_path: Path, monkeypatch):
    package = tmp_path / 'lecture.mp4'
    package.write_bytes(b'PK fake ready stream')
    reconstructed = tmp_path / 'lecture.__reconstructed_tmp__.mp4'

    def fake_is_ready_stream(path: Path):
        return path.name in {'lecture.mp4', 'lecture.readystream-package.zip'} and path.read_bytes().startswith(b'PK')

    def fake_reconstruct(input_path, *, output_path, overwrite, on_progress=None):
        from ssu_lecture_downloader.postprocess_media import VideoReconstructionResult
        assert input_path == tmp_path / 'lecture.readystream-package.zip'
        assert output_path == reconstructed
        output_path.write_bytes(b'real mp4')
        return VideoReconstructionResult(ok=True, input_path=input_path, output_path=output_path, file_size=8, reconstructed=True, source_kind='readystream_package')

    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media._is_ready_stream_package', fake_is_ready_stream)
    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.reconstruct_ready_stream_video', fake_reconstruct)

    result = ensure_playable_mp4(package)

    assert result.ok is True
    assert result.output_path == package
    assert package.read_bytes() == b'real mp4'
    assert (tmp_path / 'lecture.readystream-package.zip').read_bytes() == b'PK fake ready stream'


def test_extract_audio_does_not_overwrite_existing_output(tmp_path: Path, monkeypatch):
    video = tmp_path / 'lecture.mp4'
    video.write_bytes(b'video')
    output = tmp_path / 'transcripts' / 'lecture.wav'
    output.parent.mkdir()
    output.write_bytes(b'existing')
    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.shutil.which', lambda name: '/usr/bin/ffmpeg')

    result = extract_audio(AudioExtractionRequest(input_path=video, overwrite=False))

    assert result.ok is False
    assert result.error_code == 'audio_exists'
    assert result.output_path == output


def run_with_deadline(target, seconds=15):
    outcome = {}

    def runner():
        try:
            outcome['value'] = target()
        except BaseException as exc:  # noqa: BLE001 - reported to the asserting test
            outcome['error'] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), 'ffmpeg runner deadlocked'
    return outcome


def test_run_ffmpeg_drains_stderr_while_reading_stdout_without_deadlock():
    script = (
        'import sys\n'
        'sys.stderr.write("e" * 2_000_000)\n'
        'sys.stderr.flush()\n'
        'for _ in range(2000):\n'
        '    sys.stdout.write("out_time_ms=1\\n")\n'
        'sys.stdout.flush()\n'
        'sys.stderr.write("final error line\\n")\n'
    )
    lines = []

    outcome = run_with_deadline(lambda: _run_ffmpeg([sys.executable, '-c', script], on_stdout_line=lines.append))

    assert 'error' not in outcome
    assert len(lines) == 2000
    assert outcome['value'].endswith('final error line\n')
    assert len(outcome['value']) <= postprocess_media.FFMPEG_STDERR_TAIL_CHARS


def test_run_ffmpeg_raises_called_process_error_with_stderr_tail():
    script = 'import sys; sys.stderr.write("boom: bad input\\n"); sys.exit(3)'

    outcome = run_with_deadline(lambda: _run_ffmpeg([sys.executable, '-c', script]))

    error = outcome['error']
    assert isinstance(error, subprocess.CalledProcessError)
    assert error.returncode == 3
    assert 'boom: bad input' in error.stderr


def test_run_ffmpeg_kills_process_when_cancel_is_requested():
    started = time.monotonic()
    cancel_after = started + 0.3

    outcome = run_with_deadline(
        lambda: _run_ffmpeg([sys.executable, '-c', 'import time; time.sleep(60)'], should_cancel=lambda: time.monotonic() > cancel_after),
        seconds=20,
    )

    assert isinstance(outcome.get('error'), MediaCancelled)
    assert time.monotonic() - started < 15


def test_extract_audio_failure_leaves_no_partial_output_and_keeps_previous_file(tmp_path: Path, monkeypatch):
    video = tmp_path / 'lecture.mp4'
    video.write_bytes(b'video')
    output = tmp_path / 'transcripts' / 'lecture.mp3'
    output.parent.mkdir()
    output.write_bytes(b'previous good audio')
    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.shutil.which', lambda name: '/opt/homebrew/bin/ffmpeg')

    def handler(command):
        Path(command[-1]).write_bytes(b'trunc')
        return 1, '', 'Conversion failed!\n'

    install_fake_ffmpeg(monkeypatch, handler)

    result = extract_audio(AudioExtractionRequest(input_path=video, format='mp3', overwrite=True))

    assert result.ok is False
    assert result.error_code == 'ffmpeg_failed'
    assert 'Conversion failed' in result.message
    assert output.read_bytes() == b'previous good audio'
    assert [path.name for path in output.parent.iterdir()] == ['lecture.mp3']


def test_extract_audio_cancel_reports_cancelled_and_cleans_partial(tmp_path: Path, monkeypatch):
    video = tmp_path / 'lecture.mp4'
    video.write_bytes(b'video')
    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.shutil.which', lambda name: '/opt/homebrew/bin/ffmpeg')

    def fake_run_ffmpeg(command, *, on_stdout_line=None, should_cancel=None, check=True):
        Path(command[-1]).write_bytes(b'partial')
        assert should_cancel is not None and should_cancel() is True
        raise MediaCancelled('ffmpeg cancelled')

    monkeypatch.setattr(postprocess_media, '_run_ffmpeg', fake_run_ffmpeg)

    result = extract_audio(AudioExtractionRequest(input_path=video, format='mp3'), should_cancel=lambda: True)

    assert result.ok is False
    assert result.error_code == 'cancelled'
    assert not (tmp_path / 'transcripts').exists() or list((tmp_path / 'transcripts').iterdir()) == []


def make_ready_stream_package(path: Path) -> bytes:
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('contents/media_root.xml', '<media_root />')
    return path.read_bytes()


@pytest.mark.parametrize('failure', ['not_ok', 'os_error', 'key_error'])
def test_ensure_playable_mp4_restores_original_package_when_conversion_fails(tmp_path: Path, monkeypatch, failure):
    package = tmp_path / 'lecture.mp4'
    original = make_ready_stream_package(package)

    def fake_reconstruct(input_path, *, output_path, overwrite, on_progress=None):
        output_path.write_bytes(b'half written mp4')
        if failure == 'os_error':
            raise OSError('No space left on device')
        if failure == 'key_error':
            raise KeyError('contents/web_files/missing.xml')
        return postprocess_media.VideoReconstructionResult(ok=False, input_path=input_path, output_path=output_path, error_code='video_reconstruction_failed', message='ffmpeg failed')

    monkeypatch.setattr(postprocess_media, 'reconstruct_ready_stream_video', fake_reconstruct)

    result = ensure_playable_mp4(package)

    assert result.ok is False
    assert result.error_code == 'video_reconstruction_failed'
    assert package.read_bytes() == original
    assert sorted(path.name for path in tmp_path.iterdir()) == ['lecture.mp4']


def test_ensure_playable_mp4_restores_original_package_on_cancel(tmp_path: Path, monkeypatch):
    package = tmp_path / 'lecture.mp4'
    original = make_ready_stream_package(package)

    def fake_reconstruct(input_path, *, output_path, overwrite, on_progress=None, should_cancel=None):
        raise MediaCancelled('cancelled')

    monkeypatch.setattr(postprocess_media, 'reconstruct_ready_stream_video', fake_reconstruct)

    result = ensure_playable_mp4(package, should_cancel=lambda: True)

    assert result.ok is False
    assert result.error_code == 'cancelled'
    assert package.read_bytes() == original


def test_reconstruct_ready_stream_video_reports_failure_when_ffmpeg_fails(tmp_path: Path, monkeypatch):
    package = tmp_path / 'readystream.mp4'
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr('contents/media_root.xml', '<media_root />')
        archive.writestr('contents/media_files/media/(video).mp4', b'inner')
    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.bundled_or_system_ffmpeg', lambda: '/opt/ffmpeg')
    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media._ffprobe_for_ffmpeg', lambda ffmpeg: '/opt/ffprobe')
    monkeypatch.setattr(
        'ssu_lecture_downloader.postprocess_media.subprocess.run',
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout='{"streams":[{"codec_type":"video"}]}', stderr=''),
    )
    install_fake_ffmpeg(monkeypatch, lambda command: (1, '', 'Invalid data found when processing input\n'))

    result = reconstruct_ready_stream_video(package, output_path=tmp_path / 'out.mp4', overwrite=True)

    assert result.ok is False
    assert result.error_code == 'video_reconstruction_failed'
    assert 'Invalid data found' in result.message
