import threading
import time
from pathlib import Path

import pytest

from ssu_lecture_downloader.auth import SessionState
from ssu_lecture_downloader.lms import LearningXLister
from ssu_lecture_downloader.login_flow import LoginDiscoveryResult
import ssu_lecture_downloader.service as service_mod
from ssu_lecture_downloader.service import ResolvedMediaResult, SsuLectureService, _ResolvedMediaInternal


class FakeResponse:
    def __init__(self, payload=None, *, text='', status_code=200, headers=None, content=b'', url='https://canvas.ssu.ac.kr/'):
        self._payload = payload
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=1024 * 1024):
        midpoint = len(self.content) // 2 or len(self.content)
        if midpoint:
            yield self.content[:midpoint]
        if midpoint < len(self.content):
            yield self.content[midpoint:]


class FakeHttp:
    def __init__(self):
        self.cookies = {'xn_api_token': 'raw-token'}
        self.trace = type('Trace', (), {'to_json': lambda self: '[]'})()
        self.calls = []
        self.course_payloads_by_term = {}

    def get(self, step, url, **kwargs):
        self.calls.append(('GET', step, url, kwargs))
        if step == 'list_courses':
            term_ids = (kwargs.get('params') or {}).get('term_ids[]')
            if term_ids:
                return FakeResponse(self.course_payloads_by_term[term_ids[0]])
        if step == 'list_course_modules':
            return FakeResponse([
                {
                    'id': 10,
                    'name': '1주차',
                    'module_items': [
                        {
                            'id': 11,
                            'module_item_id': 111,
                            'title': '강의 1',
                            'content_type': 'attendance_item',
                            'completed': False,
                            'content_data': {'week_position': 1, 'lesson_position': 1, 'item_content_data': {'content_type': 'mp4', 'duration': 60, 'total_file_size': 1234}},
                        },
                        {
                            'id': 12,
                            'module_item_id': 112,
                            'title': '강의자료 PDF',
                            'content_type': 'attendance_item',
                            'completed': True,
                            'content_data': {'week_position': 1, 'lesson_position': 2, 'item_content_data': {'content_type': 'pdf', 'file_name': 'week1.pdf', 'total_file_size': 2048}},
                        },
                        {
                            'id': 13,
                            'module_item_id': 113,
                            'title': '과제 1',
                            'content_type': 'assignment',
                            'completed': False,
                            'content_data': {'week_position': 1, 'lesson_position': 3, 'due_at': '2026-06-01T00:00:00Z'},
                        },
                    ],
                }
            ])
        if step == 'fetch_module_item_html':
            return FakeResponse(text="""
                <form id='tool_form' action='https://canvas.ssu.ac.kr/learningx/lti/lecture_attendance/items/view/555'>
                  <input name='oauth_consumer_key' value='abc'>
                </form>
            """)
        if step == 'get_attendance_item_detail':
            return FakeResponse({
                'item_id': 555,
                'course_id': 42,
                'title': '테스트 강의',
                'item_content_id': 'learningx-content',
                'viewer_url': 'https://commons.ssu.ac.kr/viewer',
                'item_content_data': {'content_id': 'content-1', 'content_type': 'mp4', 'duration': 90},
            })
        if step == 'get_commons_content':
            return FakeResponse({'result': {'content_id': 'content-1', 'title': '테스트 강의', 'download_url': 'https://commons.ssu.ac.kr/download?redacted=1', 'total_file_size': 4}})
        if step == 'download_media':
            return FakeResponse(content=b'1234')
        raise AssertionError(step)

    def post(self, step, url, **kwargs):
        self.calls.append(('POST', step, url, kwargs))
        return FakeResponse(text='ok')

    def request(self, step, method, url, **kwargs):
        self.calls.append((method, step, url, kwargs))
        return FakeResponse(status_code=307, headers={'location': 'https://ssu-toast.commonscdn.com/video.mp4?redacted=1', 'content-type': 'video/mp4', 'content-length': '4'})


class CountingLogin:
    def __init__(self):
        self.calls = []

    def __call__(self, username, password, http):
        self.calls.append((username, password, http))
        http.cookies['xn_api_token'] = 'fresh-token'
        return LoginDiscoveryResult(ok=True, error_code=None, session=SessionState(cookie_names=['xn_api_token'], xn_api_token_present=True))


class CookieSensitiveLogin:
    def __init__(self):
        self.cookies_seen = []

    def __call__(self, username, password, http):
        self.cookies_seen.append(dict(http.cookies))
        if http.cookies.get('xn_api_token') == 'old-session-token':
            return LoginDiscoveryResult(ok=True, error_code=None, session=SessionState(cookie_names=['xn_api_token'], xn_api_token_present=True))
        return LoginDiscoveryResult(ok=False, error_code='smartid_login_rejected')


def ok_login(username, password, http):
    http.cookies['xn_api_token'] = 'fresh-token'
    return LoginDiscoveryResult(ok=True, error_code=None, session=SessionState(cookie_names=['xn_api_token'], xn_api_token_present=True))


