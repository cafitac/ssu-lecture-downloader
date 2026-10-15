"""Upstream request-count regressions: what the service asks the LMS, and how often."""

import threading
import time
from collections import Counter
from pathlib import Path

from ssu_lecture_downloader.auth import SessionState
from ssu_lecture_downloader.login_flow import LoginDiscoveryResult
from ssu_lecture_downloader.service import SsuLectureService

COURSE_IDS = (101, 102, 103)


class Response:
    def __init__(self, payload=None, *, status_code=200, headers=None, content=b''):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content
        self.url = 'https://canvas.ssu.ac.kr/'

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=1024):
        yield self.content


def modules_of(course_id):
    items = [
        {
            'id': course_id * 10 + n,
            'module_item_id': course_id * 10 + n,
            'title': f'강의 {course_id}-{n}',
            'content_type': 'attendance_item',
            'completed': False,
            'content_data': {'item_content_data': {'content_id': f'c{course_id}{n}', 'content_type': 'mp4', 'duration': 60, 'total_file_size': 4}},
        }
        for n in range(3)
    ]
    return [{'id': course_id, 'name': '1주차', 'module_items': items}]


class Upstream:
    """Counts every request (by step) over all clients and the peak number in flight."""

    def __init__(self, latency=0.0):
        self.latency = latency
        self.steps = Counter()
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()
        self.logins = 0

    def count(self, step):
        return self.steps[step]

    def total(self):
        return sum(self.steps.values())

    def reset(self):
        with self.lock:
            self.steps.clear()
            self.peak = 0

    def client(self):
        return FakeClient(self)


class FakeClient:
    def __init__(self, upstream):
        self.upstream = upstream
        self.cookies = {'xn_api_token': 'token'}
        self.trace = None

    def close(self):
        pass

    def _hit(self, step):
        upstream = self.upstream
        with upstream.lock:
            upstream.steps[step] += 1
            upstream.active += 1
            upstream.peak = max(upstream.peak, upstream.active)
        try:
            if upstream.latency:
                time.sleep(upstream.latency)
        finally:
            with upstream.lock:
                upstream.active -= 1

    def get(self, step, url, **kwargs):
        self._hit(step)
        if step == 'list_courses':
            term = (kwargs.get('params') or {}).get('term_ids[]', [None])[0]
            if term == 46:
                return Response({'courses': [{'id': cid, 'name': f'과목{cid}', 'ended': False, 'term': {'name': '2026년 2학기'}} for cid in COURSE_IDS]})
            return Response({'courses': []})
        if step == 'list_course_modules':
            return Response(modules_of(int(url.split('/courses/')[1].split('/')[0])))
        if step == 'get_commons_content':
            content_id = url.rsplit('=', 1)[-1]
            return Response({'result': {'content_id': content_id, 'title': f'T {content_id}', 'download_url': f'https://commons.ssu.ac.kr/dl/{content_id}', 'total_file_size': 4}})
        if step == 'download_media':
            return Response(content=b'1234', headers={'content-length': '4'})
        raise AssertionError(step)

    def request(self, step, method, url, **kwargs):
        self._hit(step)
        return Response(status_code=307, headers={'location': url + '?sig=1', 'content-type': 'video/mp4', 'content-length': '4'})

    post = get


def make_service(latency=0.0, **kwargs):
    upstream = Upstream(latency)

    def login(username, password, http):
        upstream.logins += 1
        http.cookies['xn_api_token'] = 'token'
        return LoginDiscoveryResult(ok=True, error_code=None, session=SessionState(xn_api_token_present=True, cookie_names=['xn_api_token']))

    service = SsuLectureService(
        http=upstream.client(),
        login_func=login,
        current_term_candidates=[45, 46],
        worker_client_factory=upstream.client,
        **kwargs,
    )
    return service, upstream


def test_a_session_is_logged_in_once_for_the_whole_discovery_flow():
    service, upstream = make_service()

    service.login('u', 'p')
    service.list_terms('u', 'p')
    service.discover_courses('u', 'p', term_ids=[46])
    service.get_course_detail('u', 'p', course_id=101, course_name='a')

    assert upstream.logins == 1


