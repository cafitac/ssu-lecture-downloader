"""Per-lecture artifact pipeline: overlap, in-lecture order, limits, isolation, cancel, event contract.

Everything is fake: the LMS (download latency per lecture), ffmpeg (audio extraction), the local
speech engine and the remote whisper server. Nothing touches the network or a real LMS.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from stt_fakes import FakeSttServer, srt, write_silent_wav
from test_api import FakeCredentialStore
from test_service import FakeHttp, ok_login

from ssu_lecture_downloader.core import postprocess_media, stt
from ssu_lecture_downloader.core.jobs import MAX_JOB_PROGRESS_EVENTS, Job, JobRegistry, JobRunner
from ssu_lecture_downloader.core.lms_service import BatchDownloadItemResult, BatchDownloadResult, SsuLectureService
from ssu_lecture_downloader.core.postprocess_media import AudioExtractionResult
from ssu_lecture_downloader.core.stt import TranscriptionResult
from ssu_lecture_downloader.core.stt_remote import RemoteSTTService
from ssu_lecture_downloader.core.stt_remote_config import RemoteSTTSettingsStore
from ssu_lecture_downloader.platforms.macos import MacOSPlatform
from ssu_lecture_downloader.service.api_models import ArtifactDownloadRequest
from ssu_lecture_downloader.service.app_context import AppContext, build_app_context
from ssu_lecture_downloader.service.artifacts import ArtifactPipeline
from ssu_lecture_downloader.service.credentials import CredentialPair
from ssu_lecture_downloader.service.pipeline_gates import GateCancelled, StageGates

REAL_TRANSCRIBE_AUDIO = stt.transcribe_audio  # captured before any test replaces it with a fake engine

# --------------------------------------------------------------------------- fakes


@dataclass
class Spec:
    """One fake lecture: latencies in seconds and where it should fail."""

    item_id: int
    download: float = 0.0
    extract: float = 0.02
    transcribe: float = 0.02
    has_video: bool = False  # video already on disk: no download is needed for mp3/txt
    fail_at: str | None = None  # "download" | "audio" | "transcript"
    fail_code: str | None = None
    block_extract: bool = False  # the ffmpeg step never finishes on its own; only the cancel ends it
    block_transcribe: bool = False  # the local engine step never finishes on its own; only the cancel ends it


class Signal:
    """A condition the fakes notify on every change a test may wait for (progress appended, worker entered/left, request seen)."""

    def __init__(self) -> None:
        self._cond = threading.Condition()

    def notify(self) -> None:
        with self._cond:
            self._cond.notify_all()

    def wait_for(self, predicate: Callable[[], bool], timeout: float = 5.0, message: str = 'condition') -> None:
        with self._cond:
            reached = self._cond.wait_for(predicate, timeout)
        assert reached, f'timed out waiting for {message}'


def pause(seconds: float) -> None:
    """Simulated work latency inside a fake (an Event nobody sets: it just waits)."""
    threading.Event().wait(seconds)


class Probe:
    """Counts how many fake workers run at the same time."""

    def __init__(self, signal: Signal | None = None) -> None:
        self._signal = signal
        self._lock = threading.Lock()
        self.current = 0
        self.peak = 0
        self.total = 0

    @contextmanager
    def track(self):
        with self._lock:
            self.current += 1
            self.total += 1
            self.peak = max(self.peak, self.current)
        if self._signal is not None:
            self._signal.notify()
        try:
            yield
        finally:
            with self._lock:
                self.current -= 1
            if self._signal is not None:
                self._signal.notify()


def sleep_unless_cancelled(seconds: float, should_cancel: Callable[[], bool] | None, *, until_cancel: bool = False, limit: float = 30.0) -> bool:
    """Sleep `seconds` (or until cancel). True when the sleep was cut short by the cancel."""
    deadline = time.monotonic() + (limit if until_cancel else seconds)
    while time.monotonic() < deadline:
        if should_cancel is not None and should_cancel():
            return True
        pause(0.005)  # the fake polls the cancel flag the way the real ffmpeg / engine wrappers do
    return False


class FakeLms:
    """Stands in for SsuLectureService.batch_download_lectures with a per-lecture download latency."""

    def __init__(self, root: Path, specs: list[Spec], *, plan_error: str | None = None) -> None:
        self.root = root
        self.specs = {spec.item_id: spec for spec in specs}
        self.plan_error = plan_error
        self.downloaded: list[int] = []

    def video_path(self, item_id: int) -> Path:
        return self.root / 'course' / f'lecture-{item_id}.mp4'

    def prepare_disk(self) -> None:
        (self.root / 'course').mkdir(parents=True, exist_ok=True)
        for spec in self.specs.values():
            if spec.has_video:
                self.video_path(spec.item_id).write_bytes(b'video')

    def _item(self, spec: Spec, *, dry_run: bool, ok: bool = True, error_code: str | None = None, written: bool = False) -> BatchDownloadItemResult:
        path = self.video_path(spec.item_id)
        return BatchDownloadItemResult(
            ok=ok, error_code=error_code, dry_run=dry_run, written=written, course_id=1, module_item_id=spec.item_id, title=f'강의 {spec.item_id}',
            completed=False, attendance_item_id=None, content_id=None, download_url=None, download_size=None,
            output_path=str(path), file_size=path.stat().st_size if path.exists() else None, file_exists=path.exists(), skipped=False, conflict_strategy='skip',
        )

    def batch_download_lectures(
        self, username, password, *, course_id, course_name, output_dir, dry_run, module_item_ids=None, include_completed=False,
        on_progress=None, should_cancel=None, conflict_strategy='rename', max_parallel_downloads=4, on_item_ready=None,
    ):
        if self.plan_error:
            return BatchDownloadResult(ok=False, error_code=self.plan_error, dry_run=dry_run, course_id=course_id, course_name=course_name, items=[])
        selected = [spec for item_id, spec in self.specs.items() if module_item_ids is None or item_id in module_item_ids]
        total = len(selected)
        if dry_run:
            return BatchDownloadResult(ok=True, error_code=None, dry_run=True, course_id=course_id, course_name=course_name, items=[self._item(spec, dry_run=True) for spec in selected])

        def emit(stage: str, spec: Spec, **extra: Any) -> None:
            if on_progress:
                on_progress({'stage': stage, 'index': list(self.specs).index(spec.item_id) + 1, 'total': total, 'module_item_id': spec.item_id, 'title': f'강의 {spec.item_id}', **extra})

        def download(spec: Spec) -> BatchDownloadItemResult | None:
            emit('downloading', spec)
            if sleep_unless_cancelled(spec.download, should_cancel):
                return None  # cancelled: a real batch drops the item and reports nothing
            if spec.fail_at == 'download':
                emit('failed', spec, error_code=spec.fail_code or 'download_http_error')
                item = self._item(spec, dry_run=False, ok=False, error_code=spec.fail_code or 'download_http_error')
            else:
                self.video_path(spec.item_id).write_bytes(b'video')
                self.downloaded.append(spec.item_id)
                emit('done', spec, output_path=str(self.video_path(spec.item_id)))
                item = self._item(spec, dry_run=False, written=True)
            if on_item_ready is not None:
                on_item_ready(item)
            return item

        with ThreadPoolExecutor(max_workers=max(1, min(max_parallel_downloads, total))) as pool:
            items = [item for item in pool.map(download, selected) if item is not None]
        ok = all(item.ok for item in items)
        return BatchDownloadResult(ok=ok, error_code=None if ok else 'partial_failure', dry_run=False, course_id=course_id, course_name=course_name, items=items)


class Rig:
    """An AppContext on fakes, with instrumented ffmpeg and local engine."""

    def __init__(self, tmp_path: Path, monkeypatch, specs: list[Spec], *, plan_error: str | None = None) -> None:
        self.tmp_path = tmp_path
        self.lms = FakeLms(tmp_path, specs, plan_error=plan_error)
        self.lms.prepare_disk()
        self.signal = Signal()
        self.media = Probe(self.signal)
        self.engine = Probe(self.signal)
        self.extract_calls: list[int] = []
        self.transcribe_calls: list[int] = []
        self.remote_enabled = False
        remote = RemoteSTTService(RemoteSTTSettingsStore(tmp_path / 'stt.json', writer=MacOSPlatform()))
        self.ctx: AppContext = build_app_context(
            service=self.lms, credentials=FakeCredentialStore(CredentialPair(username='u', password='p')),
            runner=JobRunner(), platform=MacOSPlatform(), default_output_dir=tmp_path, remote_stt=remote,
        )
        monkeypatch.setattr(postprocess_media, 'extract_audio', self._extract)
        monkeypatch.setattr(postprocess_media, 'ensure_playable_mp4', self._ensure_mp4)
        monkeypatch.setattr(stt, 'transcribe_audio', self._transcribe)
        self.registry = JobRegistry()
        self.job: Job | None = None
        self.job_started = threading.Event()
        create = self.registry.create

        def create_watched() -> Job:
            job = create()
            append = job.append_progress

            def append_and_notify(event: dict[str, Any]) -> None:
                append(event)
                self.signal.notify()

            job.append_progress = append_and_notify  # in place before the runner thread starts
            return job

        self.registry.create = create_watched

    # ---- fakes
    def _spec_for(self, path: Path) -> Spec:
        return self.lms.specs[int(path.stem.split('-')[1].split('.')[0])]

    def _extract(self, request, on_progress=None, should_cancel=None):
        spec = self._spec_for(request.input_path)
        self.extract_calls.append(spec.item_id)
        with self.media.track():
            if sleep_unless_cancelled(spec.extract, should_cancel, until_cancel=spec.block_extract):
                return AudioExtractionResult(ok=False, input_path=request.input_path, error_code='cancelled', message='cancelled')
            if spec.fail_at == 'audio':
                return AudioExtractionResult(ok=False, input_path=request.input_path, error_code=spec.fail_code or 'ffmpeg_failed', message='bad input')
        output = request.input_path.with_suffix('.mp3')
        output.write_bytes(b'audio')
        return AudioExtractionResult(ok=True, input_path=request.input_path, output_path=output, file_size=5)

    def _ensure_mp4(self, path, *, overwrite=True, on_progress=None, should_cancel=None):
        with self.media.track():
            pause(0.05)
        return postprocess_media.VideoReconstructionResult(ok=True, input_path=path, output_path=path, file_size=5, reconstructed=False)

    def _transcribe(self, request, on_progress=None, should_cancel=None, remote=None, *, stt_capability, gates=None):
        spec = self._spec_for(request.audio_path)
        self.transcribe_calls.append(spec.item_id)
        with self.engine.track():
            if sleep_unless_cancelled(spec.transcribe, should_cancel, until_cancel=spec.block_transcribe):
                return TranscriptionResult(ok=False, audio_path=request.audio_path, error_code='cancelled', message='cancelled')
            if spec.fail_at == 'transcript':
                return TranscriptionResult(ok=False, audio_path=request.audio_path, error_code=spec.fail_code or 'stt_failed', message='bad')
        text = request.audio_path.with_name(f'{request.audio_path.stem}.transcript.txt')
        text.write_text('[00:00:00] 안녕하세요\n', encoding='utf-8')
        return TranscriptionResult(ok=True, audio_path=request.audio_path, text_path=text)

    # ---- running
    def start(self, outputs: list[str], *, ids: list[int] | None = None, **overrides: Any) -> Job:
        request = ArtifactDownloadRequest(
            **{'course_id': 1, 'course_name': 'course', 'module_item_ids': ids or list(self.lms.specs), 'outputs': outputs, 'stt_provider': 'fake', **overrides},
        )

        def work(job: Job):
            return ArtifactPipeline(self.ctx, job, request, outputs, ('u', 'p'), self.tmp_path).run()

        self.job = self.ctx.runner.start(self.registry, work)
        self.job_started.set()
        return self.job

    def wait_for(self, predicate: Callable[[], bool], timeout: float = 5.0, message: str = 'condition') -> None:
        self.signal.wait_for(predicate, timeout, message)

    def run(self, outputs: list[str], **kwargs: Any) -> dict[str, Any]:
        return finish(self.start(outputs, **kwargs))

    def enable_remote(self, url: str) -> None:
        self.ctx.remote_stt.update({'enabled': True, 'kind': 'whisper_cpp_server', 'base_url': url})
        self.remote_enabled = True


def finish(job: Job, timeout: float = 15.0) -> dict[str, Any]:
    """Join the job's runner thread (it ends only after the job reached a terminal status) and return the snapshot."""
    runner = next((thread for thread in threading.enumerate() if thread.name == f'ssu-job-{job.job_id[:8]}'), None)
    if runner is not None:
        runner.join(timeout)
        assert not runner.is_alive(), f'job never finished: {job.snapshot()["status"]}'
    snapshot = job.snapshot()
    assert snapshot['status'] in {'succeeded', 'failed', 'cancelled'}, snapshot['status']
    return snapshot


