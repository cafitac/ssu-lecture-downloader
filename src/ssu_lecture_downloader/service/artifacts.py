"""MP4 / MP3 / TXT artifact pipeline behind `POST /api/download/artifacts/jobs`.

One job plans the course (dry-run), then hands every lecture to its own `LecturePipeline`: a
linear chain `download -> mp4 -> audio -> transcript -> cleanup` that starts the moment that
lecture's input is ready. There is no barrier between lectures (a lecture whose download is
done does not wait for the others) and no shared abort (a failed lecture ends alone; the rest
run to their own end). Only the load on this computer is limited, by the process-wide
`StageGates`: ffmpeg-class work 2, local speech-to-text 1. Remote transcription requests are not
queued by the app.

Existing complete outputs are reused; outputs are only considered complete when they were
swapped in atomically (see postprocess_media / stt).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ssu_lecture_downloader.core import postprocess_media, stt
from ssu_lecture_downloader.core.jobs import Job, JobOutcome
from ssu_lecture_downloader.service.api_models import ArtifactDownloadRequest
from ssu_lecture_downloader.service.app_context import AppContext
from ssu_lecture_downloader.service.payloads import artifact_payload, batch_payload
from ssu_lecture_downloader.service.pipeline_gates import LOCAL_STT, MEDIA, GateCancelled

logger = logging.getLogger(__name__)

CANCELLED = "cancelled"


@dataclass(frozen=True)
class _ItemPaths:
    video: Path
    audio: Path
    text: Path
    md: Path
    json: Path
    srt: Path

    @classmethod
    def for_video(cls, video_path: Path, audio_format: str) -> _ItemPaths:
        audio = postprocess_media.plan_audio_output_path(video_path, video_path.parent, audio_format)  # type: ignore[arg-type]
        text, md, json_path = stt.transcript_output_paths(audio, video_path.parent)
        return cls(video=video_path, audio=audio, text=text, md=md, json=json_path, srt=stt.transcript_srt_path(audio, video_path.parent))

    @property
    def transcript_exists(self) -> bool:
        # The .txt is committed last by write_transcript_files, so it marks a complete set.
        return self.text.exists()


@dataclass(frozen=True)
class LectureResult:
    module_item_id: Any
    state: str  # "done" | "failed" | "cancelled"
    error_code: str | None = None
    failed_stage: str | None = None  # "download" | "mp4" | "audio" | "transcript" | "cleanup"

    def status_entry(self) -> dict[str, Any]:
        return {"module_item_id": self.module_item_id, "state": self.state, "error_code": self.error_code, "failed_stage": self.failed_stage}


class LecturePipeline:
    """One lecture's chain. It reads and writes only its own files and its own state."""

    def __init__(self, owner: ArtifactPipeline, index: int, total: int, plan_item: Any) -> None:
        self.owner = owner
        self.module_item_id = getattr(plan_item, "module_item_id", None)
        self.context: dict[str, Any] = {"index": index, "total": total, "module_item_id": self.module_item_id, "title": getattr(plan_item, "title", None)}
        self.item = plan_item
        self.submitted = False
        self.result: LectureResult | None = None
        # A failure known at hand-off (download failed, batch cancelled): the chain only reports it.
        self.preset_failure: tuple[str, str] | None = None
        self.stage = "download"

    # ------------------------------------------------------------------ run

    def run(self) -> LectureResult:
        try:
            code = self._steps()
        except GateCancelled:
            code = CANCELLED
        except Exception as exc:  # noqa: BLE001 - one lecture's bug must never reach the executor or a sibling
            logger.exception("lecture pipeline failed unexpectedly")
            code = type(exc).__name__
        if code is None:
            result = LectureResult(self.module_item_id, "done")
            self.owner.emit({"stage": "artifact_item_done", **self.context})
        elif code == CANCELLED or self.owner.should_cancel():
            result = LectureResult(self.module_item_id, "cancelled")
            self.owner.emit({"stage": "artifact_item_cancelled", **self.context})
        else:
            result = LectureResult(self.module_item_id, "failed", error_code=code, failed_stage=self.stage)
            self.owner.emit({"stage": "artifact_item_failed", **self.context, "error_code": code, "failed_stage": self.stage})
        self.result = result
        return result

    def _steps(self) -> str | None:
        owner = self.owner
        if self.preset_failure is not None:
            code, self.stage = self.preset_failure
            return code
        if owner.should_cancel():
            return CANCELLED
        request = owner.request
        item = self.item
        output_path = getattr(item, "output_path", None)
        if not output_path:
            return None
        paths = _ItemPaths.for_video(Path(output_path), request.audio_format)
        outputs = owner.outputs
        video_exists = paths.video.exists()

        error_code: str | None = None
        if "mp4" in outputs:
            self.stage = "mp4"
            error_code = self._ensure_mp4(item, paths) if video_exists else "video_not_found"
        audio_path = paths.audio
        # Audio made only to feed the transcriber is removed again unless MP3 was asked for.
        wants_mp3 = "mp3" in outputs
        audio_preexisting = paths.audio.exists()
        transcript_ready = "txt" in outputs and paths.transcript_exists and not request.overwrite_artifacts
        if error_code is None and owner.should_cancel():
            return CANCELLED
        if error_code is None and owner.wants_audio and (wants_mp3 or not transcript_ready):
            self.stage = "audio"
            error_code, audio_path = self._ensure_audio(paths, video_exists)
        if error_code is None and owner.should_cancel():
            return CANCELLED
        if error_code is None and "txt" in outputs:
            self.stage = "transcript"
            error_code = self._ensure_transcript(paths, audio_path)
        if error_code is not None:
            return error_code
        self.stage = "cleanup"
        if not wants_mp3 and not audio_preexisting and audio_path.exists():
            if self._remove(audio_path):
                owner.emit({"stage": "intermediate_audio_removed", **self.context, "output_path": str(audio_path)})
        if "mp4" not in outputs and video_exists and bool(getattr(item, "written", False)):
            if self._remove(paths.video):
                owner.emit({"stage": "source_video_removed", **self.context, "output_path": str(paths.video)})
        return None

    @staticmethod
    def _remove(path: Path) -> bool:
        # A file that cannot be removed leaves an extra file behind; the lecture's outputs are complete either way.
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("could not remove intermediate file %s", path, exc_info=True)
            return False
        return True

    # ---------------------------------------------------------------- steps

    def _with_context(self) -> Callable[[dict[str, Any]], None]:
        return lambda event: self.owner.emit({**event, **self.context})

    def _media_slot(self):
        """A slot of the process-wide ffmpeg-class limit; says so once when it has to wait."""
        owner = self.owner
        return owner.ctx.gates.hold(MEDIA, should_cancel=owner.should_cancel, on_wait=lambda: owner.emit({"stage": "audio_waiting", **self.context}))

    def _ensure_mp4(self, item: Any, paths: _ItemPaths) -> str | None:
        owner = self.owner
        with self._media_slot():
            video_result = postprocess_media.ensure_playable_mp4(
                paths.video,
                overwrite=owner.request.overwrite_artifacts,
                on_progress=self._with_context(),
                should_cancel=owner.should_cancel,
            )
        owner.add_artifact({"kind": "mp4", **video_result.model_dump(mode="json"), "file_exists": getattr(item, "file_exists", False), "skipped": getattr(item, "skipped", False)})
        return None if video_result.ok else (video_result.error_code or "video_reconstruction_failed")

    def _ensure_audio(self, paths: _ItemPaths, video_exists: bool) -> tuple[str | None, Path]:
        owner = self.owner
        request = owner.request
        if paths.audio.exists() and not request.overwrite_artifacts:
            size = paths.audio.stat().st_size
            owner.add_artifact({"kind": request.audio_format, "ok": True, "input_path": str(paths.video), "output_path": str(paths.audio), "file_size": size, "file_exists": True, "skipped": True})
            owner.emit({"stage": "audio_skipped", **self.context, "output_path": str(paths.audio), "file_size": size, "progress_percent": 100})
            return None, paths.audio
        if not video_exists:
            return "source_video_not_found", paths.audio
        with self._media_slot():
            owner.emit({"stage": "extracting_audio", **self.context, "output_path": str(paths.video)})
            audio_request = postprocess_media.AudioExtractionRequest(input_path=paths.video, output_dir=paths.video.parent, format=request.audio_format, overwrite=request.overwrite_artifacts)
            audio_result = postprocess_media.extract_audio(audio_request, on_progress=self._with_context(), should_cancel=owner.should_cancel)
        owner.add_artifact({"kind": request.audio_format, **audio_result.model_dump(mode="json")})
        if not audio_result.ok:
            return audio_result.error_code or "audio_extraction_failed", paths.audio
        audio_path = audio_result.output_path or paths.audio
        owner.emit({"stage": "audio_done", **self.context, "output_path": str(audio_path), "file_size": audio_result.file_size, "progress_percent": 100})
        return None, audio_path

    def _ensure_transcript(self, paths: _ItemPaths, audio_path: Path) -> str | None:
        owner = self.owner
        request = owner.request
        if paths.transcript_exists and not request.overwrite_artifacts:
            owner.add_artifact({"kind": "txt", "ok": True, "audio_path": str(audio_path), "text_path": str(paths.text), "md_path": str(paths.md), "json_path": str(paths.json), "srt_path": str(paths.srt) if paths.srt.exists() else None, "segments": [], "file_exists": True, "skipped": True})
            owner.emit({"stage": "transcript_skipped", **self.context, "output_path": str(paths.text), "progress_percent": 100})
            return None
        if not audio_path.exists():
            return "audio_not_found"
        use_remote = request.stt_provider != "fake" and owner.ctx.remote_stt.is_enabled()
        provider = "remote" if use_remote else request.stt_provider
        stt_request = stt.TranscriptionRequest(audio_path=audio_path, output_dir=paths.video.parent, provider=request.stt_provider, language=request.stt_language, model=request.stt_model, overwrite=request.overwrite_artifacts)

        def transcribe(**options: Any) -> stt.TranscriptionResult:
            owner.emit({"stage": "transcribing", **self.context, "output_path": str(audio_path), "provider": provider, "model": request.stt_model, "remote": use_remote})
            return stt.transcribe_audio(stt_request, on_progress=self._with_context(), should_cancel=owner.should_cancel, stt_capability=owner.ctx.platform, **options)

        if use_remote:
            # Prefer a configured remote server over the local engine. The app does not queue remote requests
            # (the server does); only the WAV conversion inside shares the ffmpeg limit.
            stt_result = transcribe(remote=owner.ctx.remote_stt, gates=owner.ctx.gates)
        else:
            gate = owner.ctx.gates.hold(
                LOCAL_STT,
                should_cancel=owner.should_cancel,
                on_wait=lambda: owner.emit({"stage": "transcript_waiting", **self.context, "reason": "local_engine", "audio_path": str(audio_path)}),
            )
            with gate:
                stt_result = transcribe()
        owner.add_artifact({"kind": "txt", **stt_result.model_dump(mode="json")})
        if not stt_result.ok:
            return stt_result.error_code or "stt_failed"
        owner.emit({"stage": "transcript_done", **self.context, "output_path": str(stt_result.text_path) if stt_result.text_path else None, "progress_percent": 100})
        return None