def test_service_discovers_current_course_summaries_after_login():
    http = FakeHttp()
    http.course_payloads_by_term = {
        45: {'courses': [{'id': 1, 'name': '과거', 'term_id': 45, 'ended': True}]},
        46: {'courses': [{'id': 42, 'name': '현재 과목', 'term_id': 46, 'ended': False}]},
    }
    service = SsuLectureService(http=http, login_func=ok_login, current_term_candidates=[45, 46])

    result = service.discover_courses('user', 'password')

    assert result.ok is True
    assert result.course_count == 1
    assert result.courses[0].name == '현재 과목'
    assert result.courses[0].lecture_count == 1
    assert result.courses[0].uncompleted_lecture_count == 1



def test_service_reuses_login_session_and_course_cache_for_repeated_explicit_term_lookup():
    http = FakeHttp()
    http.course_payloads_by_term = {
        46: {'courses': [{'id': 42, 'name': '현재 과목', 'term_id': 46, 'ended': False}]},
    }
    login = CountingLogin()
    service = SsuLectureService(http=http, login_func=login, current_term_candidates=[46])

    first = service.discover_courses('user', 'password', term_ids=[46])
    second = service.discover_courses('user', 'password', term_ids=[46])

    assert first.course_count == 1
    assert second.course_count == 1
    assert len(login.calls) == 1
    assert [call[1] for call in http.calls].count('list_courses') == 1
    assert [call[1] for call in http.calls].count('list_course_modules') == 1


def test_service_clear_cache_forces_relogin_and_refetch():
    http = FakeHttp()
    http.course_payloads_by_term = {
        46: {'courses': [{'id': 42, 'name': '현재 과목', 'term_id': 46, 'ended': False}]},
    }
    login = CountingLogin()
    service = SsuLectureService(http=http, login_func=login, current_term_candidates=[46])

    service.discover_courses('user', 'password', term_ids=[46])
    service.clear_cache()
    service.discover_courses('user', 'password', term_ids=[46])

    assert len(login.calls) == 2
    assert [call[1] for call in http.calls].count('list_courses') == 2


def test_service_clears_stale_session_cookies_before_new_login_attempt():
    http = FakeHttp()
    http.cookies = {'xn_api_token': 'old-session-token'}
    login = CookieSensitiveLogin()
    service = SsuLectureService(http=http, login_func=login)

    result = service.login('user', 'wrong-password')

    assert result.ok is False
    assert result.error_code == 'smartid_login_rejected'
    assert login.cookies_seen == [{}]


def test_service_clear_cache_clears_http_cookies():
    http = FakeHttp()
    http.cookies = {'xn_api_token': 'old-session-token'}
    service = SsuLectureService(http=http, login_func=ok_login)

    service.clear_cache()

    assert http.cookies == {}


def test_service_caches_terms_without_rescanning_candidates():
    http = FakeHttp()
    http.course_payloads_by_term = {
        45: {'courses': [{'id': 1, 'name': '과거', 'term_id': 45, 'ended': True}]},
        46: {'courses': [{'id': 42, 'name': '현재 과목', 'term_id': 46, 'ended': False}]},
    }
    login = CountingLogin()
    service = SsuLectureService(http=http, login_func=login, current_term_candidates=[45, 46])

    first = service.list_terms('user', 'password')
    second = service.list_terms('user', 'password')

    assert first == second
    assert len(login.calls) == 1
    assert [call[1] for call in http.calls].count('list_courses') == 2


def test_service_retries_term_discovery_after_empty_cached_session():
    class EmptyThenCurrentHttp(FakeHttp):
        def __init__(self):
            super().__init__()
            self.list_courses_calls = 0

        def get(self, step, url, **kwargs):
            if step == 'list_courses':
                self.calls.append(('GET', step, url, kwargs))
                self.list_courses_calls += 1
                if self.list_courses_calls == 1:
                    return FakeResponse({'courses': []})
                return FakeResponse({'courses': [{'id': 42, 'name': '현재 과목', 'term_id': 46, 'ended': False}]})
            return super().get(step, url, **kwargs)

    http = EmptyThenCurrentHttp()
    login = CountingLogin()
    service = SsuLectureService(http=http, login_func=login, current_term_candidates=[46])

    terms = service.list_terms('user', 'password')

    assert len(login.calls) == 2
    assert terms == [{'term_id': 46, 'course_count': 1, 'current_course_count': 1, 'is_current': True, 'label': 'term 46'}]


def test_service_returns_course_detail_with_lecture_rows_after_login():
    service = SsuLectureService(http=FakeHttp(), login_func=ok_login)

    result = service.get_course_detail('user', 'password', course_id=42, course_name='현재 과목')

    assert result.ok is True
    assert result.course.id == 42
    assert result.course.name == '현재 과목'
    assert result.course.module_count == 1
    assert result.course.lecture_count == 1
    assert result.lectures[0].module_name == '1주차'
    assert result.lectures[0].module_item_id == 111
    assert result.lectures[0].title == '강의 1'
    assert result.content_items is not None
    assert [item.kind for item in result.content_items] == ['video', 'material', 'assignment']
    assert result.content_items[0].duration == 60
    assert result.content_items[1].downloadable is True
    assert result.content_items[1].resolvable is True
    assert result.content_items[1].file_name == 'week1.pdf'
    assert result.content_items[1].size_bytes == 2048
    assert result.content_items[2].downloadable is False