def events(snapshot_or_job, stage: str | None = None, item_id: int | None = None) -> list[dict[str, Any]]:
    progress = snapshot_or_job.progress_events() if isinstance(snapshot_or_job, Job) else snapshot_or_job['progress']
    return [e for e in progress if (stage is None or e['stage'] == stage) and (item_id is None or e.get('module_item_id') == item_id)]


@pytest.fixture
def make_rig(tmp_path, monkeypatch):
    def build(specs: list[Spec], **kwargs: Any) -> Rig:
        return Rig(tmp_path, monkeypatch, specs, **kwargs)

    return build


# --------------------------------------------------------------------------- gates


def test_gate_limits_waits_fifo_and_releases_in_finally():
    gates = StageGates(media=2, poll_seconds=0.01)
    signal = Signal()
    order: list[int] = []
    waited: list[int] = []
    release = threading.Event()
    started = threading.Barrier(3)

    def worker(n: int) -> None:
        with gates.hold('media', on_wait=lambda: (waited.append(n), signal.notify())):
            order.append(n)
            if n < 2:
                started.wait(2)
            release.wait(2)

    first = [threading.Thread(target=worker, args=(n,)) for n in (0, 1)]
    for thread in first:
        thread.start()
    started.wait(2)
    late = []
    for n in (2, 3):
        thread = threading.Thread(target=worker, args=(n,))
        thread.start()
        late.append(thread)
        signal.wait_for(lambda n=n: n in waited, message=f'worker {n} to wait')
    assert gates.in_use('media') == 2 and gates.waiting('media') == 2 and order == [0, 1]
    release.set()
    for thread in first + late:
        thread.join(2)
    assert order[2:] == [2, 3]  # first come, first served
    assert waited == [2, 3]  # on_wait fires once, only for those that had to wait
    assert gates.in_use('media') == 0 and gates.peak('media') == 2

    with pytest.raises(RuntimeError):
        with gates.hold('media'):
            raise RuntimeError('boom')
    assert gates.in_use('media') == 0