def test_explicit_term_courses_reuse_the_terms_scan_and_fetch_modules_in_parallel():
    service, upstream = make_service(latency=0.03)
    service.list_terms('u', 'p')
    upstream.reset()

    result = service.discover_courses('u', 'p', term_ids=[46])

    assert result.course_count == 3
    assert upstream.count('list_courses') == 0  # served by the terms scan
    assert upstream.count('list_course_modules') == 3
    assert upstream.peak == 3  # one round trip, not three in a row


def test_course_detail_after_the_course_list_needs_no_upstream_request():
    service, upstream = make_service()
    service.discover_courses('u', 'p', term_ids=[46])
    upstream.reset()

    detail = service.get_course_detail('u', 'p', course_id=102, course_name='과목102')

    assert detail.ok and detail.course.lecture_count == 3
    assert upstream.total() == 0


def test_refresh_bypasses_the_caches():
    service, upstream = make_service()
    service.list_terms('u', 'p')
    service.discover_courses('u', 'p', term_ids=[46])
    service.get_course_detail('u', 'p', course_id=101)
    upstream.reset()

    service.get_course_detail('u', 'p', course_id=101, refresh=True)
    assert upstream.count('list_course_modules') == 1
    service.list_terms('u', 'p', refresh=True)
    assert upstream.count('list_courses') == 2
    service.discover_courses('u', 'p', term_ids=[46], refresh=True)
    assert upstream.count('list_courses') == 3  # the one explicitly refreshed term
    assert upstream.count('list_course_modules') == 4