def test_lms_extracts_general_course_content_items_for_lms_like_rows():
    lister = LearningXLister(http=FakeHttp())

    items = lister.extract_course_content_items([
        {
            'module_id': 790135,
            'title': '1주차: 강의 소개',
            'week_position': 1,
            'module_items': [
                {
                    'module_item_id': 3268578,
                    'title': '영상 강의',
                    'content_type': 'attendance_item',
                    'completed': True,
                    'attendance_status': 'present',
                    'published': True,
                    'content_data': {'lesson_position': 1, 'item_content_data': {'content_type': 'movie', 'duration': 2309.57, 'total_file_size': 330554102}},
                },
                {
                    'module_item_id': 3268579,
                    'title': '강의자료',
                    'content_type': 'attendance_item',
                    'completed': True,
                    'content_data': {'lesson_position': 2, 'item_content_data': {'content_type': 'pdf', 'file_name': 'week1.pdf', 'total_file_size': '11525882'}},
                },
                {'module_item_id': 3268580, 'title': '질문 게시판', 'content_type': 'board', 'content_data': {}},
                {'module_item_id': 3268581, 'title': '퀴즈', 'content_type': 'quiz', 'content_data': {}},
                {'module_item_id': 3268582, 'title': '구분선', 'content_type': 'sub_header', 'content_data': {}},
            ],
        }
    ])

    assert [item.kind for item in items] == ['video', 'material', 'board', 'quiz', 'section']
    assert items[0].duration == 2309.57
    assert items[0].downloadable is True
    assert items[0].resolvable is True
    assert items[1].inner_content_type == 'pdf'
    assert items[1].file_name == 'week1.pdf'
    assert items[1].size_bytes == 11525882
    assert items[2].downloadable is False


def test_service_resolves_media_with_head_metadata_after_login():
    service = SsuLectureService(http=FakeHttp(), login_func=ok_login)

    result = service.resolve_media('user', 'password', course_id=42, module_item_id=111)

    assert result.ok is True
    assert result.attendance_item_id == '555'
    assert result.title == '테스트 강의'
    assert result.content_id == 'content-1'
    assert result.download_size == 4
    assert result.head_status == 307
    assert 'token=secret' not in result.download_url
    assert 'token=secret' not in (result.head_location or '')


def test_service_official_viewer_plan_returns_viewer_url_without_commons_download_lookup():
    http = FakeHttp()
    service = SsuLectureService(http=http, login_func=ok_login)

    result = service.official_viewer_plan('user', 'password', course_id=42, module_item_id=111)

    assert result.ok is True
    assert result.attendance_item_id == '555'
    assert result.title == '테스트 강의'
    assert result.content_id == 'content-1'
    assert result.viewer_url == 'https://commons.ssu.ac.kr/viewer'
    assert 'get_commons_content' not in [call[1] for call in http.calls]
    assert 'head_commons_download' not in [call[1] for call in http.calls]


def test_service_native_plan_uses_head_redirect_target_for_native_transfer(tmp_path: Path):
    service = SsuLectureService(http=FakeHttp(), login_func=ok_login)

    result = service.native_download_plan(
        'user',
        'password',
        course_id=42,
        module_item_id=111,
        course_name='자료/구조',
        output_dir=tmp_path,
    )

    assert result.result.ok is True
    assert result.native_download is not None
    assert result.native_download.download_url == 'https://ssu-toast.commonscdn.com/video.mp4?redacted=1'
    assert result.native_download.output_path.endswith('자료_구조/테스트 강의 [content-1].mp4')


def test_service_plans_and_downloads_lecture_to_safe_path(tmp_path: Path):
    service = SsuLectureService(http=FakeHttp(), login_func=ok_login)
    events = []

    result = service.download_lecture(
        'user',
        'password',
        course_id=42,
        module_item_id=111,
        course_name='자료/구조',
        output_dir=tmp_path,
        dry_run=False,
        on_progress=events.append,
    )

    assert result.ok is True
    assert result.written is True
    assert result.file_size == 4
    assert result.output_path.endswith('자료_구조/테스트 강의 [content-1].mp4')
    assert Path(result.output_path).read_bytes() == b'1234'
    assert 'download_progress' in [event['stage'] for event in events]
    assert events[-1]['stage'] == 'download_done'
    assert events[-1]['bytes_written'] == 4


def test_service_skips_existing_file_without_downloading_when_requested(tmp_path: Path):
    http = FakeHttp()
    service = SsuLectureService(http=http, login_func=ok_login)
    existing_path = tmp_path / '자료_구조' / '테스트 강의 [content-1].mp4'
    existing_path.parent.mkdir(parents=True)
    existing_path.write_bytes(b'existing')
    events = []

    result = service.download_lecture(
        'user',
        'password',
        course_id=42,
        module_item_id=111,
        course_name='자료/구조',
        output_dir=tmp_path,
        dry_run=False,
        conflict_strategy='skip',
        on_progress=events.append,
    )

    assert result.ok is True
    assert result.written is False
    assert result.skipped is True
    assert result.file_exists is True
    assert result.file_size == len(b'existing')
    assert Path(result.output_path).read_bytes() == b'existing'
    assert 'download_media' not in [call[1] for call in http.calls]
    assert events[-1]['stage'] == 'download_skipped'