def test_gate_wait_is_cancel_aware_and_a_cancelled_waiter_holds_nothing():
    gates = StageGates(local_stt=1, poll_seconds=0.01)
    signal = Signal()
    cancel = threading.Event()
    outcome: list[str] = []
    with gates.hold('local_stt'):
        def waiter() -> None:
            try:
                with gates.hold('local_stt', should_cancel=cancel.is_set, on_wait=signal.notify):
                    outcome.append('acquired')
            except GateCancelled:
                outcome.append('cancelled')

        thread = threading.Thread(target=waiter)
        thread.start()
        signal.wait_for(lambda: gates.waiting('local_stt') == 1, message='waiter')
        started = time.monotonic()
        cancel.set()
        thread.join(2)
        assert outcome == ['cancelled'] and time.monotonic() - started < 1
        assert gates.in_use('local_stt') == 1 and gates.waiting('local_stt') == 0
    assert gates.in_use('local_stt') == 0


# ------------------------------------------------------------- AC1: no barrier


def test_a_lecture_is_processed_while_another_is_still_downloading(make_rig):
    rig = make_rig([Spec(1, download=0.05), Spec(2, download=1.0)])

    snapshot = rig.run(['mp3'])

    assert snapshot['status'] == 'succeeded'
    first_extract = events(snapshot, 'extracting_audio', 1)[0]['at']
    second_download_done = events(snapshot, 'done', 2)[0]['at']
    assert first_extract < second_download_done - 0.5  # lecture 1 was already extracting while lecture 2 had most of its download left
    assert events(snapshot, 'audio_done', 1)[0]['at'] < second_download_done


def test_lectures_that_need_no_download_start_before_any_download_ends(make_rig):
    rig = make_rig([Spec(1, has_video=True), Spec(2, download=0.5)])

    snapshot = rig.run(['mp3'])

    assert events(snapshot, 'extracting_audio', 1)[0]['at'] < events(snapshot, 'done', 2)[0]['at']
    assert snapshot['result']['summary'] == {'total': 2, 'succeeded': 2, 'failed': 0, 'cancelled': 0}


# --------------------------------------------------------- AC2: in-lecture order


def test_every_lecture_keeps_its_own_order_under_random_latencies(make_rig):
    rng = random.Random(20261030)
    specs = [Spec(n, download=rng.uniform(0, 0.3), extract=rng.uniform(0.01, 0.1), transcribe=rng.uniform(0.01, 0.1)) for n in range(1, 5)]
    rig = make_rig(specs)

    snapshot = rig.run(['mp3', 'txt'])

    assert snapshot['status'] == 'succeeded'
    expected = ['artifact_item_queued', 'downloading', 'done', 'extracting_audio', 'audio_done', 'transcribing', 'transcript_done', 'artifact_item_done']
    for spec in specs:
        lecture_events = [e for e in snapshot['progress'] if e.get('module_item_id') == spec.item_id and e['stage'] in expected]
        stages = [e['stage'] for e in lecture_events]
        assert stages == expected, (spec.item_id, stages)
        stamps = [e['at'] for e in lecture_events]
        assert stamps == sorted(stamps)
        assert rig.lms.video_path(spec.item_id).with_suffix('.transcript.txt').exists()


