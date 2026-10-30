"""The tidy-up API: contract, single-use plans, and the exclusion between tidy-ups and downloads (AC11 backend).

A fake LMS service and temp folders. Jobs are joined by thread, blocked work waits on events the test sets;
nothing sleeps or polls.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_api import FakeCredentialStore, FakeService

from ssu_lecture_downloader.core import library_migration
from ssu_lecture_downloader.core.lms_service import CourseDetailResult
from ssu_lecture_downloader.core.library_migration import MigrationRunner
from ssu_lecture_downloader.service.api import create_app
from ssu_lecture_downloader.service.credentials import CredentialPair

COURSE = 'UX방법론'
LEGACY = ['26년1학기_UX방법론_1주차 [cid-1].mp4', '26년1학기_UX방법론_1주차 [cid-1].transcript.txt', '자료 [cid-2].pdf']
JOB_BODY = {'course_id': 44070, 'module_item_id': 3268578, 'course_name': COURSE}


class LibraryService(FakeService):
    """FakeService whose content items carry content ids; downloads can be held until the test releases them."""

    def __init__(self) -> None:
        self.hold_downloads = False
        self.release = threading.Event()

    def get_course_detail(self, username, password, *, course_id, course_name=None):
        result = super().get_course_detail(username, password, course_id=course_id, course_name=course_name)
        items = [item.model_copy(update={'content_id': cid}) for item, cid in zip(result.content_items, ['cid-1', 'cid-2'])]
        return CourseDetailResult(ok=True, error_code=None, course=result.course, lectures=result.lectures, content_items=items)

    def download_lecture(self, *args, **kwargs):
        if self.hold_downloads:
            assert self.release.wait(10), 'the test never released the download'
        return super().download_lecture(*args, **kwargs)

    def batch_download_lectures(self, *args, **kwargs):
        if self.hold_downloads:
            assert self.release.wait(10), 'the test never released the download'
        return super().batch_download_lectures(*args, **kwargs)


class Api:
    def __init__(self, tmp_path: Path, *, credentials: bool = True) -> None:
        self.root = tmp_path
        self.service = LibraryService()
        pair = CredentialPair(username='student-id', password='secret-password') if credentials else None
        self.client = TestClient(create_app(service_factory=lambda: self.service, default_output_dir=tmp_path, credential_store=FakeCredentialStore(pair)))

    def legacy_files(self) -> None:
        folder = self.root / COURSE
        folder.mkdir()
        for name in LEGACY:
            (folder / name).write_bytes(name.encode())

    def finish(self, started: dict[str, Any], kind: str) -> dict[str, Any]:
        """Join the job's thread, then read its final status through the API."""
        runner = next((thread for thread in threading.enumerate() if thread.name == f'ssu-job-{started["job_id"][:8]}'), None)
        if runner is not None:
            runner.join(15)
            assert not runner.is_alive()
        status = self.client.get(f'/api/library/tidy/{kind}/jobs/{started["job_id"]}').json()
        assert status['status'] in {'succeeded', 'failed', 'cancelled'}, status
        return status

    def plan(self) -> dict[str, Any]:
        started = self.client.post('/api/library/tidy/plan/jobs', json={})
        assert started.status_code == 200, started.text
        return self.finish(started.json(), 'plan')['result']

    def apply(self, plan_id: str):
        return self.client.post('/api/library/tidy/apply/jobs', json={'plan_id': plan_id})


@pytest.fixture
def api(tmp_path):
    return Api(tmp_path)


def error_code(response) -> str:
    assert response.status_code == 409, response.text
    return response.json()['error_code']