def test_service_batch_plans_selected_lectures_with_progress_without_writing(tmp_path: Path):
    http = FakeHttp()
    events = []
    service = SsuLectureService(http=http, login_func=ok_login)

    result = service.batch_download_lectures(
        'user',
        'password',
        course_id=42,
        course_name='자료/구조',
        output_dir=tmp_path,
        module_item_ids=[111],
        dry_run=False,
        on_progress=events.append,
    )

    assert result.ok is True
    assert result.items[0].written is True
    assert result.items[0].file_size == 4
    assert 'download_progress' in [event['stage'] for event in events]
    assert events[-1]['stage'] == 'done'
    assert Path(result.items[0].output_path).read_bytes() == b'1234'


def test_service_plans_pdf_material_to_pdf_path(tmp_path: Path):
    class PdfHttp(FakeHttp):
        def get(self, step, url, **kwargs):
            if step == 'get_attendance_item_detail':
                return FakeResponse({
                    'item_id': 556,
                    'course_id': 42,
                    'title': '강의자료 PDF',
                    'item_content_id': 'learningx-pdf',
                    'viewer_url': 'https://commons.ssu.ac.kr/viewer/pdf',
                    'item_content_data': {'content_id': 'pdf-content-1', 'content_type': 'pdf'},
                })
            if step == 'get_commons_content':
                return FakeResponse({'result': {'content_id': 'pdf-content-1', 'title': '강의자료 PDF', 'download_url': 'https://commons.ssu.ac.kr/download?redacted=1', 'total_file_size': 2048}})
            return super().get(step, url, **kwargs)

    service = SsuLectureService(http=PdfHttp(), login_func=ok_login)

    result = service.download_lecture(
        'user',
        'password',
        course_id=42,
        module_item_id=112,
        course_name='자료/구조',
        output_dir=tmp_path,
        dry_run=True,
    )

    assert result.ok is True
    assert result.written is False
    assert result.download_size == 2048
    assert result.output_path.endswith('자료_구조/강의자료 PDF [pdf-content-1].pdf')


def test_service_plans_readystream_video_to_mp4_path(tmp_path: Path):
    class ReadystreamHttp(FakeHttp):
        def get(self, step, url, **kwargs):
            if step == 'get_attendance_item_detail':
                return FakeResponse({
                    'item_id': 557,
                    'course_id': 42,
                    'title': '계약의 기본조건 2',
                    'item_content_id': 'learningx-readystream',
                    'viewer_url': 'https://commons.ssu.ac.kr/viewer/readystream',
                    'item_content_data': {'content_id': '63b26d5c58048', 'content_type': 'readystream', 'duration': 2809},
                })
            if step == 'get_commons_content':
                return FakeResponse({'result': {'content_id': '63b26d5c58048', 'title': '계약의 기본조건 2', 'download_url': 'https://commons.ssu.ac.kr/download?redacted=1', 'total_file_size': 1903378432}})
            return super().get(step, url, **kwargs)

    service = SsuLectureService(http=ReadystreamHttp(), login_func=ok_login)

    result = service.download_lecture(
        'user',
        'password',
        course_id=42,
        module_item_id=111,
        course_name='국제상무론',
        output_dir=tmp_path,
        dry_run=True,
    )

    assert result.ok is True
    assert result.written is False
    assert result.output_path.endswith('국제상무론/계약의 기본조건 2 [63b26d5c58048].mp4')


def test_service_batch_can_plan_selected_pdf_material(tmp_path: Path):
    class PdfHttp(FakeHttp):
        def get(self, step, url, **kwargs):
            if step == 'get_attendance_item_detail':
                return FakeResponse({
                    'item_id': 556,
                    'course_id': 42,
                    'title': '강의자료 PDF',
                    'item_content_id': 'learningx-pdf',
                    'viewer_url': 'https://commons.ssu.ac.kr/viewer/pdf',
                    'item_content_data': {'content_id': 'pdf-content-1', 'content_type': 'pdf'},
                })
            if step == 'get_commons_content':
                return FakeResponse({'result': {'content_id': 'pdf-content-1', 'title': '강의자료 PDF', 'download_url': 'https://commons.ssu.ac.kr/download?redacted=1', 'total_file_size': 2048}})
            return super().get(step, url, **kwargs)

    events = []
    service = SsuLectureService(http=PdfHttp(), login_func=ok_login)

    result = service.batch_download_lectures(
        'user',
        'password',
        course_id=42,
        course_name='자료/구조',
        output_dir=tmp_path,
        module_item_ids=[112],
        dry_run=True,
        on_progress=events.append,
    )

    assert result.ok is True
    assert [item.module_item_id for item in result.items] == [112]
    assert result.items[0].written is False
    assert result.items[0].output_path.endswith('자료_구조/강의자료 PDF [pdf-content-1].pdf')
    assert any(event['stage'] == 'planned' for event in events)
    assert events[-1]['stage'] == 'batch_prepare_done'
    assert events[-1]['resolved_count'] == 1