# ------------------------------------------------------------- AC3: limits


def test_process_wide_limits_hold_across_two_simultaneous_jobs(make_rig, tmp_path):
    specs = [Spec(n, extract=0.05, transcribe=0.05, has_video=True) for n in range(1, 11)]
    rig = make_rig(specs)
    first = rig.start(['mp3', 'txt'], ids=list(range(1, 6)))
    second = rig.start(['mp3', 'txt'], ids=list(range(6, 11)))

    assert finish(first)['status'] == 'succeeded' and finish(second)['status'] == 'succeeded'

    assert rig.media.total == 10 and rig.engine.total == 10
    assert rig.media.peak == 2  # reached, never exceeded
    assert rig.engine.peak == 1
    assert rig.ctx.gates.peak('media') == 2 and rig.ctx.gates.peak('local_stt') == 1
    assert rig.ctx.gates.in_use('media') == 0 and rig.ctx.gates.in_use('local_stt') == 0
    assert events(first, 'audio_waiting') or events(second, 'audio_waiting')
    assert events(first, 'transcript_waiting') or events(second, 'transcript_waiting')
    waiting = (events(first, 'transcript_waiting') + events(second, 'transcript_waiting'))[0]
    assert waiting['reason'] == 'local_engine'


def test_remote_requests_are_not_gated_and_say_how_many_are_ahead(make_rig, monkeypatch):
    specs = [Spec(n, has_video=True) for n in range(1, 5)]
    rig = make_rig(specs)
    for spec in specs:
        rig.lms.video_path(spec.item_id).with_suffix('.mp3').write_bytes(b'audio')  # audio on disk: only the remote transcript is left
    monkeypatch.setattr(stt, 'transcribe_audio', REAL_TRANSCRIBE_AUDIO)
    monkeypatch.setattr(postprocess_media, 'convert_to_stt_wav', lambda src, dst, should_cancel=None: write_silent_wav(dst, 5))
    lock = threading.Lock()
    state = {'current': 0, 'peak': 0}

    def respond(request):
        with lock:
            state['current'] += 1
            state['peak'] = max(state['peak'], state['current'])
        pause(0.4)
        with lock:
            state['current'] -= 1
        return 200, 'text/plain', srt((0, 3, '원격 자막'))

    with FakeSttServer(respond) as server:
        rig.enable_remote(server.url)
        snapshot = rig.run(['txt'], stt_provider='faster_whisper')

    assert snapshot['status'] == 'succeeded', snapshot['error_code']
    assert state['peak'] >= 3  # the app does not serialize remote work
    assert rig.ctx.remote_stt.in_flight == 0
    busy = [e for e in events(snapshot, 'transcript_waiting') if e.get('reason') == 'server_busy']
    assert busy and all(e['ahead_requests'] >= 1 for e in busy)
    assert rig.ctx.gates.peak('local_stt') == 0 and rig.ctx.gates.in_use('media') == 0


# ---------------------------------------------------------- AC4: isolation


@pytest.mark.parametrize('stage', ['download', 'audio', 'transcript'])
def test_one_failing_lecture_does_not_stop_the_others(make_rig, stage):
    specs = [Spec(n, download=0.05 * n, extract=0.03, transcribe=0.03) for n in range(1, 5)]
    specs[0].fail_at = stage  # the first lecture fails early while 2-4 are still downloading
    specs[0].fail_code = {'download': 'download_http_error', 'audio': 'ffmpeg_failed', 'transcript': 'stt_failed'}[stage]
    rig = make_rig(specs)

    snapshot = rig.run(['mp3', 'txt'])

    assert snapshot['status'] == 'failed' and snapshot['error_code'] == 'partial_failure'
    result = snapshot['result']
    assert result['summary'] == {'total': 4, 'succeeded': 3, 'failed': 1, 'cancelled': 0}
    by_id = {entry['module_item_id']: entry for entry in result['items_status']}
    assert by_id[1] == {'module_item_id': 1, 'state': 'failed', 'error_code': specs[0].fail_code, 'failed_stage': stage}
    assert all(by_id[n]['state'] == 'done' and by_id[n]['error_code'] is None for n in (2, 3, 4))
    for n in (2, 3, 4):
        assert rig.lms.video_path(n).with_suffix('.transcript.txt').exists()
    failed = events(snapshot, 'artifact_item_failed')
    assert [(e['module_item_id'], e['error_code'], e['failed_stage']) for e in failed] == [(1, specs[0].fail_code, stage)]
    assert len(events(snapshot, 'artifact_item_done')) == 3
    # lecture 1 failed while the others were still going: they were processed after its failure
    assert events(snapshot, 'artifact_item_failed')[0]['at'] < max(e['at'] for e in events(snapshot, 'artifact_item_done'))


def test_a_failed_lecture_keeps_its_own_files_and_others_clean_up_theirs(make_rig):
    specs = [Spec(1, has_video=True, fail_at='audio'), Spec(2, has_video=True), Spec(3, download=0.05)]
    rig = make_rig(specs)

    snapshot = rig.run(['txt'])

    assert snapshot['error_code'] == 'partial_failure'
    assert rig.lms.video_path(1).exists()  # not cleaned up: its own chain failed
    assert not rig.lms.video_path(3).exists()  # downloaded by this job and done: source video removed
    assert rig.lms.video_path(2).exists()  # pre-existing video is not this job's to delete
    assert not rig.lms.video_path(3).with_suffix('.mp3').exists()  # intermediate audio removed by lecture 3 only
    assert rig.lms.video_path(3).with_suffix('.transcript.txt').exists()
    assert events(snapshot, 'source_video_removed')[0]['module_item_id'] == 3


