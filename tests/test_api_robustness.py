import subprocess
import threading
import time
import types
import zipfile
from pathlib import Path

import pytest
from curl_cffi.requests import exceptions as curl_exceptions
from fastapi.testclient import TestClient

from ssu_lecture_downloader.service.api import create_app
from ssu_lecture_downloader.core.credentials import CredentialPair
from ssu_lecture_downloader.core.errors import LoginFlowError
from ssu_lecture_downloader.core.login_flow import LoginDiscoveryResult
from ssu_lecture_downloader.core.postprocess_media import AudioExtractionResult
from ssu_lecture_downloader.core.lms_service import BatchDownloadItemResult, BatchDownloadResult
from ssu_lecture_downloader.core.stt import TranscriptionResult
from test_api import ClearableFakeService, FakeCredentialStore, FakeService


@pytest.fixture(autouse=True)
def no_ambient_token(monkeypatch):
    monkeypatch.delenv('SSU_API_TOKEN', raising=False)


def make_client(service_cls, tmp_path, **kwargs):
    store = FakeCredentialStore(CredentialPair(username='student-id', password='secret-password'))
    app = create_app(service_factory=service_cls, default_output_dir=tmp_path, credential_store=store)
    return TestClient(app, **kwargs)


def http_error(status, url):
    error = curl_exceptions.HTTPError(f'HTTP {status}')
    error.response = types.SimpleNamespace(status_code=status, url=url)
    return error


def wait_job(client, path, job_id, timeout=5):
    deadline = time.monotonic() + timeout
    status = {}
    while time.monotonic() < deadline:
        status = client.get(f'{path}/{job_id}').json()
        if status['status'] not in {'queued', 'running'}:
            return status
        time.sleep(0.01)
    raise AssertionError(f'job never finished: {status}')


# ---- item 3: errors always come back as JSON ---------------------------------------------------


class RaisingService(ClearableFakeService):
    def __init__(self, error):
        super().__init__()
        self.error = error

    def get_course_detail(self, *args, **kwargs):
        raise self.error

    def discover_courses(self, *args, **kwargs):
        raise self.error


def course_detail(tmp_path, error):
    service = RaisingService(error)
    client = make_client(lambda: service, tmp_path, raise_server_exceptions=False)
    return service, client.get('/api/courses/1')


def test_login_flow_error_surfaces_its_own_error_code(tmp_path):
    _, response = course_detail(tmp_path, LoginFlowError('lti_launch_form_missing', 'LTI tool launch form not found'))

    assert response.status_code == 502
    assert response.json() == {'ok': False, 'error_code': 'lti_launch_form_missing', 'message': 'LTI tool launch form not found'}


def test_login_flow_auth_class_error_is_401(tmp_path):
    _, response = course_detail(tmp_path, LoginFlowError('xn_api_token_missing', 'no cookie'))

    assert response.status_code == 401
    assert response.json()['error_code'] == 'xn_api_token_missing'


@pytest.mark.parametrize(
    'error, status, code',
    [
        (curl_exceptions.Timeout('timed out'), 504, 'network_timeout'),
        (curl_exceptions.ReadTimeout('read timed out'), 504, 'network_timeout'),
        (curl_exceptions.ConnectionError('refused'), 502, 'network_error'),
        (curl_exceptions.DNSError('no such host'), 502, 'network_error'),
        (curl_exceptions.SSLError('tls'), 502, 'network_error'),
    ],
)
def test_network_failures_map_to_gateway_errors_not_500(tmp_path, error, status, code):
    service, response = course_detail(tmp_path, error)

    assert response.status_code == status
    body = response.json()
    assert body['ok'] is False and body['error_code'] == code
    assert body['message']
    assert service.clear_calls == 0, 'a network blip must not throw away the LMS session'


def test_unknown_exception_becomes_json_500_without_leaking_its_message(tmp_path):
    _, response = course_detail(tmp_path, ValueError('password=secret-password signed=https://cdn/x?token=abc'))

    assert response.status_code == 500
    assert response.headers['content-type'].startswith('application/json')
    body = response.json()
    assert body['ok'] is False
    assert body['error_code'] == 'internal_error'
    assert 'secret-password' not in response.text and 'token=abc' not in response.text


