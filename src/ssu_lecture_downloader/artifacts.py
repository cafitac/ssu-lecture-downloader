"""MP4 / MP3 / TXT artifact pipeline behind `POST /api/download/artifacts/jobs`.

Phases: plan the course (dry-run) -> download only the videos that some requested
artifact still needs -> per item build mp4 / audio / transcript from whatever already
exists on disk. Existing complete outputs are reused; outputs are only considered
complete when they were swapped in atomically (see postprocess_media / stt).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ssu_lecture_downloader import postprocess_media, stt
from ssu_lecture_downloader.api_models import ArtifactDownloadRequest
from ssu_lecture_downloader.app_context import AppContext
from ssu_lecture_downloader.jobs import Job, JobOutcome
from ssu_lecture_downloader.payloads import artifact_payload, batch_payload

MAX_ARTIFACT_POSTPROCESS_WORKERS = 1


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
        # Set when one item failed so a running sibling stops instead of finishing pointlessly.
        self._abort = threading.Event()
        self._first_error: str | None = None

    # ------------------------------------------------------------------ helpers

    def should_cancel(self) -> bool:
        return self.job.should_cancel() or self._abort.is_set()

    def on_progress(self, event: dict[str, Any]) -> None:
        self.job.append_progress(event)

    def _add_artifact(self, artifact: dict[str, Any]) -> None:
        with self._artifacts_lock:
            self.artifacts.append(artifact)

    def _batch(self, *, dry_run: bool, module_item_ids: list[int | str] | None, include_completed: bool):
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
        )

    # --------------------------------------------------------------------- run

    def run(self) -> JobOutcome:
        request = self.request
        plan_result = self._batch(
            dry_run=True,
            module_item_ids=request.module_item_ids if request.mode == "selected" else None,
            include_completed=request.mode == "all",
        )
        if not plan_result.ok:
            return JobOutcome.failed(plan_result.error_code or "download_plan_failed")

        planned_items = [item for item in plan_result.items if getattr(item, "output_path", None)]
        needs_video_ids = [item.module_item_id for item in planned_items if self._needs_video(item)]

        downloaded_by_id: dict[str, Any] = {}
        if needs_video_ids:
            batch_result = self._batch(dry_run=False, module_item_ids=needs_video_ids, include_completed=True)
            if not batch_result.ok:
                return JobOutcome.failed(batch_result.error_code or "download_failed")
            downloaded_by_id = {str(item.module_item_id): item for item in batch_result.items}
            download_result = batch_result
        else:
            download_result = plan_result
            self.on_progress({"stage": "artifact_downloads_skipped", "total": len(planned_items)})

        downloaded_items = [downloaded_by_id.get(str(item.module_item_id), item) for item in planned_items]
        processable = [(index, item) for index, item in enumerate(downloaded_items, start=1) if getattr(item, "output_path", None)]
        self.ctx.remember_outputs(*(getattr(item, "output_path", None) for _, item in processable))
        if self.wants_audio:
            self.on_progress({"stage": "artifact_audio_started", "total": len(processable), "max_parallel_downloads": request.max_parallel_downloads})
        if "txt" in self.outputs:
            self.on_progress({"stage": "artifact_transcripts_queued", "total": len(processable), "max_parallel_downloads": request.max_parallel_downloads})

        error_code = self._process_all(processable, total=len(downloaded_items))
        progress = self.job.progress_events()
        result = artifact_payload(batch_payload(download_result, progress), self.artifacts, progress)
        if error_code:
            return JobOutcome.failed(error_code, result=result)
        return JobOutcome.succeeded(result)

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

    def _process_all(self, processable: list[tuple[int, Any]], *, total: int) -> str | None:
        if not processable:
            return None
        request = self.request
        max_workers = max(1, min(request.max_parallel_downloads, MAX_ARTIFACT_POSTPROCESS_WORKERS, len(processable)))
        executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ssu-artifact")
        try:
            futures = [executor.submit(self._guarded_item, index, item, total) for index, item in processable]
            for future in as_completed(futures):
                future.result()
            return self._first_error
        finally:
            # A failure (or an exception) must not leave queued items running behind a finished job.
            self._abort.set()
            executor.shutdown(wait=True, cancel_futures=True)

    def _guarded_item(self, index: int, item: Any, total: int) -> None:
        """Run one item; the first failure stops every sibling immediately, from the worker thread."""
        try:
            error_code = self._process_item(index, item, total)
        except BaseException:
            self._abort.set()
            raise
        if error_code:
            with self._artifacts_lock:
                if self._first_error is None:
                    self._first_error = error_code
            self._abort.set()

    # -------------------------------------------------------------- per item

    def _process_item(self, index: int, item: Any, total: int) -> str | None:
        if self.should_cancel():
            return "cancelled"
        output_path = getattr(item, "output_path", None)
        if not output_path:
            return None
        paths = _ItemPaths.for_video(Path(output_path), self.request.audio_format)
        context = {"index": index, "total": total, "module_item_id": getattr(item, "module_item_id", None), "title": getattr(item, "title", None)}
        video_exists = paths.video.exists()
        outputs = self.outputs

        error_code: str | None = None
        if "mp4" in outputs:
            error_code = self._ensure_mp4(item, paths, context) if video_exists else "video_not_found"
        audio_path = paths.audio
        if error_code is None and self.wants_audio:
            error_code, audio_path = self._ensure_audio(paths, context, video_exists)
        if error_code is None and "txt" in outputs:
            error_code = self._ensure_transcript(paths, audio_path, context)
        if error_code is not None:
            return error_code
        if "mp4" not in outputs and video_exists and bool(getattr(item, "written", False)):
            paths.video.unlink(missing_ok=True)
            self.on_progress({"stage": "source_video_removed", **context, "output_path": str(paths.video)})
        return None

    def _with_context(self, context: dict[str, Any]) -> Callable[[dict[str, Any]], None]:
        return lambda event: self.on_progress({**event, **context})

    def _ensure_mp4(self, item: Any, paths: _ItemPaths, context: dict[str, Any]) -> str | None:
        video_result = postprocess_media.ensure_playable_mp4(
            paths.video,
            overwrite=self.request.overwrite_artifacts,
            on_progress=self._with_context(context),
            should_cancel=self.should_cancel,
        )
        self._add_artifact({"kind": "mp4", **video_result.model_dump(mode="json"), "file_exists": getattr(item, "file_exists", False), "skipped": getattr(item, "skipped", False)})
        return None if video_result.ok else (video_result.error_code or "video_reconstruction_failed")

    def _ensure_audio(self, paths: _ItemPaths, context: dict[str, Any], video_exists: bool) -> tuple[str | None, Path]:
        request = self.request
        if paths.audio.exists() and not request.overwrite_artifacts:
            size = paths.audio.stat().st_size
            self._add_artifact({"kind": request.audio_format, "ok": True, "input_path": str(paths.video), "output_path": str(paths.audio), "file_size": size, "file_exists": True, "skipped": True})
            self.on_progress({"stage": "audio_skipped", **context, "output_path": str(paths.audio), "file_size": size, "progress_percent": 100})
            return None, paths.audio
        if not video_exists:
            return "source_video_not_found", paths.audio
        self.on_progress({"stage": "extracting_audio", **context, "output_path": str(paths.video)})
        audio_request = postprocess_media.AudioExtractionRequest(input_path=paths.video, output_dir=paths.video.parent, format=request.audio_format, overwrite=request.overwrite_artifacts)
        audio_result = postprocess_media.extract_audio(audio_request, on_progress=self._with_context(context), should_cancel=self.should_cancel)
        self._add_artifact({"kind": request.audio_format, **audio_result.model_dump(mode="json")})
        if not audio_result.ok:
            return audio_result.error_code or "audio_extraction_failed", paths.audio
        audio_path = audio_result.output_path or paths.audio
        self.on_progress({"stage": "audio_done", **context, "output_path": str(audio_path), "file_size": audio_result.file_size, "progress_percent": 100})
        return None, audio_path

    def _ensure_transcript(self, paths: _ItemPaths, audio_path: Path, context: dict[str, Any]) -> str | None:
        request = self.request
        if paths.transcript_exists and not request.overwrite_artifacts:
            self._add_artifact({"kind": "txt", "ok": True, "audio_path": str(audio_path), "text_path": str(paths.text), "md_path": str(paths.md), "json_path": str(paths.json), "srt_path": str(paths.srt) if paths.srt.exists() else None, "segments": [], "file_exists": True, "skipped": True})
            self.on_progress({"stage": "transcript_skipped", **context, "output_path": str(paths.text), "progress_percent": 100})
            return None
        if not audio_path.exists():
            return "audio_not_found"
        use_remote = request.stt_provider != "fake" and self.ctx.remote_stt.is_enabled()
        provider = "remote" if use_remote else request.stt_provider
        self.on_progress({"stage": "transcribing", **context, "output_path": str(audio_path), "provider": provider, "model": request.stt_model, "remote": use_remote})
        stt_request = stt.TranscriptionRequest(audio_path=audio_path, output_dir=paths.video.parent, provider=request.stt_provider, language=request.stt_language, model=request.stt_model, overwrite=request.overwrite_artifacts)
        # Prefer a configured remote server over the local engine; the local path keeps its original call.
        options: dict[str, Any] = {"remote": self.ctx.remote_stt} if use_remote else {}
        stt_result = stt.transcribe_audio(stt_request, on_progress=self._with_context(context), should_cancel=self.should_cancel, **options)
        self._add_artifact({"kind": "txt", **stt_result.model_dump(mode="json")})
        if not stt_result.ok:
            return stt_result.error_code or "stt_failed"
        self.on_progress({"stage": "transcript_done", **context, "output_path": str(stt_result.text_path) if stt_result.text_path else None, "progress_percent": 100})
        return None