def test_service_batch_defaults_to_uncompleted_lectures_only(tmp_path: Path):
    class MixedHttp(FakeHttp):
        def get(self, step, url, **kwargs):
            if step == 'list_course_modules':
                return FakeResponse([
                    {
                        'id': 10,
                        'name': '1주차',
                        'module_items': [
                            {
                                'id': 11,
                                'module_item_id': 111,
                                'title': '미완료 강의',
                                'content_type': 'attendance_item',
                                'completed': False,
                                'content_data': {'item_content_data': {'content_type': 'mp4', 'duration': 60, 'total_file_size': 1024}},
                            },
                            {
                                'id': 12,
                                'module_item_id': 222,
                                'title': '완료 강의',
                                'content_type': 'attendance_item',
                                'completed': True,
                                'content_data': {'item_content_data': {'content_type': 'mp4', 'duration': 70}},
                            },
                        ],
                    }
                ])
            return super().get(step, url, **kwargs)

    service = SsuLectureService(http=MixedHttp(), login_func=ok_login)

    result = service.batch_download_lectures(
        'user',
        'password',
        course_id=42,
        course_name='자료구조',
        output_dir=tmp_path,
        dry_run=True,
    )

    assert [item.module_item_id for item in result.items] == [111]


def test_service_batch_downloads_files_in_parallel_with_per_file_progress(tmp_path: Path, monkeypatch):
    service = SsuLectureService(http=FakeHttp(), login_func=ok_login)
    events = []
    lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_resolve(client, *, course_id, module_item_id):
        return _ResolvedMediaInternal(
            result=ResolvedMediaResult(
                ok=True,
                error_code=None,
                course_id=course_id,
                module_item_id=module_item_id,
                attendance_item_id=str(module_item_id),
                title=f'강의 {module_item_id}',
                content_id=f'content-{module_item_id}',
                content_type='mp4',
                duration=60,
                download_url=f'https://commons.ssu.ac.kr/download/{module_item_id}',
                download_size=4,
                head_status=307,
                head_content_type='video/mp4',
                head_content_length='4',
            ),
            raw_download_url=f'https://ssu-toast.commonscdn.com/{module_item_id}.mp4',
            referer=None,
            extension='mp4',
        )

    download_attempt_settings = []

    def fake_download_to_plan(*, http, download_url, plan, referer=None, max_attempts=1, on_progress=None, should_cancel=None):
        nonlocal active, max_active
        download_attempt_settings.append(max_attempts)
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            if on_progress:
                on_progress({'stage': 'download_progress', 'bytes_written': 2, 'total_bytes': 4, 'output_path': str(plan.file_path)})
            time.sleep(0.08)
            plan.file_path.parent.mkdir(parents=True, exist_ok=True)
            plan.file_path.write_bytes(b'1234')
            if on_progress:
                on_progress({'stage': 'download_done', 'bytes_written': 4, 'total_bytes': 4, 'output_path': str(plan.file_path)})
            return plan.file_path
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(service, '_resolve_media_with', fake_resolve)
    monkeypatch.setattr(service_mod, 'download_to_plan', fake_download_to_plan)

    result = service.batch_download_lectures(
        'user',
        'password',
        course_id=42,
        course_name='자료/구조',
        output_dir=tmp_path,
        module_item_ids=[111, 112],
        dry_run=False,
        on_progress=events.append,
        max_parallel_downloads=2,
    )

    assert result.ok is True
    assert [item.module_item_id for item in result.items] == [111, 112]
    assert all(item.written for item in result.items)
    assert max_active == 2
    assert download_attempt_settings == [service_mod.MEDIA_DOWNLOAD_MAX_ATTEMPTS, service_mod.MEDIA_DOWNLOAD_MAX_ATTEMPTS]
    assert any(event['stage'] == 'batch_prepare_done' and event['resolved_count'] == 2 and event['download_count'] == 2 for event in events)
    assert any(event['stage'] == 'batch_downloads_started' and event['max_parallel_downloads'] == 2 and event['download_count'] == 2 for event in events)
    progress_events = [event for event in events if event['stage'] == 'download_progress']
    assert {event['module_item_id'] for event in progress_events} == {111, 112}
    assert all(event['total'] == 2 for event in progress_events)
    assert all(event['title'] for event in progress_events)