def test_exception_escaping_a_route_is_still_json_and_carries_cors_headers(tmp_path):
    class ExplodingStore(FakeCredentialStore):
        def get(self):
            raise KeyError('store exploded')

    client = TestClient(create_app(service_factory=FakeService, default_output_dir=tmp_path, credential_store=ExplodingStore()))

    response = client.get('/api/courses', headers={'Origin': 'tauri://localhost'})

    assert response.status_code == 500
    assert response.json()['error_code'] == 'internal_error'
    assert response.headers['access-control-allow-origin'] == 'tauri://localhost'


def test_unknown_routes_and_methods_use_the_json_error_shape(tmp_path):
    client = make_client(FakeService, tmp_path)

    missing = client.get('/api/nope')
    wrong_method = client.post('/api/health')

    assert missing.status_code == 404 and missing.json() == {'ok': False, 'error_code': 'not_found'}
    assert wrong_method.status_code == 405 and wrong_method.json()['ok'] is False


class NetworkDownLoginService(ClearableFakeService):
    def __init__(self, code):
        super().__init__()
        self.code = code

    def login(self, username, password):
        return LoginDiscoveryResult(ok=False, error_code=self.code)


@pytest.mark.parametrize('code, status', [('network_error', 502), ('network_timeout', 504), ('smartid_login_rejected', 401)])
def test_login_network_failure_is_not_reported_as_rejected_credentials(tmp_path, code, status):
    service = NetworkDownLoginService(code)
    client = make_client(lambda: service, tmp_path)

    terms = client.get('/api/terms')
    saved = client.post('/api/credentials', json={'username': 'student-id', 'password': 'secret-password'})

    assert terms.status_code == status and terms.json() == {'ok': False, 'error_code': code}
    assert saved.status_code == status and saved.json() == {'ok': False, 'error_code': code}


# ---- item 10: only LMS auth failures mean "session expired" -----------------------------------


def test_lms_401_means_session_expired_and_clears_the_cache(tmp_path):
    service, response = course_detail(tmp_path, http_error(401, 'https://canvas.ssu.ac.kr/learningx/api/v1/x'))

    assert response.status_code == 401
    assert response.json() == {'ok': False, 'error_code': 'session_expired'}
    assert service.clear_calls == 1


def test_cdn_403_is_a_request_failure_not_a_session_expiry(tmp_path):
    service, response = course_detail(tmp_path, http_error(403, 'https://ssu-toast.commonscdn.com/video.mp4?sig=expired'))

    assert response.status_code == 502
    assert response.json()['error_code'].startswith('lms_request_failed:')
    assert response.json()['error_code'] != 'session_expired'
    assert service.clear_calls == 0


class FailingDownloadService(ClearableFakeService):
    def __init__(self, error):
        super().__init__()
        self.error = error

    def download_lecture(self, *args, **kwargs):
        raise self.error


@pytest.mark.parametrize(
    'error, expected, cleared',
    [
        (http_error(401, 'https://canvas.ssu.ac.kr/x'), 'session_expired', 1),
        (http_error(403, 'https://ssu-toast.commonscdn.com/v.mp4'), 'lms_request_failed:unknown_step:403', 0),
        (curl_exceptions.Timeout('slow'), 'network_timeout', 0),
        (LoginFlowError('commons_download_url_missing', 'missing'), 'commons_download_url_missing', 0),
        (PermissionError('denied'), 'PermissionError', 0),
    ],
)
def test_background_job_error_codes(tmp_path, error, expected, cleared):
    service = FailingDownloadService(error)
    client = make_client(lambda: service, tmp_path)

    started = client.post('/api/download/jobs', json={'course_id': 1, 'module_item_id': 2}).json()
    status = wait_job(client, '/api/download/jobs', started['job_id'])

    assert status['status'] == 'failed'
    assert status['error_code'] == expected
    assert service.clear_calls == cleared


# ---- item 5: every job type reaches a terminal status ----------------------------------------


def test_audio_job_that_raises_is_failed_not_stuck_running(tmp_path, monkeypatch):
    video = tmp_path / 'lecture.mp4'
    video.write_bytes(b'video')

    def explode(request, on_progress=None, should_cancel=None):
        raise zipfile.BadZipFile('corrupt package')

    monkeypatch.setattr('ssu_lecture_downloader.core.postprocess_media.extract_audio', explode)
    client = make_client(FakeService, tmp_path)

    started = client.post('/api/media/audio/jobs', json={'input_path': str(video), 'format': 'mp3'}).json()
    status = wait_job(client, '/api/media/audio/jobs', started['job_id'])

    assert status['status'] == 'failed'
    assert status['error_code'] == 'BadZipFile'