def test_all_lectures_failing_with_one_code_reports_that_code(make_rig):
    specs = [Spec(n, has_video=True, fail_at='audio') for n in (1, 2, 3)]
    snapshot = make_rig(specs).run(['mp3'])

    assert (snapshot['status'], snapshot['error_code']) == ('failed', 'ffmpeg_failed')
    assert snapshot['result']['summary'] == {'total': 3, 'succeeded': 0, 'failed': 3, 'cancelled': 0}


def test_failures_with_different_codes_report_partial_failure(make_rig):
    specs = [Spec(1, has_video=True, fail_at='audio', fail_code='ffmpeg_failed'), Spec(2, has_video=True, fail_at='audio', fail_code='audio_extraction_failed')]
    snapshot = make_rig(specs).run(['mp3'])

    assert (snapshot['status'], snapshot['error_code']) == ('failed', 'partial_failure')


def test_login_or_plan_failure_fails_the_job_before_any_lecture_starts(make_rig):
    rig = make_rig([Spec(1, has_video=True), Spec(2)], plan_error='authentication_failed')

    snapshot = rig.run(['mp3'])

    assert (snapshot['status'], snapshot['error_code']) == ('failed', 'authentication_failed')
    assert rig.extract_calls == [] and events(snapshot, 'artifact_item_queued') == []


def test_a_download_batch_that_dies_fails_only_the_lectures_that_were_waiting_for_it(make_rig, monkeypatch):
    rig = make_rig([Spec(1, has_video=True), Spec(2, download=0.1), Spec(3, download=0.1)])
    original = rig.lms.batch_download_lectures

    def exploding(*args, **kwargs):
        if kwargs.get('dry_run'):
            return original(*args, **kwargs)
        raise RuntimeError('network gone')

    monkeypatch.setattr(rig.lms, 'batch_download_lectures', exploding)

    snapshot = rig.run(['mp3'])

    assert snapshot['error_code'] == 'partial_failure'
    by_id = {entry['module_item_id']: entry for entry in snapshot['result']['items_status']}
    assert by_id[1]['state'] == 'done' and by_id[2]['state'] == 'failed' and by_id[3]['failed_stage'] == 'download'


def test_a_chain_that_raises_unexpectedly_fails_alone(make_rig, monkeypatch):
    rig = make_rig([Spec(1, has_video=True), Spec(2, has_video=True)])
    inner = rig._extract

    def flaky(request, on_progress=None, should_cancel=None):
        if request.input_path.stem == 'lecture-1':
            raise ValueError('boom')
        return inner(request, on_progress=on_progress, should_cancel=should_cancel)

    monkeypatch.setattr(postprocess_media, 'extract_audio', flaky)

    snapshot = rig.run(['mp3'])

    by_id = {entry['module_item_id']: entry for entry in snapshot['result']['items_status']}
    assert by_id[1]['error_code'] == 'ValueError' and by_id[1]['failed_stage'] == 'audio' and by_id[2]['state'] == 'done'
    assert rig.ctx.gates.in_use('media') == 0


# -------------------------------------------------------------- AC5: cancel


def cancel_and_wait(rig: Rig, job: Job, *, limit: float = 3.0) -> tuple[dict[str, Any], float, float]:
    cancelled_at = time.time()
    started = time.monotonic()
    job.mark_cancel_requested()
    snapshot = finish(job, timeout=limit + 2)
    return snapshot, time.monotonic() - started, cancelled_at


def assert_cancelled_cleanly(rig: Rig, snapshot: dict[str, Any], elapsed: float, cancelled_at: float) -> None:
    assert snapshot['status'] == 'cancelled'
    assert elapsed < 3.0
    assert snapshot['result'] is not None and snapshot['error_code'] is None
    late = [e for e in snapshot['progress'] if e['stage'] in {'extracting_audio', 'transcribing'} and e['at'] > cancelled_at]
    assert late == []
    assert rig.ctx.gates.in_use('media') == 0 and rig.ctx.gates.in_use('local_stt') == 0
    summary = snapshot['result']['summary']
    assert summary['succeeded'] + summary['failed'] + summary['cancelled'] == summary['total']
    assert summary['cancelled'] >= 1


def test_cancel_while_lectures_are_downloading(make_rig):
    rig = make_rig([Spec(n, download=10.0) for n in range(1, 5)])
    job = rig.start(['mp3'])
    rig.wait_for(lambda: len(events(job, 'downloading')) == 4, message='downloads to start')

    snapshot, elapsed, cancelled_at = cancel_and_wait(rig, job)

    assert_cancelled_cleanly(rig, snapshot, elapsed, cancelled_at)
    assert rig.extract_calls == []
    assert {e['module_item_id'] for e in events(snapshot, 'artifact_item_cancelled')} == {1, 2, 3, 4}


def test_cancel_while_lectures_wait_for_the_ffmpeg_limit_and_while_two_extract(make_rig):
    rig = make_rig([Spec(n, has_video=True, block_extract=True) for n in range(1, 6)])
    job = rig.start(['mp3'])
    rig.wait_for(lambda: rig.media.current == 2 and len(events(job, 'audio_waiting')) == 3, message='2 extracting + 3 waiting')

    snapshot, elapsed, cancelled_at = cancel_and_wait(rig, job)

    assert_cancelled_cleanly(rig, snapshot, elapsed, cancelled_at)
    assert len(rig.extract_calls) == 2  # the three waiting lectures never started after the cancel
    assert snapshot['result']['summary']['cancelled'] == 5


