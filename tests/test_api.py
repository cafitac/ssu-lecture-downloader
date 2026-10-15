from pathlib import Path
import time

from curl_cffi.requests.exceptions import HTTPError
from fastapi.testclient import TestClient

from ssu_lecture_downloader.api import create_app
from ssu_lecture_downloader.credentials import CredentialPair
from ssu_lecture_downloader.models import CourseContentItem, CourseSummary, LectureItem
from ssu_lecture_downloader.service import BatchDownloadItemResult, BatchDownloadResult, CourseDetailResult, CourseDiscoveryResult, DownloadLectureResult, OfficialViewerBridgeCookie, OfficialViewerPlanResult, OfficialViewerSessionBridgeResult
from ssu_lecture_downloader.login_flow import LoginDiscoveryResult
from ssu_lecture_downloader.stt import STTProviderStatus
from ssu_lecture_downloader.trace import NetworkTrace, TraceEntry


class FakeCredentialStore:
    def __init__(self, pair: CredentialPair | None = None):
        self.pair = pair
        self.saved: CredentialPair | None = None
        self.deleted = False

    def get(self):
        return self.pair

    def status(self):
        return {
            'source': 'test' if self.pair else 'none',
            'username_present': self.pair is not None,
            'password_present': self.pair is not None,
            'can_write': True,
        }

    def save(self, pair: CredentialPair):
        self.saved = pair
        self.pair = pair

    def delete(self):
        self.deleted = True
        self.pair = None


class FakeService:
    def login(self, username, password):
        return LoginDiscoveryResult(ok=True, error_code=None)

    def list_terms(self, username, password):
        return [
            {'term_id': 43, 'course_count': 2, 'current_course_count': 0, 'is_current': False, 'label': 'term 43'},
            {'term_id': 50, 'course_count': 1, 'current_course_count': 1, 'is_current': True, 'label': 'term 50'},
        ]

    def discover_courses(self, username, password, term_ids=None):
        courses = [CourseSummary(id=44070, name='UX방법론', module_count=15, lecture_count=1, uncompleted_lecture_count=0)]
        if term_ids == [43]:
            courses = [CourseSummary(id=42045, name='데이터사이언스', module_count=12, lecture_count=1, uncompleted_lecture_count=1)]
        return CourseDiscoveryResult(
            ok=True,
            error_code=None,
            login=LoginDiscoveryResult(ok=True, error_code=None),
            courses=courses,
        )

    def get_course_detail(self, username, password, *, course_id, course_name=None):
        return CourseDetailResult(
            ok=True,
            error_code=None,
            course=CourseSummary(id=course_id, name=course_name or 'UX방법론', module_count=15, lecture_count=1, uncompleted_lecture_count=0),
            lectures=[
                LectureItem(
                    module_id=790135,
                    module_name='1주차: 강의 소개',
                    item_id=None,
                    module_item_id=3268578,
                    title='26년1학기_UX방법론_1주차',
                    completed=True,
                    duration=42,
                    content_type='movie',
                )
            ],
            content_items=[
                CourseContentItem(
                    module_id=790135,
                    module_name='1주차: 강의 소개',
                    week_position=1,
                    lesson_position=1,
                    item_id=None,
                    module_item_id=3268578,
                    title='26년1학기_UX방법론_1주차',
                    content_type='attendance_item',
                    inner_content_type='movie',
                    kind='video',
                    completed=True,
                    duration=42,
                    size_bytes=330554102,
                    downloadable=True,
                    resolvable=True,
                ),
                CourseContentItem(
                    module_id=790135,
                    module_name='1주차: 강의 소개',
                    week_position=1,
                    lesson_position=2,
                    item_id=None,
                    module_item_id=3268579,
                    title='26년1학기_UX방법론_1주차_자료',
                    content_type='attendance_item',
                    inner_content_type='pdf',
                    kind='material',
                    completed=True,
                    size_bytes=11525882,
                    file_name='week1.pdf',
                    downloadable=True,
                    resolvable=True,
                ),
            ],
        )

    def download_lecture(self, username, password, *, course_id, module_item_id, course_name, output_dir, dry_run, on_progress=None, should_cancel=None, conflict_strategy='rename'):
        if should_cancel and should_cancel():
            raise RuntimeError('cancelled')
        if on_progress:
            on_progress({'stage': 'download_started', 'module_item_id': module_item_id, 'bytes_written': 0, 'total_bytes': 330554102})
            on_progress({'stage': 'download_progress', 'module_item_id': module_item_id, 'bytes_written': 1024, 'total_bytes': 330554102})
            on_progress({'stage': 'download_done', 'module_item_id': module_item_id, 'bytes_written': 330554102, 'total_bytes': 330554102})
        return DownloadLectureResult(
            ok=True,
            error_code=None,
            dry_run=dry_run,
            written=False,
            course_id=course_id,
            module_item_id=module_item_id,
            attendance_item_id='555',
            title='26년1학기_UX방법론_1주차',
            content_id='699c7d422c427',
            download_url='https://commons.ssu.ac.kr/download?[REDACTED]',
            download_size=330554102,
            output_path=str(output_dir / 'UX방법론' / '26년1학기_UX방법론_1주차 [699c7d422c427].movie'),
            file_size=None,
            file_exists=False,
            skipped=False,
            conflict_strategy=conflict_strategy,
        )

    def native_download_plan(self, username, password, *, course_id, module_item_id, course_name, output_dir, conflict_strategy='rename'):
        result = self.download_lecture(
            username,
            password,
            course_id=course_id,
            module_item_id=module_item_id,
            course_name=course_name,
            output_dir=output_dir,
            dry_run=True,
            conflict_strategy=conflict_strategy,
        )
        return {
            'result': result,
            'native_download': {
                'download_url': 'https://ssu-toast.commonscdn.com/native/raw.mp4?token=***',
                'referer': 'https://canvas.ssu.ac.kr/learningx/lti/lecture_attendance/items/view/555',
                'output_path': result.output_path,
                'total_bytes': result.download_size,
            },
        }

    def official_viewer_plan(self, username, password, *, course_id, module_item_id):
        return OfficialViewerPlanResult(
            ok=True,
            error_code=None,
            course_id=course_id,
            module_item_id=module_item_id,
            attendance_item_id='555',
            title='26년1학기_UX방법론_1주차',
            content_id='699c7d422c427',
            content_type='movie',
            duration=42,
            viewer_url='https://commons.ssu.ac.kr/em/699c7d422c427?viewer=1&TargetUrl=%2Flearningx%2Fapi%2Fv1%2Fprogress',
        )

    def official_viewer_session_bridge_plan(self, username, password, *, course_id, module_item_id):
        plan = self.official_viewer_plan(username, password, course_id=course_id, module_item_id=module_item_id)
        return OfficialViewerSessionBridgeResult(
            ok=plan.ok,
            error_code=plan.error_code,
            course_id=plan.course_id,
            module_item_id=plan.module_item_id,
            attendance_item_id=plan.attendance_item_id,
            title=plan.title,
            content_id=plan.content_id,
            content_type=plan.content_type,
            duration=plan.duration,
            viewer_url=plan.viewer_url,
            launch_url=f'https://canvas.ssu.ac.kr/courses/{course_id}/modules/items/{module_item_id}',
            cookies=[OfficialViewerBridgeCookie(name='xn_api_token', value='raw-token', domain='canvas.ssu.ac.kr', path='/', secure=True, http_only=True)],
            diagnostics={'cookie_count': 1, 'cookie_names': ['xn_api_token'], 'cookie_domains': ['canvas.ssu.ac.kr']},
        )

    def batch_download_lectures(self, username, password, *, course_id, course_name, output_dir, dry_run, module_item_ids=None, include_completed=False, on_progress=None, should_cancel=None, conflict_strategy='rename', max_parallel_downloads=4):
        selected_ids = module_item_ids or [3268578]
        if on_progress:
            on_progress({'stage': 'resolving', 'module_item_id': selected_ids[0], 'index': 1, 'total': 1})
            on_progress({'stage': 'planned' if dry_run else 'done', 'module_item_id': selected_ids[0], 'index': 1, 'total': 1})
        return BatchDownloadResult(
            ok=True,
            error_code=None,
            dry_run=dry_run,
            course_id=course_id,
            course_name=course_name,
            items=[
                BatchDownloadItemResult(
                    ok=True,
                    error_code=None,
                    dry_run=dry_run,
                    written=not dry_run,
                    course_id=course_id,
                    module_item_id=selected_ids[0],
                    title='26년1학기_UX방법론_1주차',
                    completed=True,
                    attendance_item_id='555',
                    content_id='699c7d422c427',
                    download_url='https://commons.ssu.ac.kr/download?[REDACTED]',
                    download_size=330554102,
                    output_path=str(output_dir / 'UX방법론' / '26년1학기_UX방법론_1주차 [699c7d422c427].movie'),
                    file_size=None if dry_run else 330554102,
                    file_exists=False,
                    skipped=False,
                    conflict_strategy=conflict_strategy,
                )
            ],
        )


