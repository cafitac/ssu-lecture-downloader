import types
from pathlib import Path

import pytest
from curl_cffi.requests import exceptions as curl_exceptions

from ssu_lecture_downloader.core import download as download_mod
from ssu_lecture_downloader.core.download import DownloadCancelled, DownloadPlan, IncompleteDownload, download_to_plan


class StreamingResponse:
    def __init__(self, chunks, *, status_code=200, headers=None, fail_after_chunks=None):
        self._chunks = list(chunks)
        self.status_code = status_code
        self.headers = headers or {}
        self.fail_after_chunks = fail_after_chunks
        self.content = b''.join(self._chunks)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')

    def iter_content(self, chunk_size=1024 * 1024):
        for index, chunk in enumerate(self._chunks, start=1):
            if self.fail_after_chunks is not None and index > self.fail_after_chunks:
                raise ConnectionError('stream interrupted')
            yield chunk


class StreamingHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, step, url, **kwargs):
        self.calls.append((step, url, kwargs))
        if not self.responses:
            raise AssertionError('no response left')
        return self.responses.pop(0)


def make_plan(tmp_path: Path) -> DownloadPlan:
    output_dir = tmp_path / 'downloads'
    course_dir = output_dir / '자료구조'
    return DownloadPlan(output_dir=output_dir, course_dir=course_dir, file_path=course_dir / '강의.mp4')


def test_plan_download_path_maps_commons_movie_content_type_to_playable_mp4_extension(tmp_path: Path):
    from ssu_lecture_downloader.core.download import plan_download_path

    plan = plan_download_path(
        output_dir=tmp_path,
        course_name='UX방법론',
        lecture_title='1주차',
        content_id='content-1',
        extension='movie',
    )

    assert plan.file_path.name == '1주차 [content-1].mp4'


def test_download_streams_chunks_to_temp_file_then_atomic_final_path(tmp_path: Path):
    plan = make_plan(tmp_path)
    events = []
    http = StreamingHttp([
        StreamingResponse([b'ab', b'cd'], headers={'content-length': '4'}),
    ])

    path = download_to_plan(
        http=http,
        download_url='https://commons.example/download',
        plan=plan,
        referer='https://commons.example/viewer',
        on_progress=events.append,
    )

    assert path == plan.file_path
    assert plan.file_path.read_bytes() == b'abcd'
    assert not plan.file_path.with_suffix(plan.file_path.suffix + '.part').exists()
    assert [event['stage'] for event in events] == ['download_started', 'download_progress', 'download_progress', 'download_done']
    assert events[1]['bytes_written'] == 2
    assert events[2]['bytes_written'] == 4
    assert events[2]['total_bytes'] == 4
    assert events[-1]['output_path'] == str(plan.file_path)
    assert http.calls[0][2]['headers']['Referer'] == 'https://commons.example/viewer'
    assert http.calls[0][2]['stream'] is True


def test_download_cancellation_keeps_partial_and_emits_cancelled_event(tmp_path: Path):
    plan = make_plan(tmp_path)
    events = []
    cancel_checks = {'count': 0}

    def should_cancel():
        cancel_checks['count'] += 1
        return cancel_checks['count'] > 1

    http = StreamingHttp([
        StreamingResponse([b'ab', b'cd'], headers={'content-length': '4'}),
    ])

    with pytest.raises(DownloadCancelled):
        download_to_plan(
            http=http,
            download_url='https://commons.example/download',
            plan=plan,
            on_progress=events.append,
            should_cancel=should_cancel,
        )

    part_path = plan.file_path.with_suffix(plan.file_path.suffix + '.part')
    assert part_path.read_bytes() == b'ab'
    assert not plan.file_path.exists()
    assert events[-1]['stage'] == 'download_cancelled'
    assert events[-1]['bytes_written'] == 2


def test_download_failure_keeps_partial_temp_and_does_not_create_final_file(tmp_path: Path):
    plan = make_plan(tmp_path)
    events = []
    http = StreamingHttp([
        StreamingResponse([b'ab', b'cd'], headers={'content-length': '4'}, fail_after_chunks=1),
    ])

    with pytest.raises(ConnectionError):
        download_to_plan(
            http=http,
            download_url='https://commons.example/download',
            plan=plan,
            on_progress=events.append,
            max_attempts=1,
        )

    part_path = plan.file_path.with_suffix(plan.file_path.suffix + '.part')
    assert part_path.read_bytes() == b'ab'
    assert not plan.file_path.exists()
    assert events[-1]['stage'] == 'download_failed'
    assert events[-1]['bytes_written'] == 2