def test_cancel_while_lectures_wait_for_the_local_engine(make_rig):
    rig = make_rig([Spec(n, has_video=True, block_transcribe=True) for n in range(1, 4)])
    job = rig.start(['txt'], stt_provider='fake')
    rig.wait_for(lambda: rig.engine.current == 1 and len(events(job, 'transcript_waiting')) == 2, timeout=8, message='1 transcribing + 2 waiting')

    snapshot, elapsed, cancelled_at = cancel_and_wait(rig, job)

    assert_cancelled_cleanly(rig, snapshot, elapsed, cancelled_at)
    assert len(rig.transcribe_calls) == 1


def test_cancel_while_remote_requests_are_waiting_on_the_server(make_rig, monkeypatch):
    specs = [Spec(n, has_video=True) for n in (1, 2)]
    rig = make_rig(specs)
    for spec in specs:
        rig.lms.video_path(spec.item_id).with_suffix('.mp3').write_bytes(b'audio')
    monkeypatch.setattr(stt, 'transcribe_audio', REAL_TRANSCRIBE_AUDIO)
    monkeypatch.setattr(postprocess_media, 'convert_to_stt_wav', lambda src, dst, should_cancel=None: write_silent_wav(dst, 5))

    def slow(request):
        rig.signal.notify()  # the request is already recorded by the fake server
        pause(6)
        return 200, 'text/plain', srt((0, 1, 'late'))

    with FakeSttServer(slow) as server:
        rig.enable_remote(server.url)
        job = rig.start(['txt'], stt_provider='faster_whisper')
        rig.wait_for(lambda: len(server.requests) == 2, message='both requests at the server')

        snapshot, elapsed, cancelled_at = cancel_and_wait(rig, job)

    assert_cancelled_cleanly(rig, snapshot, elapsed, cancelled_at)
    assert rig.ctx.remote_stt.in_flight == 0
    assert not list(rig.tmp_path.glob('course/*.transcript.txt'))


# ----------------------------------------------- AC8: payload and event contract


def test_job_snapshot_and_result_keep_their_keys_and_only_add(make_rig):
    rig = make_rig([Spec(1, download=0.01), Spec(2, has_video=True)])

    snapshot = rig.run(['mp3', 'txt'])

    assert set(snapshot) == {'job_id', 'status', 'cancel_requested', 'progress', 'result', 'error_code'}
    result = snapshot['result']
    legacy_result_keys = {'ok', 'error_code', 'download', 'artifacts', 'progress'}
    assert legacy_result_keys <= set(result) and set(result) - legacy_result_keys == {'summary', 'items_status'}
    assert set(result['download']) == {'ok', 'error_code', 'dry_run', 'course_id', 'course_name', 'total_count', 'success_count', 'failure_count', 'items', 'progress'}
    assert set(result['summary']) == {'total', 'succeeded', 'failed', 'cancelled'}
    assert all(set(entry) == {'module_item_id', 'state', 'error_code', 'failed_stage'} for entry in result['items_status'])
    assert result['ok'] is True and result['error_code'] is None


def test_existing_progress_events_keep_their_keys_and_gain_only_at(make_rig):
    rig = make_rig([Spec(1, download=0.01)])

    snapshot = rig.run(['mp3', 'txt'])

    context = {'index', 'total', 'module_item_id', 'title'}
    expected = {
        'extracting_audio': context | {'stage', 'output_path'},
        'audio_done': context | {'stage', 'output_path', 'file_size', 'progress_percent'},
        'transcribing': context | {'stage', 'output_path', 'provider', 'model', 'remote'},
        'transcript_done': context | {'stage', 'output_path', 'progress_percent'},
        'artifact_audio_started': {'stage', 'total', 'max_parallel_downloads'},
        'artifact_transcripts_queued': {'stage', 'total', 'max_parallel_downloads'},
        'downloading': context | {'stage'},
        'done': context | {'stage', 'output_path'},
    }
    for stage, keys in expected.items():
        event = events(snapshot, stage)[0]
        assert set(event) == keys | {'at'}, stage
    new_stages = {'artifact_item_queued': context, 'artifact_item_done': context}
    for stage, keys in new_stages.items():
        assert set(events(snapshot, stage)[0]) == keys | {'stage', 'at'}
    assert all(isinstance(e['at'], float) for e in snapshot['progress'])


def test_thirty_lectures_stay_classifiable_after_the_400_event_cap(make_rig, monkeypatch):
    specs = [Spec(n, has_video=True, extract=0.005, transcribe=0.005) for n in range(1, 31)]
    specs[6].fail_at = 'transcript'
    rig = make_rig(specs)
    inner = rig._extract

    def chatty(request, on_progress=None, should_cancel=None):
        for tick in range(15):  # not coalesced: every tick is its own event
            on_progress({'stage': 'audio_note', 'tick': tick})
        return inner(request, on_progress=on_progress, should_cancel=should_cancel)

    monkeypatch.setattr(postprocess_media, 'extract_audio', chatty)
    snapshot = rig.run(['mp3', 'txt'])

    assert len(snapshot['progress']) == MAX_JOB_PROGRESS_EVENTS  # the cap was hit and early events were dropped
    assert len(events(snapshot, 'artifact_item_done')) == 29  # milestones outlive the noise: only plain events were evicted
    assert not events(snapshot, 'audio_note')[:1] or len(events(snapshot, 'audio_note')) < 30 * 15
    entries = {entry['module_item_id']: entry for entry in snapshot['result']['items_status']}
    assert set(entries) == set(range(1, 31))
    assert entries[7]['state'] == 'failed' and entries[7]['failed_stage'] == 'transcript'
    assert sum(entry['state'] == 'done' for entry in entries.values()) == 29
    assert snapshot['result']['summary'] == {'total': 30, 'succeeded': 29, 'failed': 1, 'cancelled': 0}
    assert (snapshot['status'], snapshot['error_code']) == ('failed', 'partial_failure')


# ------------------------------------------- LmsService.batch_download_lectures(on_item_ready)


def batch_kwargs(tmp_path: Path, **extra: Any) -> dict[str, Any]:
    return {'course_id': 42, 'course_name': '자료/구조', 'output_dir': tmp_path, 'module_item_ids': [111], **extra}