def test_stt_job_that_raises_is_failed_not_stuck_running(tmp_path, monkeypatch):
    audio = tmp_path / 'lecture.wav'
    audio.write_bytes(b'audio')

    def explode(request, on_progress=None, should_cancel=None):
        raise PermissionError('cannot write transcript')

    monkeypatch.setattr('ssu_lecture_downloader.core.stt.transcribe_audio', explode)
    client = make_client(FakeService, tmp_path)

    started = client.post('/api/stt/jobs', json={'audio_path': str(audio), 'provider': 'fake'}).json()
    status = wait_job(client, '/api/stt/jobs', started['job_id'])

    assert status['status'] == 'failed'
    assert status['error_code'] == 'PermissionError'


def test_dependency_install_job_that_times_out_is_failed_not_stuck_running(tmp_path, monkeypatch):
    def explode(components, on_progress=None, should_cancel=None):
        raise subprocess.TimeoutExpired(['uv', 'pip', 'install'], 900)

    monkeypatch.setattr('ssu_lecture_downloader.service.dependency_installer.install_missing_media_components', explode)
    client = make_client(FakeService, tmp_path)

    started = client.post('/api/media/dependencies/install/jobs', json={'components': ['ffmpeg']}).json()
    status = wait_job(client, '/api/media/dependencies/install/jobs', started['job_id'])

    assert status['status'] == 'failed'
    assert status['error_code'] == 'TimeoutExpired'


def test_audio_job_cancel_endpoint_reaches_the_running_extraction(tmp_path, monkeypatch):
    video = tmp_path / 'lecture.mp4'
    video.write_bytes(b'video')
    running = threading.Event()

    def slow_extract(request, on_progress=None, should_cancel=None):
        running.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if should_cancel and should_cancel():
                return AudioExtractionResult(ok=False, input_path=request.input_path, error_code='cancelled', message='cancelled')
            time.sleep(0.005)
        return AudioExtractionResult(ok=True, input_path=request.input_path, output_path=tmp_path / 'late.mp3')

    monkeypatch.setattr('ssu_lecture_downloader.core.postprocess_media.extract_audio', slow_extract)
    client = make_client(FakeService, tmp_path)

    started = client.post('/api/media/audio/jobs', json={'input_path': str(video), 'format': 'mp3'}).json()
    assert running.wait(5)
    cancelled = client.post(f"/api/media/audio/jobs/{started['job_id']}/cancel").json()
    status = wait_job(client, '/api/media/audio/jobs', started['job_id'])

    assert cancelled['cancel_requested'] is True
    assert status['status'] == 'cancelled'


def test_stt_job_cancel_endpoint_reaches_the_running_transcription(tmp_path, monkeypatch):
    audio = tmp_path / 'lecture.wav'
    audio.write_bytes(b'audio')
    running = threading.Event()

    def slow_transcribe(request, on_progress=None, should_cancel=None):
        running.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (should_cancel and should_cancel()):
            time.sleep(0.005)
        return TranscriptionResult(ok=False, audio_path=request.audio_path, error_code='cancelled')

    monkeypatch.setattr('ssu_lecture_downloader.core.stt.transcribe_audio', slow_transcribe)
    client = make_client(FakeService, tmp_path)

    started = client.post('/api/stt/jobs', json={'audio_path': str(audio), 'provider': 'fake'}).json()
    assert running.wait(5)
    client.post(f"/api/stt/jobs/{started['job_id']}/cancel")

    assert wait_job(client, '/api/stt/jobs', started['job_id'])['status'] == 'cancelled'


def test_dependency_install_job_can_be_cancelled(tmp_path, monkeypatch):
    running = threading.Event()

    def slow_install(components, on_progress=None, should_cancel=None):
        running.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not should_cancel():
            time.sleep(0.005)
        return {'ok': False, 'error_code': 'cancelled', 'installed_components': [], 'missing_components': components}

    monkeypatch.setattr('ssu_lecture_downloader.service.dependency_installer.install_missing_media_components', slow_install)
    client = make_client(FakeService, tmp_path)

    started = client.post('/api/media/dependencies/install/jobs', json={'components': ['ffmpeg']}).json()
    assert running.wait(5)
    response = client.post(f"/api/media/dependencies/install/jobs/{started['job_id']}/cancel")

    assert response.status_code == 200
    assert wait_job(client, '/api/media/dependencies/install/jobs', started['job_id'])['status'] == 'cancelled'
    assert client.post('/api/media/dependencies/install/jobs/missing/cancel').status_code == 404


