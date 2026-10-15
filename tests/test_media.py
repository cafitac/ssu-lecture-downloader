from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

from ssu_lecture_downloader.media import AudioExtractionRequest, extract_audio, plan_audio_output_path
from ssu_lecture_downloader.postprocess_media import ensure_playable_mp4, reconstruct_ready_stream_video


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
    recorded = {}

    def fake_run(command, *, capture_output, text, check):
        recorded['command'] = command
        output_path = Path(command[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b'audio')
        return subprocess.CompletedProcess(command, 0, stderr='')

    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.subprocess.run', fake_run)

    result = extract_audio(AudioExtractionRequest(input_path=video, format='wav'))

    assert result.ok is True
    assert result.output_path == tmp_path / 'transcripts' / 'lecture.wav'
    assert result.file_size == 5
    assert recorded['command'][:6] == ['/opt/homebrew/bin/ffmpeg', '-y', '-i', str(video), '-vn', '-ac']


def test_extract_audio_emits_ffmpeg_progress_percent(tmp_path: Path, monkeypatch):
    video = tmp_path / 'lecture.mp4'
    video.write_bytes(b'video')
    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.shutil.which', lambda name: f'/opt/homebrew/bin/{name}')

    def fake_run(command, *, capture_output, text, check):
        assert command[0].endswith('ffprobe')
        return subprocess.CompletedProcess(command, 0, stdout='10.0\n', stderr='')

    class FakeProcess:
        def __init__(self, command, *, stdout, stderr, text):
            self.command = command
            self.stdout = iter(['out_time_ms=2500000\n', 'progress=continue\n', 'out_time_ms=10000000\n', 'progress=end\n'])
            self.stderr = self

        def read(self):
            output_path = Path(self.command[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b'audio')
            return ''

        def wait(self):
            return 0

    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.subprocess.run', fake_run)
    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.subprocess.Popen', FakeProcess)
    events = []

    result = extract_audio(AudioExtractionRequest(input_path=video, format='mp3'), on_progress=events.append)

    assert result.ok is True
    progress_events = [event for event in events if event['stage'] == 'audio_progress']
    assert progress_events[0]['progress_percent'] == 25
    assert progress_events[-1]['progress_percent'] == 100
    assert progress_events[-1]['duration_ms'] == 10000



def test_extract_audio_uses_inner_media_from_readystream_package(tmp_path: Path, monkeypatch):
    package = tmp_path / 'readystream.mp4'
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr('AppManifest.xaml', '<Deployment />')
        archive.writestr('contents/media_root.xml', '<media_root />')
        archive.writestr('contents/media_files/media/(small).mp4', b'small')
        archive.writestr('contents/media_files/media/(large).mp4', b'large media bytes')
    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.shutil.which', lambda name: '/opt/homebrew/bin/ffmpeg')
    recorded = {}

    def fake_run(command, *, capture_output, text, check):
        recorded['command'] = command
        ffmpeg_input = Path(command[3])
        assert ffmpeg_input.name == 'readystream.mp4'
        assert ffmpeg_input.read_bytes() == b'large media bytes'
        output_path = Path(command[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b'audio')
        return subprocess.CompletedProcess(command, 0, stderr='')

    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.subprocess.run', fake_run)

    result = extract_audio(AudioExtractionRequest(input_path=package, format='mp3'))

    assert result.ok is True
    assert result.output_path == tmp_path / 'transcripts' / 'readystream.mp3'
    assert recorded['command'][3] != str(package)


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

    def fake_run(command, *, capture_output, text, check):
        if command[0] == '/opt/ffprobe':
            return subprocess.CompletedProcess(command, 0, stdout='{"streams":[{"codec_type":"video"},{"codec_type":"audio"}]}', stderr='')
        if command[:3] == ['/opt/ffmpeg', '-v', 'error'] and command[-2:] == ['null', '-']:
            return subprocess.CompletedProcess(command, 0, stdout='', stderr='')
        output_path = Path(command[-1])
        output_path.write_bytes(inner_video)
        return subprocess.CompletedProcess(command, 0, stdout='', stderr='')

    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.subprocess.run', fake_run)

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
    commands = []

    def fake_run(command, *, capture_output, text, check):
        commands.append(command)
        if command[0] == '/opt/ffprobe':
            return subprocess.CompletedProcess(command, 0, stdout='{"streams":[{"codec_type":"video"}]}', stderr='')
        if command[:3] == ['/opt/ffmpeg', '-v', 'error'] and command[-2:] == ['null', '-']:
            return subprocess.CompletedProcess(command, 0, stdout='', stderr='non monotonically increasing dts')
        output_path = Path(command[-1])
        if '-vf' in command:
            output_path.write_bytes(normalized_video)
        else:
            output_path.write_bytes(inner_video)
        return subprocess.CompletedProcess(command, 0, stdout='', stderr='')

    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.subprocess.run', fake_run)

    result = reconstruct_ready_stream_video(package, output_path=output, overwrite=True)

    assert result.ok is True
    assert output.read_bytes() == normalized_video
    assert result.source_kind == 'readystream_inner_video_normalized'
    assert any('-vf' in command for command in commands)


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

    def fake_run(command, *, capture_output, text, check):
        if command[0] == '/opt/ffprobe':
            assert Path(command[-1]).read_bytes() == b'audio bytes'
            return subprocess.CompletedProcess(command, 0, stdout='10.0\n', stderr='')
        recorded['command'] = command
        concat_path = Path(command[command.index('-i') + 1])
        concat_text = concat_path.read_text()
        assert 'duration 5.000000' in concat_text
        assert concat_text.count('file ') == 3
        output.write_bytes(b'playable mp4')
        return subprocess.CompletedProcess(command, 0, stdout='', stderr='')

    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.subprocess.run', fake_run)

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