def test_on_item_ready_reports_a_downloaded_item_before_the_batch_returns(tmp_path):
    service = SsuLectureService(http=FakeHttp(), login_func=ok_login)
    ready: list[BatchDownloadItemResult] = []

    result = service.batch_download_lectures('user', 'password', dry_run=False, on_item_ready=ready.append, **batch_kwargs(tmp_path))

    assert [(item.module_item_id, item.ok, item.written) for item in ready] == [(111, True, True)]
    assert ready[0] == result.items[0] and Path(ready[0].output_path).read_bytes() == b'1234'


def test_on_item_ready_reports_skipped_items_and_never_fires_for_a_dry_run(tmp_path):
    service = SsuLectureService(http=FakeHttp(), login_func=ok_login)
    existing = tmp_path / '자료_구조' / '테스트 강의 [content-1].mp4'
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b'existing')
    ready: list[BatchDownloadItemResult] = []

    service.batch_download_lectures('user', 'password', dry_run=True, on_item_ready=ready.append, conflict_strategy='skip', **batch_kwargs(tmp_path))
    assert ready == []

    service.batch_download_lectures('user', 'password', dry_run=False, on_item_ready=ready.append, conflict_strategy='skip', **batch_kwargs(tmp_path))
    assert [(item.skipped, item.written) for item in ready] == [(True, False)]


def test_on_item_ready_reports_a_failed_download_and_a_raising_callback_does_not_break_the_batch(tmp_path, monkeypatch):
    from ssu_lecture_downloader.core import lms_service as service_mod

    service = SsuLectureService(http=FakeHttp(), login_func=ok_login)

    def broken_download(**kwargs):
        raise OSError('disk full')

    monkeypatch.setattr(service_mod, 'download_to_plan', broken_download)
    seen: list[BatchDownloadItemResult] = []

    def callback(item):
        seen.append(item)
        raise RuntimeError('consumer bug')

    result = service.batch_download_lectures('user', 'password', dry_run=False, on_item_ready=callback, **batch_kwargs(tmp_path))

    assert [(item.ok, item.error_code) for item in seen] == [(False, 'OSError')]
    assert result.error_code == 'partial_failure' and len(result.items) == 1


# --------------------------------------------- repair batch: waits, cap, order, failure state


def assert_no_audio_waiting_after_transcript_started(snapshot: dict[str, Any]) -> None:
    seen_transcript: set[Any] = set()
    for event in snapshot['progress']:
        lecture = event.get('module_item_id')
        if event['stage'] == 'transcribing' or event['stage'].startswith('transcript_'):
            seen_transcript.add(lecture)
        assert not (event['stage'] == 'audio_waiting' and lecture in seen_transcript), event


def test_media_waits_inside_the_transcript_stage_are_transcript_waits_not_audio_waits(make_rig, monkeypatch):
    specs = [Spec(n, has_video=True) for n in range(1, 6)]
    rig = make_rig(specs)
    for spec in specs:
        rig.lms.video_path(spec.item_id).with_suffix('.mp3').write_bytes(b'audio')  # only the remote transcript is left
    monkeypatch.setattr(stt, 'transcribe_audio', REAL_TRANSCRIBE_AUDIO)
    monkeypatch.setattr(postprocess_media, 'convert_to_stt_wav', lambda src, dst, should_cancel=None: (pause(0.15), write_silent_wav(dst, 5)))

    with FakeSttServer(lambda request: (200, 'text/plain', srt((0, 3, '원격 자막')))) as server:
        rig.enable_remote(server.url)
        snapshot = rig.run(['txt'], stt_provider='faster_whisper')

    assert snapshot['status'] == 'succeeded', snapshot['error_code']
    media_waits = [e for e in events(snapshot, 'transcript_waiting') if e.get('reason') == 'media']
    assert media_waits  # 5 conversions through a limit of 2
    assert events(snapshot, 'audio_waiting') == []
    assert_no_audio_waiting_after_transcript_started(snapshot)


def test_mp4_reconstruction_waits_carry_step_mp4(make_rig):
    rig = make_rig([Spec(n, has_video=True) for n in range(1, 7)])

    snapshot = rig.run(['mp4'])

    waits = events(snapshot, 'audio_waiting')
    assert waits and {e['step'] for e in waits} == {'mp4'}


def test_plain_audio_extraction_waits_carry_step_audio(make_rig):
    rig = make_rig([Spec(n, has_video=True, extract=0.1) for n in range(1, 6)])

    snapshot = rig.run(['mp3'])

    waits = events(snapshot, 'audio_waiting')
    assert waits and {e['step'] for e in waits} == {'audio'}


def test_over_the_cap_plain_events_go_first_and_every_unfinished_lecture_keeps_its_milestone(make_rig, monkeypatch):
    rig = make_rig([Spec(n, has_video=True, block_extract=True) for n in range(1, 31)])
    inner = rig._extract
    noisy = {'finished': 0}
    noisy_lock = threading.Lock()

    def chatty(request, on_progress=None, should_cancel=None):
        for tick in range(500):  # 1000 plain events from the two running extractions: far over the 400 cap
            on_progress({'stage': 'audio_note', 'tick': tick})
        with noisy_lock:
            noisy['finished'] += 1
        rig.signal.notify()
        return inner(request, on_progress=on_progress, should_cancel=should_cancel)

    monkeypatch.setattr(postprocess_media, 'extract_audio', chatty)
    job = rig.start(['mp3'])
    rig.wait_for(lambda: noisy['finished'] == 2 and len(events(job, 'audio_waiting')) == 28, message='both noisy extractions done and 28 lectures waiting')

    progress = job.progress_events()  # mid-run: nothing has finished
    assert len(progress) <= MAX_JOB_PROGRESS_EVENTS and len(events(job, 'audio_note')) < 1000
    for n in range(1, 31):
        mine = [e for e in progress if e.get('module_item_id') == n]
        assert any(e['stage'] == 'artifact_item_queued' for e in mine), n
    waiting_ids = {e['module_item_id'] for e in events(job, 'audio_waiting')}
    assert len(waiting_ids) == 28  # the two lectures that hold a slot are not waiting; every other one still shows it

    snapshot, elapsed, _ = cancel_and_wait(rig, job)
    assert snapshot['status'] == 'cancelled' and elapsed < 3.0