def test_service_resolves_download_from_module_metadata_when_attendance_detail_is_unavailable(tmp_path: Path):
    class ModuleMetadataHttp(FakeHttp):
        def get(self, step, url, **kwargs):
            if step == 'list_course_modules':
                return FakeResponse([
                    {
                        'module_id': 839322,
                        'title': '14주차',
                        'module_items': [
                            {
                                'module_item_id': 3265047,
                                'title': '취업에 필요한 재무제표 이해_14주차_1차시',
                                'content_type': 'attendance_item',
                                'completed': True,
                                'url': 'https://canvas.ssu.ac.kr/learningx/lti/lecture_attendance/items/view/632521',
                                'content_data': {
                                    'item_id': 632521,
                                    'course_id': 44080,
                                    'item_content_id': 'learningx-readystream',
                                    'item_content_data': {
                                        'content_id': '6773aeec2250e',
                                        'content_type': 'readystream',
                                        'view_url': 'https://commons.ssu.ac.kr/em/6773aeec2250e',
                                        'duration': 2646.47,
                                        'title': '취업에 필요한 재무제표 이해_14주차_1차시',
                                        'total_file_size': 464279393,
                                    },
                                },
                            },
                        ],
                    }
                ])
            if step == 'get_attendance_item_detail':
                return FakeResponse({}, status_code=404)
            if step == 'get_commons_content':
                return FakeResponse({'result': {'content_id': '6773aeec2250e', 'title': '취업에 필요한 재무제표 이해_14주차_1차시', 'download_url': 'https://commons.ssu.ac.kr/download?redacted=1', 'total_file_size': 464279393}})
            return super().get(step, url, **kwargs)

    service = SsuLectureService(http=ModuleMetadataHttp(), login_func=ok_login)

    result = service.native_download_plan(
        'user',
        'password',
        course_id=44080,
        module_item_id=3265047,
        course_name='취업에필요한재무제표이해',
        output_dir=tmp_path,
        conflict_strategy='overwrite',
    )

    assert result.result.ok is True
    assert result.result.attendance_item_id == '632521'
    assert result.result.content_id == '6773aeec2250e'
    assert result.result.download_size == 464279393
    assert result.result.output_path.endswith('.mp4')
    assert result.native_download is not None
    assert result.native_download.referer == 'https://commons.ssu.ac.kr/em/6773aeec2250e'
    assert not any(call[1] == 'get_attendance_item_detail' for call in service.http.calls)


# --- concurrency, session lifetime and recovery -------------------------------------------

import types

from curl_cffi.requests import exceptions as curl_exceptions

from ssu_lecture_downloader.http_client import HttpClient


def make_http_error(status, url):
    error = curl_exceptions.HTTPError(f'HTTP {status}')
    error.response = types.SimpleNamespace(status_code=status, url=url)
    return error


class StatusResponse(FakeResponse):
    """A response whose raise_for_status raises curl_cffi's HTTPError like the real client."""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise make_http_error(self.status_code, self.url)


def test_service_serializes_cold_cache_logins_and_never_clears_cookies_under_a_running_request():
    state = {'active_logins': 0, 'max_active_logins': 0, 'logins': 0}
    guard = threading.Lock()
    failures = []

    def slow_login(username, password, http):
        with guard:
            state['active_logins'] += 1
            state['max_active_logins'] = max(state['max_active_logins'], state['active_logins'])
            state['logins'] += 1
        time.sleep(0.05)
        http.cookies['xn_api_token'] = 'fresh-token'
        with guard:
            state['active_logins'] -= 1
        return LoginDiscoveryResult(ok=True, error_code=None, session=SessionState(cookie_names=['xn_api_token'], xn_api_token_present=True))

    class CookieCheckingHttp(FakeHttp):
        def get(self, step, url, **kwargs):
            if step in {'list_courses', 'list_course_modules'}:
                time.sleep(0.01)
                if not self.cookies.get('xn_api_token'):
                    failures.append(step)
            return super().get(step, url, **kwargs)

    http = CookieCheckingHttp()
    http.cookies = {}
    http.course_payloads_by_term = {46: {'courses': [{'id': 42, 'name': '현재 과목', 'term_id': 46, 'ended': False}]}}
    service = SsuLectureService(http=http, login_func=slow_login, current_term_candidates=[46])
    errors = []

    def call(index):
        try:
            if index % 2:
                service.list_terms('user', 'password')
            else:
                service.discover_courses('user', 'password')
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=call, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors
    assert failures == []
    assert state['max_active_logins'] == 1
    assert state['logins'] == 1


def test_service_clear_cache_waits_for_a_running_session_user():
    entered = threading.Event()
    release = threading.Event()

    class BlockingHttp(FakeHttp):
        def get(self, step, url, **kwargs):
            if step == 'list_course_modules':
                entered.set()
                release.wait(5)
                assert self.cookies.get('xn_api_token'), 'cookies were wiped under a running request'
            return super().get(step, url, **kwargs)

    service = SsuLectureService(http=BlockingHttp(), login_func=ok_login)
    results = {}
    worker = threading.Thread(target=lambda: results.setdefault('detail', service.get_course_detail('user', 'password', course_id=42)))
    worker.start()
    assert entered.wait(5)
    cleared = threading.Thread(target=service.clear_cache)
    cleared.start()
    cleared.join(timeout=0.2)
    assert cleared.is_alive(), 'clear_cache must wait for the in-flight request'
    release.set()
    worker.join(5)
    cleared.join(5)

    assert results['detail'].ok is True
    assert not cleared.is_alive()


def test_service_renews_login_after_session_ttl():
    http = FakeHttp()
    login = CountingLogin()
    service = SsuLectureService(http=http, login_func=login, session_ttl_seconds=0)

    service.login('user', 'password')
    service.login('user', 'password')

    assert len(login.calls) == 2


def test_service_keeps_login_within_session_ttl():
    login = CountingLogin()
    service = SsuLectureService(http=FakeHttp(), login_func=login, session_ttl_seconds=60)

    service.login('user', 'password')
    service.login('user', 'password')

    assert len(login.calls) == 1