def test_download_retries_and_resumes_from_existing_partial_file(tmp_path: Path):
    plan = make_plan(tmp_path)
    part_path = plan.file_path.with_suffix(plan.file_path.suffix + '.part')
    part_path.parent.mkdir(parents=True, exist_ok=True)
    part_path.write_bytes(b'ab')
    events = []
    http = StreamingHttp([
        StreamingResponse([b'cd'], status_code=206, headers={'content-length': '2'}),
    ])

    path = download_to_plan(
        http=http,
        download_url='https://commons.example/download',
        plan=plan,
        on_progress=events.append,
        resume=True,
    )

    assert path.read_bytes() == b'abcd'
    assert http.calls[0][2]['headers']['Range'] == 'bytes=2-'
    assert events[0]['stage'] == 'download_started'
    assert events[0]['resume_from'] == 2
    assert events[-1]['bytes_written'] == 4


def test_download_discards_partial_when_server_ignores_range_request(tmp_path: Path):
    plan = make_plan(tmp_path)
    part_path = plan.file_path.with_suffix(plan.file_path.suffix + '.part')
    part_path.parent.mkdir(parents=True, exist_ok=True)
    part_path.write_bytes(b'old-partial')
    http = StreamingHttp([
        StreamingResponse([b'fresh'], status_code=200, headers={'content-length': '5'}),
    ])

    path = download_to_plan(
        http=http,
        download_url='https://commons.example/download',
        plan=plan,
        resume=True,
    )

    assert path.read_bytes() == b'fresh'
    assert http.calls[0][2]['headers']['Range'] == 'bytes=11-'


def test_download_retries_after_interrupted_stream_using_range_resume(tmp_path: Path):
    plan = make_plan(tmp_path)
    events = []
    http = StreamingHttp([
        StreamingResponse([b'ab', b'cd'], headers={'content-length': '4'}, fail_after_chunks=1),
        StreamingResponse([b'cd'], status_code=206, headers={'content-length': '2'}),
    ])

    path = download_to_plan(
        http=http,
        download_url='https://commons.example/download',
        plan=plan,
        on_progress=events.append,
        max_attempts=2,
        resume=True,
    )

    assert path.read_bytes() == b'abcd'
    assert http.calls[0][2].get('headers') is None or 'Range' not in http.calls[0][2].get('headers', {})
    assert http.calls[1][2]['headers']['Range'] == 'bytes=2-'
    assert [event['stage'] for event in events].count('download_retrying') == 1


# --- retry, resume and integrity behaviour -------------------------------------------------


@pytest.fixture(autouse=True)
def no_retry_backoff(monkeypatch):
    monkeypatch.setattr(download_mod, 'RETRY_BACKOFF_BASE_SECONDS', 0)


def http_error(status: int, url: str = 'https://cdn.example/video.mp4'):
    error = curl_exceptions.HTTPError(f'HTTP {status}')
    error.response = types.SimpleNamespace(status_code=status, url=url)
    return error