class ArtifactPipeline:
    def __init__(
        self,
        ctx: AppContext,
        job: Job,
        request: ArtifactDownloadRequest,
        outputs: list[str],
        credentials: tuple[str, str],
        output_dir: Path,
    ) -> None:
        self.ctx = ctx
        self.job = job
        self.request = request
        self.outputs = outputs
        self.credentials = credentials
        self.output_dir = output_dir
        self.conflict_strategy = "overwrite" if request.conflict_strategy == "overwrite" else "skip"
        self.wants_audio = "mp3" in outputs or "txt" in outputs
        self.artifacts: list[dict[str, Any]] = []
        self._artifacts_lock = threading.Lock()
        self._emit_lock = threading.Lock()
        self._hand_off_lock = threading.Lock()
        # Set only when run() itself exits abnormally, so no chain outlives the job. It is never set by a
        # lecture's failure: lectures do not stop each other.
        self._teardown = threading.Event()
        self._lectures: dict[str, LecturePipeline] = {}
        self._executor: ThreadPoolExecutor | None = None

    # ------------------------------------------------------------------ helpers

    def should_cancel(self) -> bool:
        return self.job.should_cancel() or self._teardown.is_set()

    def emit(self, event: dict[str, Any]) -> None:
        """Append a progress event stamped with `at` (seconds since the epoch); stamping and appending are ordered together."""
        with self._emit_lock:
            self.job.append_progress({**event, "at": event.get("at", time.time())})

    def on_progress(self, event: dict[str, Any]) -> None:
        self.emit(event)

    def add_artifact(self, artifact: dict[str, Any]) -> None:
        with self._artifacts_lock:
            self.artifacts.append(artifact)

    def _batch(self, *, dry_run: bool, module_item_ids: list[int | str] | None, include_completed: bool, **extra: Any):
        request = self.request
        return self.ctx.service.batch_download_lectures(
            *self.credentials,
            course_id=request.course_id,
            course_name=request.course_name,
            output_dir=self.output_dir,
            dry_run=dry_run,
            module_item_ids=module_item_ids,
            include_completed=include_completed,
            on_progress=self.on_progress,
            should_cancel=self.job.should_cancel,
            conflict_strategy=self.conflict_strategy,
            max_parallel_downloads=request.max_parallel_downloads,
            **extra,
        )

    # --------------------------------------------------------------------- run

    def run(self) -> JobOutcome:
        request = self.request
        plan_result = self._batch(
            dry_run=True,
            module_item_ids=request.module_item_ids if request.mode == "selected" else None,
            include_completed=request.mode == "all",
        )
        plan_items = list(plan_result.items)
        if not plan_result.ok and not any(getattr(item, "output_path", None) for item in plan_items):
            # Nothing can be processed: login / plan level failure, the job itself fails.
            codes = {getattr(item, "error_code", None) for item in plan_items}
            common = next(iter(codes)) if len(codes) == 1 else None
            return JobOutcome.failed(common or plan_result.error_code or "download_plan_failed")

        total = len(plan_items)
        lectures = [
            LecturePipeline(self, index, total, item)
            for index, item in enumerate(plan_items, start=1)
            if getattr(item, "output_path", None) or not getattr(item, "ok", True)
        ]
        self._lectures = {str(lecture.module_item_id): lecture for lecture in lectures}
        needs_video_ids = [lecture.module_item_id for lecture in lectures if lecture.item.output_path and self._needs_video(lecture.item)]
        awaiting = {str(module_item_id) for module_item_id in needs_video_ids}
        processable = [lecture for lecture in lectures if getattr(lecture.item, "output_path", None)]

        self._executor = ThreadPoolExecutor(max_workers=max(1, len(lectures)), thread_name_prefix="ssu-artifact")
        try:
            self.ctx.remember_outputs(*(lecture.item.output_path for lecture in processable))
            for lecture in lectures:
                self.emit({"stage": "artifact_item_queued", **lecture.context})
            if not needs_video_ids:
                self.emit({"stage": "artifact_downloads_skipped", "total": len(processable)})
            if self.wants_audio:
                self.emit({"stage": "artifact_audio_started", "total": len(processable), "max_parallel_downloads": request.max_parallel_downloads})
            if "txt" in self.outputs:
                self.emit({"stage": "artifact_transcripts_queued", "total": len(processable), "max_parallel_downloads": request.max_parallel_downloads})

            # Lectures that need no download (or already failed to plan) start their chain right away.
            for lecture in lectures:
                if str(lecture.module_item_id) in awaiting:
                    continue
                if not getattr(lecture.item, "output_path", None):
                    self._hand_off(lecture, failure=(getattr(lecture.item, "error_code", None) or "download_failed", "download"))
                else:
                    self._hand_off(lecture)

            download_result = plan_result
            if needs_video_ids:
                download_result = self._download(plan_result, needs_video_ids, awaiting)
            for lecture in lectures:  # defensive: every lecture must have been handed off by now
                self._hand_off(lecture, failure=("download_failed", "download"))
        except BaseException:
            self._teardown.set()
            raise
        finally:
            self._executor.shutdown(wait=True)

        return self._finish(lectures, download_result)

    def _download(self, plan_result: Any, needs_video_ids: list[Any], awaiting: set[str]) -> Any:
        """Download the videos some requested output still needs; each lecture's chain starts as its own download ends."""
        download_result = plan_result
        failure_code: str | None = None
        by_id: dict[str, Any] = {}
        try:
            batch_result = self._batch(dry_run=False, module_item_ids=needs_video_ids, include_completed=True, on_item_ready=self._on_item_ready)
        except Exception as exc:  # noqa: BLE001 - lectures not handed off yet end with this error, the rest keep running
            failure_code = CANCELLED if self.job.should_cancel() else self.ctx.job_error_code(exc)
        else:
            download_result = batch_result
            by_id = {str(item.module_item_id): item for item in batch_result.items}
            if not batch_result.ok and not batch_result.items:
                failure_code = batch_result.error_code or "download_failed"
        for key in awaiting:
            lecture = self._lectures[key]
            item = by_id.get(key)
            if item is not None:
                self._hand_off_item(lecture, item)
            elif self.job.should_cancel():
                self._hand_off(lecture, failure=(CANCELLED, "download"))
            else:
                self._hand_off(lecture, failure=(failure_code or "download_failed", "download"))
        return download_result

    def _on_item_ready(self, item: Any) -> None:
        """Called by the download batch for each settled item, possibly from a worker thread: only queues the chain."""
        lecture = self._lectures.get(str(getattr(item, "module_item_id", None)))
        if lecture is not None:
            self._hand_off_item(lecture, item)

    def _hand_off_item(self, lecture: LecturePipeline, item: Any) -> None:
        if not getattr(item, "ok", True):
            self._hand_off(lecture, failure=(getattr(item, "error_code", None) or "download_failed", "download"))
            return
        if getattr(item, "output_path", None):
            self.ctx.remember_outputs(item.output_path)
            self._hand_off(lecture, item=item)
        else:
            self._hand_off(lecture)

    def _hand_off(self, lecture: LecturePipeline, *, item: Any | None = None, failure: tuple[str, str] | None = None) -> None:
        with self._hand_off_lock:
            if lecture.submitted:
                return
            lecture.submitted = True
        if item is not None:
            lecture.item = item
        lecture.preset_failure = failure
        assert self._executor is not None
        self._executor.submit(lecture.run)

    # ----------------------------------------------------------------- outcome

    def _finish(self, lectures: list[LecturePipeline], download_result: Any) -> JobOutcome:
        results = [lecture.result or LectureResult(lecture.module_item_id, "failed", error_code="pipeline_incomplete", failed_stage=lecture.stage) for lecture in lectures]
        summary = {
            "total": len(results),
            "succeeded": sum(result.state == "done" for result in results),
            "failed": sum(result.state == "failed" for result in results),
            "cancelled": sum(result.state == "cancelled" for result in results),
        }
        progress = self.job.progress_events()
        payload = artifact_payload(batch_payload(download_result, progress), self.artifacts, progress)
        # Per-lecture terminal state outlives the 400-event progress cap.
        payload["summary"] = summary
        payload["items_status"] = [result.status_entry() for result in results]
        if self.job.should_cancel():
            return JobOutcome.failed(CANCELLED, result=payload)  # the runner turns this into a cancelled job
        if summary["succeeded"] == summary["total"]:
            return JobOutcome.succeeded(payload)
        codes = {result.error_code for result in results if result.state != "done"}
        if summary["succeeded"] == 0 and len(codes) == 1 and None not in codes:
            return JobOutcome.failed(next(iter(codes)), result=payload)
        return JobOutcome.failed("partial_failure", result=payload)

    def _needs_video(self, item: Any) -> bool:
        """Does any requested artifact still require the source video bytes?"""
        request = self.request
        paths = _ItemPaths.for_video(Path(str(item.output_path)), request.audio_format)
        overwrite = request.overwrite_artifacts
        video_exists = paths.video.exists()
        needs_mp4_file = "mp4" in self.outputs and (overwrite or not video_exists)
        audio_satisfied = paths.audio.exists() and not overwrite
        needs_audio_source = self.wants_audio and not audio_satisfied and not video_exists
        transcript_satisfied = paths.transcript_exists and not overwrite
        needs_transcript_source = "txt" in self.outputs and not transcript_satisfied and not paths.audio.exists() and not video_exists
        return needs_mp4_file or needs_audio_source or needs_transcript_source