def test_service_relogs_in_once_and_retries_when_lms_rejects_the_session():
    class ExpiringHttp(FakeHttp):
        def __init__(self):
            super().__init__()
            self.module_calls = 0

        def get(self, step, url, **kwargs):
            if step == 'list_course_modules':
                self.module_calls += 1
                if self.module_calls == 1:
                    return StatusResponse([], status_code=401, url='https://canvas.ssu.ac.kr/learningx/api/v1/courses/42/modules')
            return super().get(step, url, **kwargs)

    http = ExpiringHttp()
    login = CountingLogin()
    service = SsuLectureService(http=http, login_func=login)

    result = service.get_course_detail('user', 'password', course_id=42, course_name='현재 과목')

    assert result.ok is True
    assert len(login.calls) == 2
    assert http.module_calls == 2


def test_service_does_not_retry_forever_when_the_lms_keeps_rejecting_the_session():
    class AlwaysExpiredHttp(FakeHttp):
        def get(self, step, url, **kwargs):
            if step == 'list_course_modules':
                return StatusResponse([], status_code=403, url='https://canvas.ssu.ac.kr/x')
            return super().get(step, url, **kwargs)

    login = CountingLogin()
    service = SsuLectureService(http=AlwaysExpiredHttp(), login_func=login)

    with pytest.raises(curl_exceptions.HTTPError):
        service.get_course_detail('user', 'password', course_id=42)

    assert len(login.calls) == 2


def test_cdn_403_does_not_count_as_lms_session_expiry():
    class CdnForbiddenHttp(FakeHttp):
        def get(self, step, url, **kwargs):
            if step == 'list_course_modules':
                return StatusResponse([], status_code=403, url='https://ssu-toast.commonscdn.com/video.mp4?sig=x')
            return super().get(step, url, **kwargs)

    login = CountingLogin()
    service = SsuLectureService(http=CdnForbiddenHttp(), login_func=login)

    with pytest.raises(curl_exceptions.HTTPError):
        service.get_course_detail('user', 'password', course_id=42)

    assert len(login.calls) == 1


def fake_resolved(module_item_id, *, raw_url='https://ssu-toast.commonscdn.com/video.mp4?sig=old'):
    return _ResolvedMediaInternal(
        result=ResolvedMediaResult(
            ok=True,
            error_code=None,
            course_id=42,
            module_item_id=module_item_id,
            attendance_item_id='555',
            title='강의',
            content_id='content-1',
            content_type='mp4',
            duration=60,
            download_url='https://commons.ssu.ac.kr/download',
            download_size=4,
        ),
        raw_download_url=raw_url,
        referer=None,
        extension='mp4',
    )


def test_expired_signed_media_url_is_resolved_again_exactly_once(tmp_path: Path, monkeypatch):
    login = CountingLogin()
    service = SsuLectureService(http=FakeHttp(), login_func=login)
    resolve_calls = []
    download_urls = []

    def fake_resolve(*, course_id, module_item_id, refresh=False):
        resolve_calls.append(module_item_id)
        signature = 'old' if len(resolve_calls) == 1 else 'new'
        return fake_resolved(module_item_id, raw_url=f'https://ssu-toast.commonscdn.com/video.mp4?sig={signature}')

    def fake_download(*, http, download_url, plan, referer=None, max_attempts=1, on_progress=None, should_cancel=None):
        download_urls.append(download_url)
        if len(download_urls) == 1:
            raise make_http_error(403, download_url)
        plan.file_path.parent.mkdir(parents=True, exist_ok=True)
        plan.file_path.write_bytes(b'1234')
        return plan.file_path

    monkeypatch.setattr(service, '_resolve_media_authenticated', fake_resolve)
    monkeypatch.setattr(service_mod, 'download_to_plan', fake_download)
    events = []

    result = service.download_lecture('user', 'password', course_id=42, module_item_id=111, course_name='과목', output_dir=tmp_path, dry_run=False, on_progress=events.append)

    assert result.ok is True and result.written is True
    assert resolve_calls == [111, 111]
    assert download_urls == ['https://ssu-toast.commonscdn.com/video.mp4?sig=old', 'https://ssu-toast.commonscdn.com/video.mp4?sig=new']
    assert len(login.calls) == 1, 'a CDN 403 must not trigger a new LMS login'
    assert any(event['stage'] == 'download_reresolving' for event in events)


def test_signed_media_url_failure_is_not_retried_a_second_time(tmp_path: Path, monkeypatch):
    service = SsuLectureService(http=FakeHttp(), login_func=ok_login)
    attempts = []

    def fake_download(*, http, download_url, plan, referer=None, max_attempts=1, on_progress=None, should_cancel=None):
        attempts.append(download_url)
        raise make_http_error(403, download_url)

    monkeypatch.setattr(service, '_resolve_media_authenticated', lambda *, course_id, module_item_id, refresh=False: fake_resolved(module_item_id))
    monkeypatch.setattr(service_mod, 'download_to_plan', fake_download)

    with pytest.raises(curl_exceptions.HTTPError):
        service.download_lecture('user', 'password', course_id=42, module_item_id=111, course_name='과목', output_dir=tmp_path, dry_run=False)

    assert len(attempts) == 2


