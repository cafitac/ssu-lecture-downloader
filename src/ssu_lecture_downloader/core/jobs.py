"""Background job bookkeeping shared by every long-running API operation.

One `Job` per request, a `JobRegistry` per job kind (so ids stay in their own
namespace), and a `JobRunner` that owns the thread, the failure handling and the
cancel contract. Work functions never set terminal statuses on error paths
themselves: they return a `JobOutcome` or raise, and the runner translates that.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

MAX_JOB_PROGRESS_EVENTS = 400
COALESCED_PROGRESS_STAGES = {
    "download_progress",
    "audio_progress",
    "transcript_progress",
    "transcript_repair",
    # Wait notes (queue position, "n requests ahead") replace each other per lecture.
    "transcript_waiting",
    "transcript_queued",
    "audio_waiting",
}
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
# Finished jobs are kept so late polls still resolve, but not forever.
DEFAULT_MAX_FINISHED_JOBS = 50
DEFAULT_FINISHED_JOB_TTL_SECONDS = 6 * 60 * 60


def _progress_identity(event: dict[str, Any]) -> tuple[object, object, object, object]:
    return (
        event.get("stage"),
        event.get("index"),
        str(event.get("module_item_id")) if event.get("module_item_id") is not None else None,
        event.get("output_path") or event.get("input_path") or event.get("audio_path"),
    )


# Stage-entry events: the first event of a step, which says which step a lecture is in (later ticks only refine it).
STAGE_ENTRY_PROGRESS_STAGES = frozenset({
    "downloading",
    "download_started",
    "extracting_audio",
    "transcribing",
    "transcript_connecting",
    "transcript_started",
})


def is_milestone_event(event: dict[str, Any]) -> bool:
    """Events that say where a lecture is: they are the last to be dropped when the list is over its cap.

    Terminal and waiting notes, the entry event of each step (so a row never falls back to an earlier step once
    its `audio_done` survives), and a bare `failed`, which a batch emits as an item's final result.
    """
    stage = str(event.get("stage") or "")
    return (
        stage.startswith("artifact_item_")
        or stage in ("done", "failed", "transcript_queued")
        or stage in STAGE_ENTRY_PROGRESS_STAGES
        or stage.endswith("_done")
        or stage.endswith("_waiting")
        or (stage.startswith("video_") and stage.endswith("_started"))
    )


TERMINAL_ITEM_STAGES = frozenset({"artifact_item_done", "artifact_item_failed", "artifact_item_cancelled"})
# What stays of a finished lecture when the list is over its cap: its entry and its end.
FINISHED_ITEM_KEPT_STAGES = TERMINAL_ITEM_STAGES | {"artifact_item_queued"}


def _lecture_key(event: dict[str, Any]) -> str | None:
    module_item_id = event.get("module_item_id")
    return str(module_item_id) if module_item_id is not None else None


def _trim_to_cap(progress: list[dict[str, Any]]) -> None:
    """Drop events until the list fits the cap, least informative first.

    1. Plain events (noise ticks, notes) and everything a finished lecture left behind except its
       `artifact_item_queued` and terminal `artifact_item_*` event: the row is closed, only its end matters.
    2. Only when nothing of that kind is left, the oldest remaining events, whatever they are.

    A running lecture's coalesced entries (one per identity: its newest tick or wait note) and the event just
    appended are not in the first group, so finished lectures cannot push live percentages out of the list.
    """
    overflow = len(progress) - MAX_JOB_PROGRESS_EVENTS
    if overflow <= 0:
        return
    finished = {key for item in progress if item.get("stage") in TERMINAL_ITEM_STAGES and (key := _lecture_key(item)) is not None}
    newest = progress[-1]

    def evict_first(item: dict[str, Any]) -> bool:
        if item is newest:
            return False
        stage = item.get("stage")
        if _lecture_key(item) in finished:
            return stage not in FINISHED_ITEM_KEPT_STAGES
        if stage in COALESCED_PROGRESS_STAGES:
            return False
        return not is_milestone_event(item)

    keep: list[dict[str, Any]] = []
    for item in progress:
        if overflow > 0 and evict_first(item):
            overflow -= 1
        else:
            keep.append(item)
    if overflow > 0:
        del keep[:overflow]
    progress[:] = keep


def append_compacted_progress(progress: list[dict[str, Any]], event: dict[str, Any]) -> None:
    """Append progress without retaining every byte-level tick forever.

    A coalesced stage deletes its previous entry (same lecture and file) and appends the new one at the end, so the
    list stays chronological: `at` never decreases along it.

    Over the cap see `_trim_to_cap`: plain events and finished lectures' leftovers go first (oldest first);
    milestones and running lectures' newest ticks are dropped only when nothing else is left.
    """
    stage = event.get("stage")
    if stage in COALESCED_PROGRESS_STAGES:
        identity = _progress_identity(event)
        for index in range(len(progress) - 1, -1, -1):
            if _progress_identity(progress[index]) == identity:
                del progress[index]
                break
    progress.append(event)
    _trim_to_cap(progress)


class ProgressCollector:
    """Thread-safe compacted progress list for synchronous endpoints."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def __call__(self, event: dict[str, Any]) -> None:
        with self._lock:
            append_compacted_progress(self._events, event)

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)


