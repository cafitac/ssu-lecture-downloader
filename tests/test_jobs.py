import threading
import time

from ssu_lecture_downloader.core.jobs import JobCancelled, JobOutcome, JobRegistry, JobRunner, ProgressCollector, append_compacted_progress


def wait_for(job, statuses=('succeeded', 'failed', 'cancelled'), timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if job.snapshot()['status'] in statuses:
            return job.snapshot()
        time.sleep(0.005)
    raise AssertionError(f"job stuck in {job.snapshot()['status']}")


def test_runner_marks_job_failed_with_error_code_when_work_raises():
    runner = JobRunner()
    registry = JobRegistry()

    def work(job):
        raise PermissionError('cannot write')

    snapshot = wait_for(runner.start(registry, work))

    assert snapshot['status'] == 'failed'
    assert snapshot['error_code'] == 'PermissionError'


def test_runner_uses_the_supplied_error_code_mapper():
    runner = JobRunner(error_code_for=lambda exc: f'mapped:{type(exc).__name__}')

    snapshot = wait_for(runner.start(JobRegistry(), lambda job: (_ for _ in ()).throw(OSError('boom'))))

    assert snapshot['error_code'] == 'mapped:OSError'


def test_runner_reports_outcome_result_and_error_code():
    runner = JobRunner()
    registry = JobRegistry()

    ok = wait_for(runner.start(registry, lambda job: JobOutcome.succeeded({'value': 1})))
    failed = wait_for(runner.start(registry, lambda job: JobOutcome.failed('disk_full', result={'partial': True})))

    assert (ok['status'], ok['result'], ok['error_code']) == ('succeeded', {'value': 1}, None)
    assert (failed['status'], failed['result'], failed['error_code']) == ('failed', {'partial': True}, 'disk_full')


def test_cancel_flag_reaches_the_work_function_and_ends_the_job_cancelled():
    runner = JobRunner()
    registry = JobRegistry()
    started = threading.Event()

    def work(job):
        started.set()
        while not job.should_cancel():
            time.sleep(0.005)
        return JobOutcome.failed('cancelled', result={'partial': True})

    job = runner.start(registry, work)
    assert started.wait(5)
    job.mark_cancel_requested()
    snapshot = wait_for(job)

    assert snapshot['status'] == 'cancelled'
    assert snapshot['cancel_requested'] is True
    assert snapshot['result'] == {'partial': True}


def test_cancel_wins_over_a_successful_return_and_over_exceptions():
    runner = JobRunner()
    registry = JobRegistry()
    gate = threading.Event()

    def succeeding(job):
        gate.wait(5)
        return JobOutcome.succeeded({'done': True})

    def raising(job):
        gate.wait(5)
        raise RuntimeError('late failure')

    first = runner.start(registry, succeeding)
    second = runner.start(registry, raising)
    first.mark_cancel_requested()
    second.mark_cancel_requested()
    gate.set()

    assert wait_for(first)['status'] == 'cancelled'
    assert wait_for(second)['status'] == 'cancelled'


def test_job_cancelled_before_it_starts_never_runs():
    registry = JobRegistry()
    job = registry.create()
    job.mark_cancel_requested()
    calls = []

    JobRunner._run(job, lambda j: calls.append('ran'), lambda exc: 'x')

    assert calls == []
    assert job.snapshot()['status'] == 'cancelled'


def test_job_cancelled_exception_ends_cancelled():
    runner = JobRunner()

    def work(job):
        raise JobCancelled()

    assert wait_for(runner.start(JobRegistry(), work))['status'] == 'cancelled'


def test_runner_wait_returns_quick_jobs_in_the_start_response():
    runner = JobRunner()

    job = runner.start(JobRegistry(), lambda j: JobOutcome.succeeded({}), wait_seconds=2)

    assert job.snapshot()['status'] == 'succeeded'


def test_registry_prunes_old_finished_jobs_but_never_active_ones():
    registry = JobRegistry(max_finished=3, finished_ttl_seconds=3600)
    active = registry.create()
    active.begin()
    finished_ids = []
    for _ in range(6):
        job = registry.create()
        job.begin()
        job.set_status('succeeded')
        finished_ids.append(job.job_id)
        time.sleep(0.002)

    registry.create()

    assert registry.get(active.job_id) is active
    kept = [job_id for job_id in finished_ids if registry.get(job_id) is not None]
    assert kept == finished_ids[-3:]


def test_registry_prunes_finished_jobs_past_the_ttl():
    registry = JobRegistry(max_finished=100, finished_ttl_seconds=0.01)
    job = registry.create()
    job.begin()
    job.set_status('failed', error_code='x')
    time.sleep(0.05)

    registry.create()

    assert registry.get(job.job_id) is None


def test_progress_collector_and_compaction_cap():
    collector = ProgressCollector()
    for index in range(1000):
        collector({'stage': 'step', 'index': index})
    collector({'stage': 'download_progress', 'module_item_id': 1, 'output_path': '/a', 'bytes_written': 1})
    collector({'stage': 'download_progress', 'module_item_id': 1, 'output_path': '/a', 'bytes_written': 2})

    events = collector.events()
    assert len(events) == 400
    assert [event for event in events if event['stage'] == 'download_progress'] == [
        {'stage': 'download_progress', 'module_item_id': 1, 'output_path': '/a', 'bytes_written': 2}
    ]
    progress = []
    append_compacted_progress(progress, {'stage': 'x'})
    assert progress == [{'stage': 'x'}]