class CountingFactory:
    def __init__(self):
        self.calls = 0
        self.service = FakeService()

    def __call__(self):
        self.calls += 1
        return self.service


class ClearableFakeService(FakeService):
    def __init__(self):
        self.clear_calls = 0

    def clear_cache(self):
        self.clear_calls += 1


class RejectingFakeService(ClearableFakeService):
    def login(self, username, password):
        return LoginDiscoveryResult(ok=False, error_code='smartid_login_rejected')

    def list_terms(self, username, password):
        raise AssertionError('terms should not be listed after rejected login')


class EmptyTermsFakeService(ClearableFakeService):
    def list_terms(self, username, password):
        return []


class NativePlanHttpErrorFakeService(FakeService):
    def __init__(self):
        self.http = type('Http', (), {'trace': NetworkTrace()})()
        self.http.trace.add(TraceEntry(
            step='get_commons_content',
            method='GET',
            url='https://canvas.ssu.ac.kr/learningx/api/v1/commons/contents?content_id=abc',
            status_code=502,
            final_url='https://canvas.ssu.ac.kr/learningx/api/v1/commons/contents?content_id=abc',
            elapsed_ms=123.4,
            body_size=77,
        ))

    def native_download_plan(self, username, password, *, course_id, module_item_id, course_name, output_dir, conflict_strategy='rename'):
        raise HTTPError('upstream failed')


class ExpiredSessionFakeService(ClearableFakeService):
    def get_course_detail(self, username, password, *, course_id, course_name=None):
        return CourseDetailResult(ok=False, error_code='session_expired', course=None, lectures=[], content_items=[])

    def download_lecture(self, username, password, *, course_id, module_item_id, course_name, output_dir, dry_run, on_progress=None, should_cancel=None, conflict_strategy='rename'):
        return DownloadLectureResult(
            ok=False,
            error_code='session_expired',
            dry_run=dry_run,
            written=False,
            course_id=course_id,
            module_item_id=module_item_id,
            attendance_item_id=None,
            title=None,
            content_id=None,
            download_url=None,
            download_size=None,
            output_path=None,
            file_size=None,
        )

    def official_viewer_plan(self, username, password, *, course_id, module_item_id):
        return OfficialViewerPlanResult(
            ok=False,
            error_code='session_expired',
            course_id=course_id,
            module_item_id=module_item_id,
            attendance_item_id=None,
            title=None,
            content_id=None,
            content_type=None,
            duration=None,
            viewer_url=None,
        )


def make_client(monkeypatch, tmp_path: Path):
    monkeypatch.setenv('SSU_USERNAME', 'present-user')
    monkeypatch.setenv('SSU_PASSWORD', 'present-password')
    store = FakeCredentialStore(CredentialPair(username='present-user', password='present-password'))
    app = create_app(service_factory=lambda: FakeService(), default_output_dir=tmp_path, credential_store=store)
    return TestClient(app)


def test_api_reuses_single_service_instance_across_requests(tmp_path):
    factory = CountingFactory()
    store = FakeCredentialStore(CredentialPair(username='present-user', password='present-password'))
    app = create_app(service_factory=factory, default_output_dir=tmp_path, credential_store=store)
    client = TestClient(app)

    assert client.get('/api/terms').status_code == 200
    assert client.get('/api/courses?term_id=43').status_code == 200
    assert factory.calls == 1