def test_the_tidy_api_follows_the_contract_from_summary_to_undo(api, tmp_path):
    api.legacy_files()
    nobody = Api(tmp_path, credentials=False).client  # the summary needs no login
    assert nobody.get('/api/library/legacy-summary').json() == {
        'ok': True, 'root': str(tmp_path), 'legacy_file_count': 3, 'course_count': 1, 'courses': [{'folder': COURSE, 'legacy_file_count': 3}],
    }
    detail = lambda: api.client.get('/api/courses/44070', params={'course_name': COURSE}).json()  # noqa: E731
    assert detail()['legacy_file_count'] == 3

    preview = api.plan()

    assert preview.keys() == {'plan_id', 'expires_at', 'move_count', 'skip_count', 'moves', 'skips', 'open_files_note'}
    assert (preview['move_count'], preview['skip_count'], preview['open_files_note']) == (3, 0, True)
    assert {move['from']: (move['to'], move['role'], move['week_folder']) for move in preview['moves']} == {
        f'{COURSE}/{LEGACY[0]}': (f'{COURSE}/01주차/26년1학기_UX방법론_1주차/26년1학기_UX방법론_1주차.mp4', 'video', '01주차'),
        f'{COURSE}/{LEGACY[1]}': (f'{COURSE}/01주차/26년1학기_UX방법론_1주차/26년1학기_UX방법론_1주차 자막.txt', 'text', '01주차'),
        f'{COURSE}/{LEGACY[2]}': (f'{COURSE}/01주차/26년1학기_UX방법론_1주차_자료.pdf', 'material', '01주차'),
    }
    assert preview['moves'][0].keys() == {'course', 'week_folder', 'lecture_stem', 'from', 'to', 'role'}
    assert all((tmp_path / COURSE / name).is_file() for name in LEGACY)  # a preview moves nothing

    applied = api.apply(preview['plan_id'])
    assert applied.status_code == 200
    finished = api.finish(applied.json(), 'apply')
    run = finished['result']
    assert finished['status'] == 'succeeded' and run.keys() == {'run_id', 'moved', 'skipped', 'skips', 'undo_available'}
    assert (run['moved'], run['skipped'], run['skips'], run['undo_available']) == (3, 0, [], True)
    assert [(event['done'], event['total']) for event in finished['progress'] if event['stage'] == 'tidy_moving'][-1] == (3, 3)
    assert not any((tmp_path / COURSE / name).exists() for name in LEGACY) and detail()['legacy_file_count'] == 0
    assert [item['downloaded_path'] for item in detail()['content_items']][0].endswith('26년1학기_UX방법론_1주차.mp4')

    runs = api.client.get('/api/library/tidy/runs').json()
    assert runs['runs'] == [{'run_id': run['run_id'], 'at': runs['runs'][0]['at'], 'moved': 3, 'undo_available': True}]
    assert error_code(api.apply(preview['plan_id'])) == 'tidy_plan_expired'  # a plan is used once

    undone = api.client.post('/api/library/tidy/undo/jobs', json={'run_id': run['run_id']})
    result = api.finish(undone.json(), 'undo')['result']

    assert result == {'run_id': run['run_id'], 'restored': 3, 'skipped': 0, 'skips': []}
    assert all((tmp_path / COURSE / name).is_file() for name in LEGACY) and detail()['legacy_file_count'] == 3
    assert api.client.get('/api/library/tidy/runs').json()['runs'][0]['undo_available'] is False
    assert api.client.post('/api/library/tidy/undo/jobs', json={'run_id': '20200101T000000Z-000000'}).status_code == 404
    assert api.client.post('/api/library/tidy/undo/jobs', json={'run_id': '../x'}).status_code == 400


def test_a_preview_expires_after_thirty_minutes_and_an_unknown_one_never_applies(api, monkeypatch):
    api.legacy_files()
    now = [1000.0]
    monkeypatch.setattr(library_migration, '_monotonic', lambda: now[0])
    inside, outside = api.plan(), api.plan()

    now[0] += 30 * 60 - 1
    applied = api.apply(inside['plan_id'])
    assert applied.status_code == 200 and api.finish(applied.json(), 'apply')['status'] == 'succeeded'
    now[0] += 2

    assert error_code(api.apply(outside['plan_id'])) == 'tidy_plan_expired'
    assert error_code(api.apply('not-a-plan')) == 'tidy_plan_expired'


@pytest.mark.parametrize(
    'start',
    [
        ('/api/download/jobs', JOB_BODY),
        ('/api/download/artifacts/jobs', {'course_id': 44070, 'course_name': COURSE, 'module_item_ids': [3268578], 'outputs': ['mp4']}),
    ],
    ids=['download job', 'artifact job'],
)
def test_a_tidy_up_is_refused_while_a_download_job_runs_and_keeps_its_preview(api, start):
    api.legacy_files()
    preview = api.plan()
    api.service.hold_downloads = True
    started = api.client.post(*start[:1], json=start[1])
    assert started.status_code == 200, started.text
    try:
        assert error_code(api.apply(preview['plan_id'])) == 'library_busy'
        assert error_code(api.client.post('/api/library/tidy/plan/jobs', json={})) == 'library_busy'
    finally:
        api.service.release.set()
    runner = next(thread for thread in threading.enumerate() if thread.name == f'ssu-job-{started.json()["job_id"][:8]}')
    runner.join(15)

    applied = api.apply(preview['plan_id'])  # the refused attempt did not use the plan up
    assert applied.status_code == 200 and api.finish(applied.json(), 'apply')['status'] == 'succeeded'


@pytest.mark.parametrize(
    'start',
    [
        ('/api/download/jobs', JOB_BODY),
        ('/api/download/artifacts/jobs', {'course_id': 44070, 'course_name': COURSE, 'module_item_ids': [3268578], 'outputs': ['mp4']}),
        ('/api/download', JOB_BODY),
        ('/api/download/canvas-file', {'course_id': 44070, 'file_id': 9, 'course_name': COURSE}),
    ],
    ids=['download job', 'artifact job', 'synchronous download', 'canvas file'],
)
def test_downloads_are_refused_while_a_tidy_up_runs(api, monkeypatch, start):
    api.legacy_files()
    release = threading.Event()
    real_apply = MigrationRunner.apply

    def held_apply(self, plan, **kwargs):
        assert release.wait(10), 'the test never released the tidy-up'
        return real_apply(self, plan, **kwargs)

    monkeypatch.setattr(MigrationRunner, 'apply', held_apply)
    applied = api.apply(api.plan()['plan_id'])
    assert applied.status_code == 200
    try:
        assert error_code(api.client.post(start[0], json=start[1])) == 'library_tidy_running'
        assert error_code(api.client.post('/api/library/tidy/plan/jobs', json={})) == 'library_busy'
    finally:
        release.set()

    assert api.finish(applied.json(), 'apply')['status'] == 'succeeded'
    assert api.client.post('/api/download/jobs', json=JOB_BODY).status_code == 200  # free again