def test_downloads_use_a_private_http_client_that_is_closed_afterwards(tmp_path: Path, monkeypatch):
    shared = HttpClient()
    service = SsuLectureService(http=shared, login_func=ok_login)
    used = []
    closed = []
    monkeypatch.setattr(HttpClient, 'close', lambda self: closed.append(self))

    def fake_download(*, http, download_url, plan, referer=None, max_attempts=1, on_progress=None, should_cancel=None):
        used.append(http)
        plan.file_path.parent.mkdir(parents=True, exist_ok=True)
        plan.file_path.write_bytes(b'1234')
        return plan.file_path

    monkeypatch.setattr(service, '_resolve_media_authenticated', lambda *, course_id, module_item_id, refresh=False: fake_resolved(module_item_id))
    monkeypatch.setattr(service_mod, 'download_to_plan', fake_download)

    service.download_lecture('user', 'password', course_id=42, module_item_id=111, course_name='과목', output_dir=tmp_path, dry_run=False)

    assert len(used) == 1 and used[0] is not shared
    assert closed == used
    shared.session.close()


def test_term_scan_runs_with_bounded_parallelism_and_is_shared_with_current_courses():
    state = {'active': 0, 'max_active': 0, 'scans': 0}
    guard = threading.Lock()

    class SlowTermHttp(FakeHttp):
        def get(self, step, url, **kwargs):
            if step == 'list_courses':
                with guard:
                    state['scans'] += 1
                    state['active'] += 1
                    state['max_active'] = max(state['max_active'], state['active'])
                time.sleep(0.03)
                with guard:
                    state['active'] -= 1
                term_id = kwargs['params']['term_ids[]'][0]
                if term_id == 47:
                    raise RuntimeError('this term id is unavailable')
            return super().get(step, url, **kwargs)

    http = SlowTermHttp()
    http.course_payloads_by_term = {
        term_id: {'courses': [{'id': term_id, 'name': f'과목 {term_id}', 'term_id': term_id, 'ended': term_id != 52}]}
        for term_id in range(44, 54)
    }
    service = SsuLectureService(http=http, login_func=ok_login, current_term_candidates=list(range(44, 54)), term_scan_workers=4)

    terms = service.list_terms('user', 'password')
    current = service.discover_courses('user', 'password')

    assert [term['term_id'] for term in terms] == [term_id for term_id in range(44, 54) if term_id != 47]
    assert [term['term_id'] for term in terms if term['is_current']] == [52]
    assert 1 < state['max_active'] <= 4
    # 9 successful terms are scanned once and reused; only the failing term 47 is retried.
    assert [call[1] for call in http.calls].count('list_courses') == 9
    assert state['scans'] == 11
    assert current.ok is True and current.course_count == 1


def test_batch_fetches_course_modules_once_for_all_items(tmp_path: Path):
    def item(module_item_id, content_id):
        return {
            'module_item_id': module_item_id,
            'title': f'강의 {module_item_id}',
            'content_type': 'attendance_item',
            'completed': False,
            'url': f'https://canvas.ssu.ac.kr/learningx/lti/lecture_attendance/items/view/{module_item_id}',
            'content_data': {
                'item_id': module_item_id,
                'course_id': 42,
                'item_content_data': {'content_id': content_id, 'content_type': 'mp4', 'duration': 60, 'total_file_size': 4},
            },
        }

    class MetadataHttp(FakeHttp):
        def get(self, step, url, **kwargs):
            self.calls.append(('GET', step, url, kwargs))
            if step == 'list_course_modules':
                return FakeResponse([{'id': 10, 'name': '1주차', 'module_items': [item(111, 'content-a'), item(112, 'content-b'), item(113, 'content-c')]}])
            if step == 'get_commons_content':
                content_id = url.rsplit('=', 1)[-1]
                return FakeResponse({'result': {'content_id': content_id, 'title': f'강의 {content_id}', 'download_url': f'https://commons.ssu.ac.kr/download/{content_id}', 'total_file_size': 4}})
            self.calls.pop()
            return super().get(step, url, **kwargs)

    http = MetadataHttp()
    service = SsuLectureService(http=http, login_func=ok_login)

    result = service.batch_download_lectures('user', 'password', course_id=42, course_name='과목', output_dir=tmp_path, dry_run=True, module_item_ids=[111, 112, 113])

    assert result.ok is True and result.total_count == 3
    assert [call[1] for call in http.calls].count('list_course_modules') == 1


def test_batch_cancel_before_downloads_start_opens_no_connections(tmp_path: Path, monkeypatch):
    service = SsuLectureService(http=FakeHttp(), login_func=ok_login)
    cancel = {'on': False}
    downloads = []

    def resolve(*, course_id, module_item_id):
        return fake_resolved(module_item_id)

    def fake_download(**kwargs):
        downloads.append(kwargs)

    def should_cancel():
        return cancel['on']

    def on_progress(event):
        if event['stage'] == 'batch_downloads_started':
            cancel['on'] = True

    monkeypatch.setattr(service, '_resolve_media_authenticated', resolve)
    monkeypatch.setattr(service_mod, 'download_to_plan', fake_download)

    service.batch_download_lectures('user', 'password', course_id=42, course_name='과목', output_dir=tmp_path, dry_run=False, module_item_ids=[111], on_progress=on_progress, should_cancel=should_cancel)

    assert downloads == []