def test_api_credential_changes_clear_service_cache(tmp_path):
    service = ClearableFakeService()
    store = FakeCredentialStore()
    app = create_app(service_factory=lambda: service, default_output_dir=tmp_path, credential_store=store)
    client = TestClient(app)

    assert client.post('/api/credentials', json={'username': 'student-id', 'password': 'secret-password'}).status_code == 200
    assert service.clear_calls == 1
    assert client.delete('/api/credentials').status_code == 200
    assert service.clear_calls == 2


def test_api_rejects_invalid_credentials_without_saving(tmp_path):
    service = RejectingFakeService()
    store = FakeCredentialStore()
    app = create_app(service_factory=lambda: service, default_output_dir=tmp_path, credential_store=store)
    client = TestClient(app)

    response = client.post('/api/credentials', json={'username': 'student-id', 'password': 'wrong-password'})

    assert response.status_code == 401
    assert response.json() == {'ok': False, 'error_code': 'smartid_login_rejected'}
    assert store.pair is None
    assert store.saved is None
    assert service.clear_calls == 1


def test_api_terms_returns_authentication_error_for_rejected_stored_credentials(tmp_path):
    service = RejectingFakeService()
    store = FakeCredentialStore(CredentialPair(username='student-id', password='wrong-password'))
    app = create_app(service_factory=lambda: service, default_output_dir=tmp_path, credential_store=store)
    client = TestClient(app)

    response = client.get('/api/terms')

    assert response.status_code == 401
    assert response.json() == {'ok': False, 'error_code': 'smartid_login_rejected'}


def test_api_terms_returns_authentication_error_for_empty_term_discovery(tmp_path):
    service = EmptyTermsFakeService()
    store = FakeCredentialStore(CredentialPair(username='student-id', password='saved-password'))
    app = create_app(service_factory=lambda: service, default_output_dir=tmp_path, credential_store=store)
    client = TestClient(app)

    response = client.get('/api/terms')

    assert response.status_code == 401
    assert response.json() == {'ok': False, 'error_code': 'lms_session_unverified'}
    assert service.clear_calls == 1


def test_api_course_detail_returns_authentication_error_for_expired_session(tmp_path):
    service = ExpiredSessionFakeService()
    store = FakeCredentialStore(CredentialPair(username='student-id', password='secret-password'))
    app = create_app(service_factory=lambda: service, default_output_dir=tmp_path, credential_store=store)
    client = TestClient(app)

    response = client.get('/api/courses/44070?course_name=UX방법론')

    assert response.status_code == 401
    assert response.json() == {'ok': False, 'error_code': 'session_expired'}


def test_api_download_plan_returns_authentication_error_for_expired_session(tmp_path):
    service = ExpiredSessionFakeService()
    store = FakeCredentialStore(CredentialPair(username='student-id', password='secret-password'))
    app = create_app(service_factory=lambda: service, default_output_dir=tmp_path, credential_store=store)
    client = TestClient(app)

    response = client.post('/api/download/plan', json={'course_id': 44070, 'module_item_id': 3268578, 'course_name': 'UX방법론'})

    assert response.status_code == 401
    assert response.json() == {'ok': False, 'error_code': 'session_expired'}