def test_concurrent_identical_requests_share_one_upstream_call():
    service, upstream = make_service(latency=0.05)
    results = []

    def call(kind):
        if kind == 'terms':
            results.append(service.list_terms('u', 'p'))
        else:
            results.append(service.discover_courses('u', 'p', term_ids=[46]))

    threads = [threading.Thread(target=call, args=('terms' if n % 2 else 'courses',)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert len(results) == 8
    assert upstream.logins == 1
    assert upstream.count('list_courses') == 2  # each of the two candidate terms once
    assert upstream.count('list_course_modules') == 3


def test_clear_cache_and_account_changes_drop_everything_learned():
    service, upstream = make_service()
    service.discover_courses('u', 'p', term_ids=[46])
    service.get_course_detail('u', 'p', course_id=101)

    service.clear_cache()
    upstream.reset()
    service.get_course_detail('u', 'p', course_id=101)
    assert upstream.logins == 2
    assert upstream.count('list_course_modules') == 1

    # A different account must not see modules (completion state) fetched for the first one.
    upstream.reset()
    service.get_course_detail('other', 'secret', course_id=101)
    assert upstream.logins == 3
    assert upstream.count('list_course_modules') == 1


def test_a_clear_during_a_running_lookup_is_not_undone_by_its_late_result():
    service, upstream = make_service(latency=0.05)
    worker = threading.Thread(target=lambda: service.discover_courses('u', 'p', term_ids=[46]))
    worker.start()
    time.sleep(0.03)
    service.clear_cache()  # waits for the running lookup, then wipes what it produced
    worker.join(10)
    upstream.reset()

    service.discover_courses('u', 'p', term_ids=[46])

    assert upstream.count('list_course_modules') == 3


def test_download_after_plan_resolves_the_media_only_once(tmp_path: Path):
    service, upstream = make_service()
    kwargs = {'course_id': 101, 'module_item_id': 1010, 'course_name': 'c', 'output_dir': tmp_path}

    planned = service.download_lecture('u', 'p', dry_run=True, **kwargs)
    upstream.reset()
    downloaded = service.download_lecture('u', 'p', dry_run=False, **kwargs)

    assert planned.ok and downloaded.written
    assert upstream.count('get_commons_content') == 0
    assert upstream.count('head_commons_download') == 0
    assert upstream.count('download_media') == 1


def test_batch_download_after_plan_reuses_resolutions_and_modules(tmp_path: Path):
    service, upstream = make_service(latency=0.02)
    kwargs = {'course_id': 101, 'course_name': 'c', 'output_dir': tmp_path, 'module_item_ids': [1010, 1011, 1012]}

    plan = service.batch_download_lectures('u', 'p', dry_run=True, **kwargs)
    assert plan.ok
    assert upstream.count('get_commons_content') == 3
    assert upstream.peak == 3  # lectures resolve concurrently
    upstream.reset()
    result = service.batch_download_lectures('u', 'p', dry_run=False, **kwargs)

    assert result.ok and all(item.written for item in result.items)
    assert upstream.count('list_course_modules') == 0
    assert upstream.count('get_commons_content') == 0
    assert upstream.count('head_commons_download') == 0


def test_resolved_media_expires_and_a_signed_url_failure_forces_a_fresh_resolution(tmp_path: Path):
    service, upstream = make_service(resolved_media_ttl_seconds=0)
    service.resolve_media('u', 'p', course_id=101, module_item_id=1010)
    service.resolve_media('u', 'p', course_id=101, module_item_id=1010)
    assert upstream.count('get_commons_content') == 2  # ttl 0: never reused

    service, upstream = make_service()
    first = service.resolve_media('u', 'p', course_id=101, module_item_id=1010)
    again = service.resolve_media('u', 'p', course_id=101, module_item_id=1010)
    assert first == again
    assert upstream.count('get_commons_content') == 1
    service._resolve_authenticated('u', 'p', 101, 1010, refresh=True)
    assert upstream.count('get_commons_content') == 2


def test_term_labels_use_the_lms_term_name_when_it_is_provided():
    service, _ = make_service()

    terms = service.list_terms('u', 'p')

    assert terms == [{'term_id': 46, 'course_count': 3, 'current_course_count': 3, 'is_current': True, 'label': '2026년 2학기'}]


def test_every_native_plan_resolves_a_fresh_signed_url(tmp_path: Path):
    service, upstream = make_service()
    kwargs = {'course_id': 101, 'module_item_id': 1010, 'course_name': 'c', 'output_dir': tmp_path}

    first = service.native_download_plan('u', 'p', **kwargs)
    upstream.reset()
    again = service.native_download_plan('u', 'p', **kwargs)

    # A re-plan after a CDN 403 must not get the cached (possibly expired) URL back.
    assert first.native_download is not None and again.native_download is not None
    assert upstream.count('get_commons_content') == 1


def test_deadlines_after_the_course_list_need_no_upstream_request():
    service, upstream = make_service()
    service.discover_courses('u', 'p', term_ids=[46])
    upstream.reset()

    result = service.list_deadlines('u', 'p', term_ids=[46])

    assert result.ok
    assert upstream.total() == 0


def test_deadlines_cover_every_course_of_the_term_sorted_by_deadline():
    from datetime import datetime, timezone

    def modules_with_deadlines(course_id):
        due = {101: '2026-10-21T14:59:59Z', 102: '2026-10-19T14:59:59Z', 103: '2026-10-20T14:59:00Z'}[course_id]
        kind = {101: 'attendance_item', 102: 'assignment', 103: 'quiz'}[course_id]
        item = {
            'id': course_id, 'module_item_id': course_id, 'title': f'항목 {course_id}', 'content_type': kind, 'completed': False,
            'url': f'https://canvas.ssu.ac.kr/x/{course_id}',
            'content_data': {'due_at': due, 'item_content_data': {'content_type': 'mp4', 'duration': 60} if kind == 'attendance_item' else {}},
        }
        return [{'id': course_id, 'name': '1주차', 'module_items': [item]}]

    service, upstream = make_service()
    original = FakeClient.get

    def get(self, step, url, **kwargs):
        if step == 'list_course_modules':
            self._hit(step)
            return Response(modules_with_deadlines(int(url.split('/courses/')[1].split('/')[0])))
        return original(self, step, url, **kwargs)

    FakeClient.get = get
    try:
        result = service.list_deadlines('u', 'p', term_ids=[46], now=datetime(2026, 10, 15, tzinfo=timezone.utc))
    finally:
        FakeClient.get = original

    assert result.ok
    assert [(item.course_id, item.kind) for item in result.deadlines] == [(102, 'assignment'), (103, 'quiz'), (101, 'video')]
    assert upstream.count('list_course_modules') == 3  # one fetch per course, shared with the course list


def test_deadlines_refresh_reloads_modules_once():
    service, upstream = make_service()
    service.discover_courses('u', 'p', term_ids=[46])
    upstream.reset()

    service.list_deadlines('u', 'p', term_ids=[46], refresh=True)

    assert upstream.count('list_course_modules') == 3