class JobCancelled(Exception):
    """Work functions raise this to end a job as cancelled."""


@dataclass(frozen=True)
class JobOutcome:
    status: str  # "succeeded" | "failed"
    result: dict[str, Any] | None = None
    error_code: str | None = None

    @classmethod
    def succeeded(cls, result: dict[str, Any] | None = None) -> JobOutcome:
        return cls("succeeded", result=result)

    @classmethod
    def failed(cls, error_code: str, result: dict[str, Any] | None = None) -> JobOutcome:
        return cls("failed", result=result, error_code=error_code)


class Job:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.status = "queued"
        self.cancel_requested = False
        self.progress: list[dict[str, Any]] = []
        self.result: dict[str, Any] | None = None
        self.error_code: str | None = None
        self.finished_at: float | None = None
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "job_id": self.job_id,
                "status": self.status,
                "cancel_requested": self.cancel_requested,
                "progress": list(self.progress),
                "result": self.result,
                "error_code": self.error_code,
            }

    def append_progress(self, event: dict[str, Any]) -> None:
        with self._lock:
            append_compacted_progress(self.progress, event)

    def progress_events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.progress)

    def mark_cancel_requested(self) -> None:
        with self._lock:
            self.cancel_requested = True
            if self.status == "queued":
                self.status = "cancelled"
                self.finished_at = time.monotonic()

    def should_cancel(self) -> bool:
        with self._lock:
            return self.cancel_requested

    @property
    def finished(self) -> bool:
        with self._lock:
            return self.status in TERMINAL_STATUSES

    def set_status(self, status: str, *, result: dict[str, Any] | None = None, error_code: str | None = None) -> None:
        with self._lock:
            self.status = status
            self.result = result
            self.error_code = error_code
            if status in TERMINAL_STATUSES:
                self.finished_at = time.monotonic()

    def begin(self) -> bool:
        """queued -> running. False when the job was cancelled before it started."""
        with self._lock:
            if self.status != "queued":
                return False
            self.status = "running"
            return True


class JobRegistry:
    def __init__(
        self,
        *,
        max_finished: int = DEFAULT_MAX_FINISHED_JOBS,
        finished_ttl_seconds: float = DEFAULT_FINISHED_JOB_TTL_SECONDS,
    ) -> None:
        self.max_finished = max_finished
        self.finished_ttl_seconds = finished_ttl_seconds
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self) -> Job:
        job = Job(uuid.uuid4().hex)
        with self._lock:
            self._prune_locked()
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _prune_locked(self) -> None:
        """Drop finished jobs past the TTL or beyond the newest `max_finished`. Active jobs stay."""
        now = time.monotonic()
        finished = [job for job in self._jobs.values() if job.finished_at is not None and job.finished]
        finished.sort(key=lambda job: job.finished_at or 0.0, reverse=True)
        for rank, job in enumerate(finished):
            expired = now - (job.finished_at or now) > self.finished_ttl_seconds
            if rank >= self.max_finished or expired:
                self._jobs.pop(job.job_id, None)


def default_error_code(exc: BaseException) -> str:
    return type(exc).__name__


class JobRunner:
    """Starts work on a daemon thread and guarantees the job reaches a terminal status."""

    def __init__(self, *, error_code_for: Callable[[Exception], str] = default_error_code) -> None:
        self._error_code_for = error_code_for

    def start(
        self,
        registry: JobRegistry,
        work: Callable[[Job], JobOutcome | None],
        *,
        wait_seconds: float = 0.0,
        error_code_for: Callable[[Exception], str] | None = None,
    ) -> Job:
        job = registry.create()
        thread = threading.Thread(
            target=self._run,
            args=(job, work, error_code_for or self._error_code_for),
            name=f"ssu-job-{job.job_id[:8]}",
            daemon=True,
        )
        thread.start()
        if wait_seconds > 0:
            # Quick jobs finish inside the start response so the UI skips a poll.
            thread.join(timeout=wait_seconds)
        return job

    @staticmethod
    def _run(job: Job, work: Callable[[Job], JobOutcome | None], error_code_for: Callable[[Exception], str]) -> None:
        if not job.begin():
            return  # cancelled while queued; mark_cancel_requested already finished it
        try:
            outcome = work(job)
        except JobCancelled:
            job.set_status("cancelled", error_code=None)
            return
        except Exception as exc:  # noqa: BLE001 - every failure must end the job
            try:
                error_code = error_code_for(exc)
            except Exception:  # noqa: BLE001
                error_code = default_error_code(exc)
            status = "cancelled" if job.should_cancel() or type(exc).__name__ == "DownloadCancelled" else "failed"
            job.set_status(status, error_code=error_code)
            return
        if outcome is None:
            outcome = JobOutcome.succeeded()
        status = outcome.status
        if job.should_cancel():
            status = "cancelled"
        job.set_status(status, result=outcome.result, error_code=outcome.error_code if status == "failed" else None)