def test_api_lists_current_courses(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.get('/api/courses')

    assert response.status_code == 200
    payload = response.json()
    assert payload['ok'] is True
    assert payload['course_count'] == 1
    assert payload['courses'][0]['id'] == 44070


def test_api_lists_available_terms(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.get('/api/terms')

    assert response.status_code == 200
    payload = response.json()
    assert payload['ok'] is True
    assert payload['terms'][-1] == {'term_id': 50, 'course_count': 1, 'current_course_count': 1, 'is_current': True, 'label': 'term 50'}


def test_api_lists_courses_for_explicit_past_term(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.get('/api/courses?term_id=43')

    assert response.status_code == 200
    payload = response.json()
    assert payload['ok'] is True
    assert payload['term_ids'] == [43]
    assert payload['courses'][0]['id'] == 42045


def test_api_returns_course_detail_lecture_rows(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.get('/api/courses/44070?course_name=UX방법론')

    assert response.status_code == 200
    payload = response.json()
    assert payload['ok'] is True
    assert payload['course']['id'] == '44070'
    assert payload['lectures'][0]['module_item_id'] == 3268578
    assert payload['lectures'][0]['module_name'] == '1주차: 강의 소개'
    assert payload['content_count'] == 2
    assert [item['kind'] for item in payload['content_items']] == ['video', 'material']
    assert payload['content_items'][0]['duration'] == 42
    assert payload['content_items'][1]['inner_content_type'] == 'pdf'
    assert payload['content_items'][1]['downloadable'] is True


def test_api_plans_download_without_writing(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post('/api/download/plan', json={'course_id': 44070, 'module_item_id': 3268578, 'course_name': 'UX방법론'})

    assert response.status_code == 200
    payload = response.json()
    assert payload['ok'] is True
    assert payload['dry_run'] is True
    assert payload['written'] is False
    assert payload['output_path'].endswith('UX방법론/26년1학기_UX방법론_1주차 [699c7d422c427].movie')
    assert payload['progress'] == []


def test_api_native_download_plan_returns_raw_transfer_data_for_tauri(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post('/api/download/native-plan', json={'course_id': 44070, 'module_item_id': 3268578, 'course_name': 'UX방법론'})

    assert response.status_code == 200
    payload = response.json()
    assert payload['ok'] is True
    assert payload['dry_run'] is True
    assert payload['written'] is False
    assert payload['download_url'] == 'https://commons.ssu.ac.kr/download?[REDACTED]'
    assert payload['native_download']['download_url'] == 'https://ssu-toast.commonscdn.com/native/raw.mp4?token=***'
    assert payload['native_download']['referer'].startswith('https://canvas.ssu.ac.kr/learningx/lti/')
    assert payload['native_download']['output_path'] == payload['output_path']
    assert payload['native_download']['total_bytes'] == 330554102


def test_api_native_download_plan_http_error_includes_lms_step_and_status(tmp_path):
    service = NativePlanHttpErrorFakeService()
    store = FakeCredentialStore(CredentialPair(username='present-user', password='present-password'))
    app = create_app(service_factory=lambda: service, default_output_dir=tmp_path, credential_store=store)
    client = TestClient(app)

    response = client.post('/api/download/native-plan', json={'course_id': 44070, 'module_item_id': 3268578, 'course_name': 'UX방법론'})

    assert response.status_code == 502
    payload = response.json()
    assert payload['error_code'] == 'lms_request_failed:get_commons_content:502'
    assert payload['message'] == 'LMS/Commons 요청 실패: get_commons_content 단계에서 HTTP 502 응답을 받았습니다.'
    assert payload['detail']['step'] == 'get_commons_content'
    assert payload['detail']['status_code'] == 502
    assert payload['detail']['host'] == 'canvas.ssu.ac.kr'


def test_api_official_viewer_plan_returns_commons_viewer_url(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post('/api/watch/viewer-plan', json={'course_id': 44070, 'module_item_id': 3268578, 'course_name': 'UX방법론'})

    assert response.status_code == 200
    payload = response.json()
    assert payload['ok'] is True
    assert payload['course_id'] == 44070
    assert payload['module_item_id'] == 3268578
    assert payload['attendance_item_id'] == '555'
    assert payload['title'] == '26년1학기_UX방법론_1주차'
    assert payload['viewer_url'].startswith('https://commons.ssu.ac.kr/')
    assert 'TargetUrl=' in payload['viewer_url']


def test_api_official_viewer_session_bridge_plan_returns_cookies_and_safe_diagnostics(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post('/api/watch/session-bridge-plan', json={'course_id': 44070, 'module_item_id': 3268578, 'course_name': 'UX방법론'})

    assert response.status_code == 200
    payload = response.json()
    assert payload['ok'] is True
    assert payload['viewer_url'].startswith('https://commons.ssu.ac.kr/')
    assert payload['launch_url'] == 'https://canvas.ssu.ac.kr/courses/44070/modules/items/3268578'
    assert payload['cookies'] == [
        {
            'name': 'xn_api_token',
            'value': 'raw-token',
            'domain': 'canvas.ssu.ac.kr',
            'path': '/',
            'secure': True,
            'http_only': True,
            'same_site': None,
        }
    ]
    assert payload['diagnostics'] == {
        'cookie_count': 1,
        'cookie_names': ['xn_api_token'],
        'cookie_domains': ['canvas.ssu.ac.kr'],
    }


def test_api_official_viewer_plan_returns_authentication_error_for_expired_session(tmp_path):
    service = ExpiredSessionFakeService()
    store = FakeCredentialStore(CredentialPair(username='student-id', password='secret-password'))
    app = create_app(service_factory=lambda: service, default_output_dir=tmp_path, credential_store=store)
    client = TestClient(app)

    response = client.post('/api/watch/viewer-plan', json={'course_id': 44070, 'module_item_id': 3268578})

    assert response.status_code == 401
    assert response.json() == {'ok': False, 'error_code': 'session_expired'}


def test_api_single_download_returns_streaming_progress_events(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post('/api/download', json={'course_id': 44070, 'module_item_id': 3268578, 'course_name': 'UX방법론'})

    assert response.status_code == 200
    payload = response.json()
    assert payload['ok'] is True
    assert payload['dry_run'] is False
    assert [event['stage'] for event in payload['progress']] == ['download_started', 'download_progress', 'download_done']
    assert payload['progress'][-1]['bytes_written'] == 330554102


def test_api_batch_plans_selected_downloads_with_progress(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post('/api/download/batch/plan', json={'course_id': 44070, 'course_name': 'UX방법론', 'module_item_ids': [3268578]})

    assert response.status_code == 200
    payload = response.json()
    assert payload['ok'] is True
    assert payload['dry_run'] is True
    assert payload['total_count'] == 1
    assert payload['success_count'] == 1
    assert payload['failure_count'] == 0
    assert payload['items'][0]['module_item_id'] == 3268578
    assert payload['items'][0]['written'] is False
    assert [event['stage'] for event in payload['progress']] == ['resolving', 'planned']


def test_api_batch_download_accepts_uncompleted_filter(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post('/api/download/batch', json={'course_id': 44070, 'course_name': 'UX방법론', 'mode': 'uncompleted'})

    assert response.status_code == 200
    payload = response.json()
    assert payload['ok'] is True
    assert payload['dry_run'] is False
    assert payload['items'][0]['written'] is True


def test_api_starts_polls_and_cancels_download_job(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    start = client.post('/api/download/jobs', json={'course_id': 44070, 'module_item_id': 3268578, 'course_name': 'UX방법론'})

    assert start.status_code == 200
    job_id = start.json()['job_id']
    assert start.json()['status'] in {'queued', 'running', 'succeeded'}

    status = client.get(f'/api/download/jobs/{job_id}')
    assert status.status_code == 200
    assert status.json()['job_id'] == job_id
    assert status.json()['status'] in {'queued', 'running', 'succeeded', 'failed'}
    assert isinstance(status.json()['progress'], list)

    cancelled = client.post(f'/api/download/jobs/{job_id}/cancel')
    assert cancelled.status_code == 200
    assert cancelled.json()['job_id'] == job_id
    assert cancelled.json()['cancel_requested'] is True


def test_api_starts_batch_download_job_with_progress(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    start = client.post('/api/download/batch/jobs', json={'course_id': 44070, 'course_name': 'UX방법론', 'module_item_ids': [3268578]})

    assert start.status_code == 200
    job_id = start.json()['job_id']
    assert start.json()['status'] in {'queued', 'running', 'succeeded'}

    status = client.get(f'/api/download/jobs/{job_id}')
    assert status.status_code == 200
    payload = status.json()
    assert payload['job_id'] == job_id
    assert payload['status'] in {'queued', 'running', 'succeeded', 'failed'}
    assert isinstance(payload['progress'], list)
    if payload['result'] is not None:
        assert payload['result']['total_count'] == 1
        assert payload['result']['items'][0]['module_item_id'] == 3268578


def test_api_returns_404_for_unknown_download_job(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.get('/api/download/jobs/missing')

    assert response.status_code == 404
    assert response.json()['error_code'] == 'download_job_not_found'


def test_api_health_exposes_capabilities_for_tauri_backend_compatibility(tmp_path):
    app = create_app(service_factory=lambda: FakeService(), default_output_dir=tmp_path, credential_store=FakeCredentialStore())
    client = TestClient(app)

    response = client.get('/api/health')

    assert response.status_code == 200
    payload = response.json()
    assert payload['ok'] is True
    assert payload['app'] == 'ssu-lecture-downloader'
    assert 'artifact_download_jobs' in payload['capabilities']
    assert 'stt_provider_status' in payload['capabilities']
    assert 'packaged_media_dependencies' in payload['capabilities']


def test_api_credentials_status_save_and_delete_without_leaking_values(tmp_path):
    store = FakeCredentialStore()
    app = create_app(service_factory=lambda: FakeService(), default_output_dir=tmp_path, credential_store=store)
    client = TestClient(app)

    status = client.get('/api/credentials/status')
    assert status.status_code == 200
    assert status.json() == {'ok': True, 'source': 'none', 'username_present': False, 'password_present': False, 'can_write': True}

    saved = client.post('/api/credentials', json={'username': 'student-id', 'password': 'secret-password'})
    assert saved.status_code == 200
    assert saved.json() == {'ok': True, 'source': 'test', 'username_present': True, 'password_present': True, 'can_write': True}
    assert store.saved == CredentialPair(username='student-id', password='secret-password')
    assert 'student-id' not in saved.text
    assert 'secret-password' not in saved.text

    deleted = client.delete('/api/credentials')
    assert deleted.status_code == 200
    assert deleted.json() == {'ok': True, 'source': 'none', 'username_present': False, 'password_present': False, 'can_write': True}
    assert store.deleted is True


def test_api_credentials_rejects_blank_values_without_leaking_values(tmp_path):
    store = FakeCredentialStore()
    app = create_app(service_factory=lambda: FakeService(), default_output_dir=tmp_path, credential_store=store)
    client = TestClient(app)

    response = client.post('/api/credentials', json={'username': ' ', 'password': 'secret-password'})

    assert response.status_code == 400
    assert response.json()['error_code'] == 'invalid_credentials_payload'
    assert 'secret-password' not in response.text


def test_api_reports_missing_credentials_without_values(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('SSU_USERNAME', raising=False)
    monkeypatch.delenv('SSU_PASSWORD', raising=False)
    app = create_app(service_factory=lambda: FakeService(), default_output_dir=tmp_path, credential_store=FakeCredentialStore())
    client = TestClient(app)

    response = client.get('/api/courses')

    assert response.status_code == 400
    payload = response.json()
    assert payload['ok'] is False
    assert payload['error_code'] == 'missing_credentials'
    assert 'present-user' not in response.text


def test_api_default_credentials_ignore_env_until_runtime_login(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('SSU_USERNAME', 'env-student-id')
    monkeypatch.setenv('SSU_PASSWORD', 'env-secret-password')
    app = create_app(service_factory=lambda: FakeService(), default_output_dir=tmp_path)
    client = TestClient(app)

    status = client.get('/api/credentials/status')
    assert status.status_code == 200
    assert status.json() == {'ok': True, 'source': 'memory', 'username_present': False, 'password_present': False, 'can_write': True}

    missing = client.get('/api/courses')
    assert missing.status_code == 400
    assert missing.json()['error_code'] == 'missing_credentials'
    assert 'env-student-id' not in missing.text
    assert 'env-secret-password' not in missing.text

    saved = client.post('/api/credentials', json={'username': 'runtime-id', 'password': 'runtime-password'})
    assert saved.status_code == 200
    assert saved.json() == {'ok': True, 'source': 'memory', 'username_present': True, 'password_present': True, 'can_write': True}

    deleted = client.delete('/api/credentials')
    assert deleted.status_code == 200
    assert deleted.json() == {'ok': True, 'source': 'memory', 'username_present': False, 'password_present': False, 'can_write': True}

    missing_after_logout = client.get('/api/courses')
    assert missing_after_logout.status_code == 400
    assert missing_after_logout.json()['error_code'] == 'missing_credentials'


def test_audio_extraction_job_succeeds_with_output_path(tmp_path, monkeypatch):
    from ssu_lecture_downloader.postprocess_media import AudioExtractionResult

    video = tmp_path / 'lecture.mp4'
    video.write_bytes(b'video')

    def fake_extract(request, on_progress=None, should_cancel=None):
        output = tmp_path / 'transcripts' / 'lecture.wav'
        output.parent.mkdir(exist_ok=True)
        output.write_bytes(b'audio')
        return AudioExtractionResult(ok=True, input_path=request.input_path, output_path=output, file_size=5)

    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.extract_audio', fake_extract)
    client = TestClient(create_app(service_factory=FakeService, credential_store=FakeCredentialStore()))

    started = client.post('/api/media/audio/jobs', json={'input_path': str(video), 'format': 'wav', 'overwrite': True}).json()
    assert started['job_id']
    status = client.get(f"/api/media/audio/jobs/{started['job_id']}").json()

    assert status['status'] == 'succeeded'
    assert status['result']['output_path'].endswith('lecture.wav')
    assert status['result']['file_size'] == 5


def test_stt_status_explains_how_to_install_whisper(monkeypatch):
    monkeypatch.setattr('ssu_lecture_downloader.stt.check_stt_provider', lambda provider='faster_whisper': STTProviderStatus(provider=provider, installed=False, available=False, install_hint='앱 패키지에 포함된 faster-whisper를 찾지 못했습니다.'))
    client = TestClient(create_app(service_factory=FakeService, credential_store=FakeCredentialStore()))

    response = client.get('/api/stt/providers/status')

    assert response.status_code == 200
    assert response.json()['providers'][0]['available'] is False
    assert '앱 패키지' in response.json()['providers'][0]['install_hint']


def test_media_dependency_status_reports_ffmpeg_and_stt(monkeypatch):
    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.ffmpeg_status', lambda: {'name': 'ffmpeg', 'installed': True, 'available': True, 'source': 'bundled', 'path': '/app/ffmpeg', 'install_hint': None})
    monkeypatch.setattr('ssu_lecture_downloader.stt.check_stt_provider', lambda provider='faster_whisper': STTProviderStatus(provider=provider, installed=provider == 'faster_whisper', available=provider == 'faster_whisper'))
    client = TestClient(create_app(service_factory=FakeService, credential_store=FakeCredentialStore()))

    response = client.get('/api/media/dependencies/status')

    assert response.status_code == 200
    payload = response.json()
    assert payload['packaging_mode'] == 'app_bundled'
    assert payload['dependencies'][0]['name'] == 'ffmpeg'
    assert payload['dependencies'][0]['source'] == 'bundled'
    assert payload['providers'][0]['provider'] == 'faster_whisper'


def test_stt_job_writes_transcript(tmp_path, monkeypatch):
    from ssu_lecture_downloader.stt import TranscriptSegment, TranscriptionResult

    audio = tmp_path / 'lecture.wav'
    audio.write_bytes(b'audio')

    def fake_transcribe(request, on_progress=None, should_cancel=None):
        text = tmp_path / 'lecture.transcript.txt'
        text.write_text('[00:00:00] 안녕하세요', encoding='utf-8')
        return TranscriptionResult(ok=True, audio_path=request.audio_path, text_path=text, md_path=None, json_path=None, segments=[TranscriptSegment(start=0, end=1, text='안녕하세요')])

    monkeypatch.setattr('ssu_lecture_downloader.stt.transcribe_audio', fake_transcribe)
    client = TestClient(create_app(service_factory=FakeService, credential_store=FakeCredentialStore()))

    started = client.post('/api/stt/jobs', json={'audio_path': str(audio), 'provider': 'fake', 'overwrite': True}).json()
    status = client.get(f"/api/stt/jobs/{started['job_id']}").json()

    assert status['status'] == 'succeeded'
    assert status['result']['text_path'].endswith('lecture.transcript.txt')
    assert status['result']['segments'][0]['text'] == '안녕하세요'



def test_artifact_download_job_supports_txt_only_selection(tmp_path, monkeypatch):
    from ssu_lecture_downloader.postprocess_media import AudioExtractionResult
    from ssu_lecture_downloader.stt import TranscriptSegment, TranscriptionResult

    def fake_extract(request, on_progress=None, should_cancel=None):
        output = Path(request.output_dir or tmp_path) / f'{request.input_path.stem}.mp3'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b'audio')
        if on_progress:
            on_progress({'stage': 'audio_progress', 'progress_percent': 100})
        return AudioExtractionResult(ok=True, input_path=request.input_path, output_path=output, file_size=5)

    def fake_transcribe(request, on_progress=None, should_cancel=None):
        text = Path(request.output_dir or tmp_path) / f'{request.audio_path.stem}.transcript.txt'
        text.write_text('[00:00:00] 안녕하세요', encoding='utf-8')
        if on_progress:
            on_progress({'stage': 'transcript_progress', 'progress_percent': 100, 'segment_count': 1, 'duration': 1})
        return TranscriptionResult(ok=True, audio_path=request.audio_path, text_path=text, md_path=None, json_path=None, segments=[TranscriptSegment(start=0, end=1, text='안녕하세요')])

    class ArtifactService(FakeService):
        def batch_download_lectures(self, *args, **kwargs):
            result = super().batch_download_lectures(*args, **kwargs)
            if not kwargs.get('dry_run'):
                output = tmp_path / 'UX방법론' / '26년1학기_UX방법론_1주차 [699c7d422c427].movie'
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b'video')
                item = result.items[0]
                result = BatchDownloadResult(**{**result.__dict__, 'items': [BatchDownloadItemResult(**{**item.__dict__, 'output_path': str(output), 'written': True, 'file_size': 5})]})
            return result

    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.extract_audio', fake_extract)
    monkeypatch.setattr('ssu_lecture_downloader.stt.transcribe_audio', fake_transcribe)
    client = TestClient(create_app(service_factory=ArtifactService, default_output_dir=tmp_path, credential_store=FakeCredentialStore(CredentialPair(username='u', password='p'))))

    started = client.post('/api/download/artifacts/jobs', json={'course_id': 44070, 'course_name': 'UX방법론', 'module_item_ids': [3268578], 'outputs': ['txt'], 'audio_format': 'mp3'}).json()
    status = {}
    for _ in range(20):
        status = client.get(f"/api/download/artifacts/jobs/{started['job_id']}").json()
        if status['status'] != 'running':
            break
        time.sleep(0.01)

    assert status['status'] == 'succeeded'
    artifacts_by_kind = {artifact['kind']: artifact for artifact in status['result']['artifacts']}
    assert set(artifacts_by_kind) == {'mp3', 'txt'}
    assert artifacts_by_kind['mp3']['output_path'].endswith('.mp3')
    assert artifacts_by_kind['txt']['text_path'].endswith('.transcript.txt')
    progress_stages = [event['stage'] for event in status['progress']]
    assert 'audio_progress' in progress_stages
    assert 'transcript_progress' in progress_stages
    assert not (tmp_path / 'UX방법론' / 'lecture.mp4').exists()
    assert not (tmp_path / 'UX방법론' / '26년1학기_UX방법론_1주차 [699c7d422c427].movie').exists()



def test_artifact_txt_reuses_existing_mp3_without_downloading_video(tmp_path, monkeypatch):
    from ssu_lecture_downloader.stt import TranscriptSegment, TranscriptionResult

    course_dir = tmp_path / 'UX방법론'
    course_dir.mkdir()
    existing_mp3 = course_dir / '26년1학기_UX방법론_1주차 [699c7d422c427].mp3'
    existing_mp3.write_bytes(b'existing-audio')
    calls = {'download': 0, 'extract': 0, 'transcribe': 0}

    class ExistingAudioService(FakeService):
        def batch_download_lectures(self, *args, **kwargs):
            if not kwargs.get('dry_run'):
                calls['download'] += 1
            return super().batch_download_lectures(*args, **kwargs)

    def fake_extract(request, on_progress=None, should_cancel=None):
        calls['extract'] += 1
        raise AssertionError('existing mp3 should be reused without audio extraction')

    def fake_transcribe(request, on_progress=None, should_cancel=None):
        calls['transcribe'] += 1
        assert request.audio_path == existing_mp3
        text = existing_mp3.with_name(f'{existing_mp3.stem}.transcript.txt')
        text.write_text('[00:00:00] 기존 mp3에서 생성', encoding='utf-8')
        if on_progress:
            on_progress({'stage': 'transcript_progress', 'progress_percent': 100, 'segment_count': 1, 'duration': 1})
        return TranscriptionResult(ok=True, audio_path=request.audio_path, text_path=text, md_path=None, json_path=None, segments=[TranscriptSegment(start=0, end=1, text='기존 mp3에서 생성')])

    monkeypatch.setattr('ssu_lecture_downloader.postprocess_media.extract_audio', fake_extract)
    monkeypatch.setattr('ssu_lecture_downloader.stt.transcribe_audio', fake_transcribe)
    client = TestClient(create_app(service_factory=ExistingAudioService, default_output_dir=tmp_path, credential_store=FakeCredentialStore(CredentialPair(username='u', password='p'))))

    started = client.post('/api/download/artifacts/jobs', json={'course_id': 44070, 'course_name': 'UX방법론', 'module_item_ids': [3268578], 'outputs': ['txt'], 'audio_format': 'mp3', 'overwrite_artifacts': False}).json()
    status = {}
    for _ in range(20):
        status = client.get(f"/api/download/artifacts/jobs/{started['job_id']}").json()
        if status['status'] != 'running':
            break
        time.sleep(0.01)

    assert status['status'] == 'succeeded'
    artifacts_by_kind = {artifact['kind']: artifact for artifact in status['result']['artifacts']}
    assert artifacts_by_kind['mp3']['skipped'] is True
    assert artifacts_by_kind['mp3']['output_path'] == str(existing_mp3)
    assert artifacts_by_kind['txt']['text_path'].endswith('.transcript.txt')
    assert calls == {'download': 0, 'extract': 0, 'transcribe': 1}
    progress_stages = [event['stage'] for event in status['progress']]
    assert 'artifact_downloads_skipped' in progress_stages
    assert 'audio_skipped' in progress_stages
    assert 'transcript_progress' in progress_stages
    assert not (course_dir / '26년1학기_UX방법론_1주차 [699c7d422c427].movie').exists()


def test_media_dependency_status_exposes_auto_install_availability(monkeypatch):
    monkeypatch.setattr('ssu_lecture_downloader.dependency_installer.install_supported', lambda: (True, None))
    client = TestClient(create_app(service_factory=FakeService, credential_store=FakeCredentialStore()))

    payload = client.get('/api/media/dependencies/status').json()

    assert payload['auto_install_available'] is True
    assert payload['auto_install_unavailable_reason'] is None


def test_media_dependency_install_job_installs_only_requested_missing(monkeypatch):
    calls = []

    def fake_install(components, on_progress=None, should_cancel=None):
        calls.append(components)
        if on_progress:
            on_progress({'stage': 'dependency_install_started', 'component': 'faster_whisper'})
        return {'ok': True, 'installed_components': ['faster_whisper'], 'missing_components': [], 'message': '필요한 구성요소 설치가 끝났습니다.'}

    monkeypatch.setattr('ssu_lecture_downloader.dependency_installer.install_missing_media_components', fake_install)
    client = TestClient(create_app(service_factory=FakeService, credential_store=FakeCredentialStore()))

    started = client.post('/api/media/dependencies/install/jobs', json={'components': ['faster-whisper']}).json()
    status = client.get(f"/api/media/dependencies/install/jobs/{started['job_id']}").json()

    assert calls == [['faster_whisper']]
    assert status['status'] == 'succeeded'
    assert status['result']['installed_components'] == ['faster_whisper']
    assert status['progress'][0]['stage'] == 'dependency_install_checking'


def test_media_dependency_install_job_rejects_unknown_component():
    client = TestClient(create_app(service_factory=FakeService, credential_store=FakeCredentialStore()))

    response = client.post('/api/media/dependencies/install/jobs', json={'components': ['java']})

    assert response.status_code == 400
    assert response.json()['error_code'] == 'unsupported_component'


def test_native_plan_runtime_missing_content_id_returns_actionable_message(tmp_path):
    class MissingContentService(FakeService):
        def native_download_plan(self, username, password, *, course_id, module_item_id, course_name, output_dir, conflict_strategy='rename'):
            raise RuntimeError('attendance item does not expose Commons content_id')

    store = FakeCredentialStore(CredentialPair(username='student-id', password='secret-password'))
    client = TestClient(create_app(service_factory=lambda: MissingContentService(), default_output_dir=tmp_path, credential_store=store))

    response = client.post('/api/download/native-plan', json={'course_id': 44080, 'module_item_id': 3265210, 'course_name': 'UX방법론'})

    assert response.status_code == 502
    payload = response.json()
    assert payload['error_code'] == 'media_content_id_missing'
    assert '다운로드 주소가 없습니다' in payload['message']
    assert payload['detail'] == {'step': 'parse_attendance_item_media', 'reason': 'content_id_missing'}


def test_append_compacted_progress_replaces_repeated_download_ticks():
    from ssu_lecture_downloader.jobs import append_compacted_progress as _append_compacted_progress

    progress = []

    _append_compacted_progress(
        progress,
        {'stage': 'download_progress', 'module_item_id': 1, 'output_path': '/tmp/a.mp4', 'bytes_written': 1024},
    )
    _append_compacted_progress(
        progress,
        {'stage': 'download_progress', 'module_item_id': 1, 'output_path': '/tmp/a.mp4', 'bytes_written': 2048},
    )

    assert progress == [
        {'stage': 'download_progress', 'module_item_id': 1, 'output_path': '/tmp/a.mp4', 'bytes_written': 2048}
    ]


def test_api_saving_the_same_credentials_again_keeps_the_service_session(tmp_path):
    service = ClearableFakeService()
    store = FakeCredentialStore()
    client = TestClient(create_app(service_factory=lambda: service, default_output_dir=tmp_path, credential_store=store))
    body = {'username': 'student-id', 'password': 'secret-password'}

    assert client.post('/api/credentials', json=body).status_code == 200
    assert client.post('/api/credentials', json=body).status_code == 200
    assert service.clear_calls == 1
    assert client.post('/api/credentials', json={**body, 'password': 'changed'}).status_code == 200
    assert service.clear_calls == 2


def test_api_refresh_flag_is_forwarded_only_when_requested(tmp_path):
    seen = []

    class RefreshAwareService(FakeService):
        def list_terms(self, username, password, **kwargs):
            seen.append(('terms', kwargs))
            return super().list_terms(username, password)

        def discover_courses(self, username, password, term_ids=None, **kwargs):
            seen.append(('courses', kwargs))
            return super().discover_courses(username, password, term_ids=term_ids)

        def get_course_detail(self, username, password, *, course_id, course_name=None, **kwargs):
            seen.append(('detail', kwargs))
            return super().get_course_detail(username, password, course_id=course_id, course_name=course_name)

    store = FakeCredentialStore(CredentialPair(username='student-id', password='secret-password'))
    client = TestClient(create_app(service_factory=RefreshAwareService, default_output_dir=tmp_path, credential_store=store))

    for path in ('/api/terms', '/api/courses', '/api/courses/44070'):
        assert client.get(path).status_code == 200
        assert client.get(path + '?refresh=true').status_code == 200

    assert seen == [
        ('terms', {}), ('terms', {'refresh': True}),
        ('courses', {}), ('courses', {'refresh': True}),
        ('detail', {}), ('detail', {'refresh': True}),
    ]


def test_api_deadlines_lists_items_and_forwards_term_and_refresh(tmp_path):
    from ssu_lecture_downloader.models import DeadlineItem
    from ssu_lecture_downloader.service import DeadlinesResult

    seen = []

    class DeadlineService(FakeService):
        def list_deadlines(self, username, password, term_ids=None, **kwargs):
            seen.append((term_ids, kwargs))
            item = DeadlineItem(course_id=44070, course_name='UX방법론', kind='quiz', title='퀴즈 1', module_item_id=9, due_at='2026-10-19T14:59:00Z', state='open')
            return DeadlinesResult(ok=True, error_code=None, deadlines=[item])

    store = FakeCredentialStore(CredentialPair(username='student-id', password='secret-password'))
    client = TestClient(create_app(service_factory=DeadlineService, default_output_dir=tmp_path, credential_store=store))

    plain = client.get('/api/deadlines')
    refreshed = client.get('/api/deadlines?term_id=46&refresh=true')

    assert plain.status_code == 200
    payload = plain.json()
    assert payload['ok'] is True and payload['deadline_count'] == 1 and payload['term_ids'] is None
    assert payload['deadlines'][0]['kind'] == 'quiz' and payload['deadlines'][0]['state'] == 'open'
    assert refreshed.json()['term_ids'] == [46]
    assert seen == [(None, {}), ([46], {'refresh': True})]


def test_api_deadlines_requires_credentials_and_reports_service_errors(tmp_path):
    from ssu_lecture_downloader.service import DeadlinesResult

    class FailingService(FakeService):
        def list_deadlines(self, username, password, term_ids=None, **kwargs):
            return DeadlinesResult(ok=False, error_code='session_expired', deadlines=[])

    anonymous = TestClient(create_app(service_factory=FakeService, default_output_dir=tmp_path, credential_store=FakeCredentialStore()))
    assert anonymous.get('/api/deadlines').status_code == 400

    store = FakeCredentialStore(CredentialPair(username='student-id', password='secret-password'))
    client = TestClient(create_app(service_factory=FailingService, default_output_dir=tmp_path, credential_store=store))
    response = client.get('/api/deadlines')
    assert response.status_code == 401 and response.json() == {'ok': False, 'error_code': 'session_expired'}


def test_course_detail_marks_lectures_that_are_already_on_disk(tmp_path):
    store = FakeCredentialStore(CredentialPair(username='student-id', password='secret-password'))

    class DetailService(FakeService):
        def get_course_detail(self, username, password, *, course_id, course_name=None):
            result = super().get_course_detail(username, password, course_id=course_id, course_name=course_name)
            items = [item.model_copy(update={'content_id': cid}) for item, cid in zip(result.content_items, ['abc123', 'zzz999'])]
            return CourseDetailResult(ok=True, error_code=None, course=result.course, lectures=result.lectures, content_items=items)

    course_dir = tmp_path / 'UX방법론'
    course_dir.mkdir()
    (course_dir / '1주차 [abc123].mp4').write_bytes(b'1234')
    (course_dir / '2주차 [zzz999].mp4.part').write_bytes(b'12')
    client = TestClient(create_app(service_factory=DetailService, default_output_dir=tmp_path, credential_store=store))

    items = client.get('/api/courses/44070', params={'course_name': 'UX방법론'}).json()['content_items']

    assert items[0]['downloaded_path'] == str(course_dir / '1주차 [abc123].mp4')
    assert items[1]['downloaded_path'] is None  # unfinished .part files do not count


def test_polling_a_finished_job_never_touches_the_service(tmp_path):
    store = FakeCredentialStore(CredentialPair(username='student-id', password='secret-password'))
    service = FakeService()
    client = TestClient(create_app(service_factory=lambda: service, default_output_dir=tmp_path, credential_store=store))
    job = client.post('/api/download/jobs', json={'course_id': 44070, 'module_item_id': 3268578, 'course_name': 'UX방법론'}).json()
    for _ in range(100):
        if client.get(f"/api/download/jobs/{job['job_id']}").json()['status'] not in {'queued', 'running'}:
            break
        time.sleep(0.02)

    for name in ('login', 'list_terms', 'discover_courses', 'get_course_detail', 'download_lecture', 'batch_download_lectures'):
        setattr(service, name, lambda *a, _n=name, **k: (_ for _ in ()).throw(AssertionError(f'status polling called service.{_n}')))
    polled = [client.get(f"/api/download/jobs/{job['job_id']}") for _ in range(5)]

    assert all(response.status_code == 200 and response.json()['status'] == 'succeeded' for response in polled)