def test_download_job_honors_cancel_even_when_the_service_returns_normally(tmp_path):
    running = threading.Event()

    class SlowService(FakeService):
        def download_lecture(self, *args, should_cancel=None, **kwargs):
            running.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not should_cancel():
                time.sleep(0.005)
            return super().download_lecture(*args, should_cancel=None, **kwargs)

    client = make_client(SlowService, tmp_path)
    started = client.post('/api/download/jobs', json={'course_id': 1, 'module_item_id': 2}).json()
    assert running.wait(5)
    client.post(f"/api/download/jobs/{started['job_id']}/cancel")

    assert wait_job(client, '/api/download/jobs', started['job_id'])['status'] == 'cancelled'


def test_job_registry_prunes_old_finished_jobs(tmp_path):
    from ssu_lecture_downloader.core import jobs

    client = make_client(FakeService, tmp_path)
    ids = []
    for _ in range(jobs.DEFAULT_MAX_FINISHED_JOBS + 5):
        started = client.post('/api/download/jobs', json={'course_id': 1, 'module_item_id': 2}).json()
        wait_job(client, '/api/download/jobs', started['job_id'])
        ids.append(started['job_id'])

    # The newest jobs stay pollable, the oldest were dropped.
    assert client.get(f'/api/download/jobs/{ids[-1]}').status_code == 200
    assert client.get(f'/api/download/jobs/{ids[0]}').status_code == 404


# ---- item 7: failed/cancelled artifact jobs stop queued work -------------------------------


class TwoVideoService(FakeService):
    """Dry-run plan with two videos that already exist on disk, so no download is needed."""

    def __init__(self, videos):
        self.videos = videos

    def batch_download_lectures(self, username, password, *, course_id, course_name, output_dir, dry_run, module_item_ids=None, include_completed=False, on_progress=None, should_cancel=None, conflict_strategy='rename', max_parallel_downloads=4):
        items = [
            BatchDownloadItemResult(
                ok=True, error_code=None, dry_run=dry_run, written=False, course_id=course_id, module_item_id=index,
                title=f'강의 {index}', completed=False, attendance_item_id=None, content_id=None, download_url=None,
                download_size=None, output_path=str(video), file_size=video.stat().st_size, file_exists=True, skipped=True,
                conflict_strategy=conflict_strategy,
            )
            for index, video in enumerate(self.videos, start=1)
        ]
        return BatchDownloadResult(ok=True, error_code=None, dry_run=dry_run, course_id=course_id, course_name=course_name, items=items)


def make_artifact_client(tmp_path, videos):
    store = FakeCredentialStore(CredentialPair(username='u', password='p'))
    return TestClient(create_app(service_factory=lambda: TwoVideoService(videos), default_output_dir=tmp_path, credential_store=store))


def make_videos(tmp_path):
    videos = []
    for index in (1, 2):
        video = tmp_path / 'course' / f'lecture-{index}.mp4'
        video.parent.mkdir(exist_ok=True)
        video.write_bytes(b'video')
        videos.append(video)
    return videos


def test_artifact_job_failure_does_not_run_the_remaining_queued_items(tmp_path, monkeypatch):
    videos = make_videos(tmp_path)
    extracted = []

    def fake_extract(request, on_progress=None, should_cancel=None):
        extracted.append(request.input_path.name)
        return AudioExtractionResult(ok=False, input_path=request.input_path, error_code='ffmpeg_failed', message='bad input')

    monkeypatch.setattr('ssu_lecture_downloader.core.postprocess_media.extract_audio', fake_extract)
    client = make_artifact_client(tmp_path, videos)

    started = client.post('/api/download/artifacts/jobs', json={'course_id': 1, 'module_item_ids': [1, 2], 'outputs': ['mp3'], 'max_parallel_downloads': 4}).json()
    status = wait_job(client, '/api/download/artifacts/jobs', started['job_id'])
    time.sleep(0.1)

    assert status['status'] == 'failed'
    assert status['error_code'] == 'ffmpeg_failed'
    assert extracted == ['lecture-1.mp4']
    assert status['result']['artifacts'][0]['error_code'] == 'ffmpeg_failed'