class TrackedResponse(StreamingResponse):
    def __init__(self, *args, error=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.error = error
        self.closed = False

    def raise_for_status(self):
        if self.error is not None:
            raise self.error
        super().raise_for_status()

    def close(self):
        self.closed = True


class FlakyHttp(StreamingHttp):
    """Items in `responses` that are exceptions are raised from get()."""

    def get(self, step, url, **kwargs):
        self.calls.append((step, url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def part_of(plan: DownloadPlan) -> Path:
    return plan.file_path.with_suffix(plan.file_path.suffix + '.part')


def test_download_retries_connect_errors_raised_by_the_request_itself(tmp_path: Path):
    plan = make_plan(tmp_path)
    events = []
    http = FlakyHttp([
        curl_exceptions.ConnectionError('connection refused'),
        curl_exceptions.Timeout('timed out'),
        TrackedResponse([b'abcd'], headers={'content-length': '4'}),
    ])

    path = download_to_plan(http=http, download_url='https://cdn.example/v', plan=plan, max_attempts=3, on_progress=events.append)

    assert path.read_bytes() == b'abcd'
    assert [event['stage'] for event in events].count('download_retrying') == 2
    assert len(http.calls) == 3


def test_download_retries_server_errors_reported_by_raise_for_status(tmp_path: Path):
    plan = make_plan(tmp_path)
    failing = TrackedResponse([b''], status_code=503, error=http_error(503))
    http = FlakyHttp([failing, TrackedResponse([b'abcd'], headers={'content-length': '4'})])

    path = download_to_plan(http=http, download_url='https://cdn.example/v', plan=plan, max_attempts=2)

    assert path.read_bytes() == b'abcd'
    assert failing.closed is True


def test_download_does_not_retry_client_errors(tmp_path: Path):
    plan = make_plan(tmp_path)
    forbidden = TrackedResponse([b''], status_code=403, error=http_error(403))
    http = FlakyHttp([forbidden, TrackedResponse([b'abcd'])])

    with pytest.raises(curl_exceptions.HTTPError):
        download_to_plan(http=http, download_url='https://cdn.example/v', plan=plan, max_attempts=5)

    assert len(http.calls) == 1
    assert forbidden.closed is True


def test_download_416_with_complete_partial_file_is_finalized(tmp_path: Path):
    plan = make_plan(tmp_path)
    part = part_of(plan)
    part.parent.mkdir(parents=True)
    part.write_bytes(b'abcd')
    rejected = TrackedResponse([b''], status_code=416, headers={'content-range': 'bytes */4'}, error=http_error(416))
    http = FlakyHttp([rejected])

    path = download_to_plan(http=http, download_url='https://cdn.example/v', plan=plan)

    assert path.read_bytes() == b'abcd'
    assert not part.exists()
    assert len(http.calls) == 1
    assert rejected.closed is True


def test_download_416_with_stale_partial_file_restarts_from_zero(tmp_path: Path):
    plan = make_plan(tmp_path)
    part = part_of(plan)
    part.parent.mkdir(parents=True)
    part.write_bytes(b'stale-and-too-long')
    rejected = TrackedResponse([b''], status_code=416, headers={'content-range': 'bytes */4'}, error=http_error(416))
    fresh = TrackedResponse([b'abcd'], status_code=200, headers={'content-length': '4'})
    http = FlakyHttp([rejected, fresh])

    path = download_to_plan(http=http, download_url='https://cdn.example/v', plan=plan)

    assert path.read_bytes() == b'abcd'
    assert http.calls[0][2]['headers']['Range'] == 'bytes=18-'
    assert 'Range' not in http.calls[1][2].get('headers', {})


def test_download_416_without_content_range_discards_partial_and_restarts(tmp_path: Path):
    plan = make_plan(tmp_path)
    part = part_of(plan)
    part.parent.mkdir(parents=True)
    part.write_bytes(b'ab')
    http = FlakyHttp([
        TrackedResponse([b''], status_code=416, error=http_error(416)),
        TrackedResponse([b'abcd'], headers={'content-length': '4'}),
    ])

    path = download_to_plan(http=http, download_url='https://cdn.example/v', plan=plan)

    assert path.read_bytes() == b'abcd'


def test_download_closes_response_on_success_failure_and_cancel(tmp_path: Path):
    plan = make_plan(tmp_path)
    ok = TrackedResponse([b'ab', b'cd'], headers={'content-length': '4'})
    download_to_plan(http=FlakyHttp([ok]), download_url='https://cdn.example/v', plan=plan)
    assert ok.closed is True

    plan2 = DownloadPlan(output_dir=plan.output_dir, course_dir=plan.course_dir, file_path=plan.course_dir / 'other.mp4')
    cancelled = TrackedResponse([b'ab', b'cd'], headers={'content-length': '4'})
    with pytest.raises(DownloadCancelled):
        download_to_plan(http=FlakyHttp([cancelled]), download_url='https://cdn.example/v', plan=plan2, should_cancel=lambda: True)
    assert cancelled.closed is True

    plan3 = DownloadPlan(output_dir=plan.output_dir, course_dir=plan.course_dir, file_path=plan.course_dir / 'third.mp4')
    broken = TrackedResponse([b'ab', b'cd'], headers={'content-length': '4'}, fail_after_chunks=1)
    with pytest.raises(ConnectionError):
        download_to_plan(http=FlakyHttp([broken]), download_url='https://cdn.example/v', plan=plan3)
    assert broken.closed is True


def test_download_rejects_short_body_instead_of_renaming_truncated_file(tmp_path: Path):
    plan = make_plan(tmp_path)
    http = FlakyHttp([TrackedResponse([b'ab'], headers={'content-length': '10'})])

    with pytest.raises(IncompleteDownload):
        download_to_plan(http=http, download_url='https://cdn.example/v', plan=plan, max_attempts=1)

    assert not plan.file_path.exists()
    assert part_of(plan).read_bytes() == b'ab'


def test_download_resumes_after_short_body_and_then_finalizes(tmp_path: Path):
    plan = make_plan(tmp_path)
    http = FlakyHttp([
        TrackedResponse([b'ab'], headers={'content-length': '4'}),
        TrackedResponse([b'cd'], status_code=206, headers={'content-length': '2'}),
    ])

    path = download_to_plan(http=http, download_url='https://cdn.example/v', plan=plan, max_attempts=2)

    assert path.read_bytes() == b'abcd'
    assert http.calls[1][2]['headers']['Range'] == 'bytes=2-'


def test_download_skips_length_check_for_content_encoded_responses(tmp_path: Path):
    plan = make_plan(tmp_path)
    http = FlakyHttp([TrackedResponse([b'decoded-body'], headers={'content-length': '5', 'content-encoding': 'gzip'})])

    path = download_to_plan(http=http, download_url='https://cdn.example/v', plan=plan)

    assert path.read_bytes() == b'decoded-body'