def test_milestones_are_dropped_only_when_nothing_else_is_left():
    from ssu_lecture_downloader.core.jobs import append_compacted_progress

    progress: list[dict[str, Any]] = []
    for n in range(MAX_JOB_PROGRESS_EVENTS + 25):
        append_compacted_progress(progress, {'stage': 'artifact_item_done', 'module_item_id': n, 'at': float(n)})

    assert len(progress) == MAX_JOB_PROGRESS_EVENTS
    assert progress[0]['module_item_id'] == 25  # oldest milestone went, in order, because there was nothing else
    append_compacted_progress(progress, {'stage': 'audio_note', 'at': 1e9})
    assert len(progress) == MAX_JOB_PROGRESS_EVENTS and progress[-1]['stage'] == 'audio_note'


def test_waiting_events_move_to_the_end_instead_of_being_replaced_in_place():
    from ssu_lecture_downloader.core.jobs import append_compacted_progress

    def waiting(n: int, stage: str = 'transcript_waiting', **extra: Any) -> dict[str, Any]:
        return {'stage': stage, 'module_item_id': 1, 'index': 1, 'audio_path': 'a.mp3', 'at': float(n), **extra}

    progress: list[dict[str, Any]] = []
    append_compacted_progress(progress, waiting(1, reason='local_engine'))
    append_compacted_progress(progress, {'stage': 'transcribing', 'module_item_id': 1, 'at': 2.0})
    append_compacted_progress(progress, waiting(3, reason='server_busy', ahead_requests=1))
    append_compacted_progress(progress, {'stage': 'transcript_progress', 'module_item_id': 1, 'at': 4.0, 'audio_path': 'a.mp3'})
    append_compacted_progress(progress, waiting(5, reason='server_busy', ahead_requests=2))
    append_compacted_progress(progress, waiting(6, stage='audio_waiting'))
    append_compacted_progress(progress, waiting(7, stage='audio_waiting', step='audio'))

    assert [e['stage'] for e in progress] == ['transcribing', 'transcript_progress', 'transcript_waiting', 'audio_waiting']
    assert progress[2]['ahead_requests'] == 2 and progress[3]['step'] == 'audio'
    assert [e['at'] for e in progress] == sorted(e['at'] for e in progress)  # still monotonic


def test_remote_repair_sequence_stays_chronological_in_the_stored_list():
    """started, progress 60, repair 1/3, waiting, repair 2/3, repair 3/3, progress 99: the last entry is progress 99."""
    from ssu_lecture_downloader.core.jobs import append_compacted_progress

    base = {'module_item_id': 1, 'index': 1, 'audio_path': 'a.mp3'}
    sequence = [
        {'stage': 'transcript_started', **base},
        {'stage': 'transcript_progress', 'progress_percent': 60, **base},
        {'stage': 'transcript_repair', 'repair_index': 1, 'repair_total': 3, **base},
        {'stage': 'transcript_waiting', 'reason': 'server_busy', 'ahead_requests': 1, **base},
        {'stage': 'transcript_repair', 'repair_index': 2, 'repair_total': 3, **base},
        {'stage': 'transcript_repair', 'repair_index': 3, 'repair_total': 3, **base},
        {'stage': 'transcript_progress', 'progress_percent': 99, **base},
    ]
    progress: list[dict[str, Any]] = []
    for n, event in enumerate(sequence):
        append_compacted_progress(progress, {**event, 'at': float(n)})

    assert progress[-1]['stage'] == 'transcript_progress' and progress[-1]['progress_percent'] == 99
    assert [e['stage'] for e in progress] == ['transcript_started', 'transcript_waiting', 'transcript_repair', 'transcript_progress']
    assert progress[2]['repair_index'] == 3
    stamps = [e['at'] for e in progress]
    assert stamps == sorted(stamps)


def test_waiting_notes_stay_monotonic_through_a_real_run(make_rig):
    rig = make_rig([Spec(n, has_video=True, extract=0.03, transcribe=0.03) for n in range(1, 7)])

    snapshot = rig.run(['mp3', 'txt'])

    stamps = [e['at'] for e in snapshot['progress']]
    assert stamps == sorted(stamps)
    for n in range(1, 7):
        waits = [e for e in events(snapshot, 'transcript_waiting', n)]
        assert len(waits) <= 1  # one entry per lecture: later ones replaced the earlier


def test_a_real_error_stays_a_failure_even_if_the_job_is_cancelled_right_after(make_rig, monkeypatch):
    rig = make_rig([Spec(1, has_video=True), Spec(2, has_video=True, block_extract=True)])
    inner = rig._extract

    def fail_then_cancel(request, on_progress=None, should_cancel=None):
        if request.input_path.stem == 'lecture-1':
            assert rig.job_started.wait(5), 'job handle'
            rig.job.mark_cancel_requested()  # the cancel flag is set before this error is reported
            return AudioExtractionResult(ok=False, input_path=request.input_path, error_code='ffmpeg_failed', message='bad input')
        return inner(request, on_progress=on_progress, should_cancel=should_cancel)

    monkeypatch.setattr(postprocess_media, 'extract_audio', fail_then_cancel)

    snapshot = rig.run(['mp3'])

    assert snapshot['status'] == 'cancelled'
    by_id = {entry['module_item_id']: entry for entry in snapshot['result']['items_status']}
    assert by_id[1] == {'module_item_id': 1, 'state': 'failed', 'error_code': 'ffmpeg_failed', 'failed_stage': 'audio'}
    assert by_id[2]['state'] == 'cancelled'
    assert [e['module_item_id'] for e in events(snapshot, 'artifact_item_failed')] == [1]