def test_artifact_job_cancel_ends_cancelled_not_failed(tmp_path, monkeypatch):
    videos = make_videos(tmp_path)
    running = threading.Event()

    def blocking_extract(request, on_progress=None, should_cancel=None):
        running.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not should_cancel():
            time.sleep(0.005)
        return AudioExtractionResult(ok=False, input_path=request.input_path, error_code='cancelled', message='cancelled')

    monkeypatch.setattr('ssu_lecture_downloader.core.postprocess_media.extract_audio', blocking_extract)
    client = make_artifact_client(tmp_path, videos)

    started = client.post('/api/download/artifacts/jobs', json={'course_id': 1, 'module_item_ids': [1, 2], 'outputs': ['mp3']}).json()
    assert running.wait(5)
    client.post(f"/api/download/artifacts/jobs/{started['job_id']}/cancel")
    status = wait_job(client, '/api/download/artifacts/jobs', started['job_id'])

    assert status['status'] == 'cancelled'
    assert status['result'] is not None


def test_artifact_transcript_is_only_reused_when_the_txt_commit_marker_exists(tmp_path, monkeypatch):
    videos = make_videos(tmp_path)
    audio = videos[0].with_suffix('.mp3')
    audio.write_bytes(b'audio')
    # An interrupted earlier run left the md/json files but never committed the .txt.
    videos[0].with_name('lecture-1.transcript.md').write_text('partial')
    videos[0].with_name('lecture-1.transcript.json').write_text('{}')
    transcribed = []

    def fake_transcribe(request, on_progress=None, should_cancel=None):
        transcribed.append(request.audio_path.name)
        text = request.audio_path.with_suffix('.transcript.txt')
        text.write_text('done')
        return TranscriptionResult(ok=True, audio_path=request.audio_path, text_path=text)

    monkeypatch.setattr('ssu_lecture_downloader.core.stt.transcribe_audio', fake_transcribe)
    client = make_artifact_client(tmp_path, videos[:1])

    started = client.post('/api/download/artifacts/jobs', json={'course_id': 1, 'module_item_ids': [1], 'outputs': ['txt'], 'audio_format': 'mp3'}).json()
    status = wait_job(client, '/api/download/artifacts/jobs', started['job_id'])

    assert status['status'] == 'succeeded'
    assert transcribed == ['lecture-1.mp3']


# ---- item 12: default download folder ---------------------------------------------------------


def test_default_output_dir_is_the_user_downloads_folder_not_the_working_directory(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    cwd = tmp_path / 'inside-app-bundle'
    cwd.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.chdir(cwd)
    client = TestClient(create_app(service_factory=FakeService, credential_store=FakeCredentialStore(CredentialPair(username='u', password='p'))))

    response = client.post('/api/download/plan', json={'course_id': 1, 'module_item_id': 2, 'course_name': 'UX'})

    assert response.status_code == 200
    output_path = Path(response.json()['output_path'])
    assert output_path.is_relative_to(home / 'Downloads' / 'SSU Lecture Downloader')
    assert not output_path.is_relative_to(cwd)


def test_explicit_output_dir_is_still_honored(tmp_path):
    client = make_client(FakeService, tmp_path / 'default')
    chosen = tmp_path / 'chosen'

    response = client.post('/api/download/plan', json={'course_id': 1, 'module_item_id': 2, 'course_name': 'UX', 'output_dir': str(chosen)})

    assert Path(response.json()['output_path']).is_relative_to(chosen)


# ---- duplicated mode validation stays consistent on every entry point -------------------------


@pytest.mark.parametrize('path', ['/api/download/batch/plan', '/api/download/batch', '/api/download/batch/jobs', '/api/download/artifacts/jobs'])
def test_batch_mode_validation_is_shared_by_every_entry_point(tmp_path, path):
    client = make_client(FakeService, tmp_path)

    bad_mode = client.post(path, json={'course_id': 1, 'mode': 'everything'})
    no_ids = client.post(path, json={'course_id': 1, 'mode': 'selected'})

    assert bad_mode.status_code == 400 and bad_mode.json()['error_code'] == 'invalid_batch_mode'
    assert no_ids.status_code == 400 and no_ids.json()['error_code'] == 'missing_module_item_ids'
