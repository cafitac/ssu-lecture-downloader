"""Media post-processing routes: playable-mp4 repair, audio extraction, STT, dependencies."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ssu_lecture_downloader import dependency_installer, postprocess_media, stt
from ssu_lecture_downloader.api_models import MediaDependencyInstallRequest, VideoEnsurePlayableRequest
from ssu_lecture_downloader.app_context import AppContext, job_or_404
from ssu_lecture_downloader.jobs import Job, JobOutcome, ProgressCollector
from ssu_lecture_downloader.postprocess_media import AudioExtractionRequest
from ssu_lecture_downloader.stt import TranscriptionRequest

# Quick jobs report their final state in the start response when they finish this fast.
QUICK_JOB_WAIT_SECONDS = 0.2


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    # ------------------------------------------------------- playable mp4

    @router.post("/api/media/video/ensure-playable")
    def ensure_playable_video(request: VideoEnsurePlayableRequest) -> dict[str, Any]:
        if not ctx.media_paths.is_allowed(request.input_path):
            raise HTTPException(
                status_code=400,
                detail={
                    "ok": False,
                    "error_code": "media_path_not_allowed",
                    "message": "다운로드 폴더 안의 .mp4 파일만 처리할 수 있습니다.",
                },
            )
        progress = ProgressCollector()
        try:
            result = postprocess_media.ensure_playable_mp4(request.input_path, overwrite=request.overwrite, on_progress=progress)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail={"ok": False, "error_code": "video_reconstruction_failed", "message": str(exc)},
            ) from exc
        payload = result.model_dump(mode="json")
        payload["progress"] = progress.events()
        if not result.ok:
            raise HTTPException(status_code=422, detail=payload)
        return payload

    # ------------------------------------------------------------ audio

    @router.post("/api/media/audio/jobs")
    def start_audio_extraction_job(request: AudioExtractionRequest) -> dict[str, Any]:
        def work(job: Job) -> JobOutcome:
            job.append_progress({"stage": "extracting_audio", "input_path": str(request.input_path)})
            result = postprocess_media.extract_audio(request, on_progress=job.append_progress, should_cancel=job.should_cancel)
            payload = result.model_dump(mode="json")
            if not result.ok:
                return JobOutcome.failed(result.error_code or "audio_extraction_failed", result=payload)
            job.append_progress({"stage": "audio_done", "output_path": str(result.output_path) if result.output_path else None, "file_size": result.file_size})
            return JobOutcome.succeeded(payload)

        return ctx.runner.start(ctx.audio_jobs, work, wait_seconds=QUICK_JOB_WAIT_SECONDS).snapshot()

    @router.get("/api/media/audio/jobs/{job_id}")
    def audio_extraction_job_status(job_id: str) -> dict[str, Any]:
        return job_or_404(ctx.audio_jobs, job_id, "audio_job_not_found").snapshot()

    @router.post("/api/media/audio/jobs/{job_id}/cancel")
    def cancel_audio_extraction_job(job_id: str) -> dict[str, Any]:
        job = job_or_404(ctx.audio_jobs, job_id, "audio_job_not_found")
        job.mark_cancel_requested()
        return job.snapshot()

    # -------------------------------------------------------------- STT

    def stt_provider_status_payload() -> list[dict[str, object]]:
        providers = []
        for provider in ["faster_whisper", "whisper_cpp"]:
            status = stt.check_stt_provider(provider)
            providers.append(status if isinstance(status, dict) else status.model_dump())
        return providers

    @router.get("/api/stt/providers/status")
    def stt_provider_status() -> dict[str, object]:
        return {"ok": True, "providers": stt_provider_status_payload()}

    @router.post("/api/stt/jobs")
    def start_stt_job(request: TranscriptionRequest) -> dict[str, Any]:
        def work(job: Job) -> JobOutcome:
            job.append_progress({"stage": "transcribing", "audio_path": str(request.audio_path), "provider": request.provider, "model": request.model})
            result = stt.transcribe_audio(request, on_progress=job.append_progress, should_cancel=job.should_cancel)
            payload = result.model_dump(mode="json")
            if not result.ok:
                return JobOutcome.failed(result.error_code or "stt_failed", result=payload)
            job.append_progress({"stage": "transcript_done", "text_path": str(result.text_path) if result.text_path else None})
            return JobOutcome.succeeded(payload)

        return ctx.runner.start(ctx.stt_jobs, work, wait_seconds=QUICK_JOB_WAIT_SECONDS).snapshot()

    @router.get("/api/stt/jobs/{job_id}")
    def stt_job_status(job_id: str) -> dict[str, Any]:
        return job_or_404(ctx.stt_jobs, job_id, "stt_job_not_found").snapshot()

    @router.post("/api/stt/jobs/{job_id}/cancel")
    def cancel_stt_job(job_id: str) -> dict[str, Any]:
        job = job_or_404(ctx.stt_jobs, job_id, "stt_job_not_found")
        job.mark_cancel_requested()
        return job.snapshot()

    # ----------------------------------------------------- dependencies

    @router.get("/api/media/dependencies/status")
    def media_dependency_status() -> dict[str, object]:
        auto_install_available, auto_install_unavailable_reason = dependency_installer.install_supported()
        return {
            "ok": True,
            "packaging_mode": "app_bundled",
            "auto_install_available": auto_install_available,
            "auto_install_unavailable_reason": auto_install_unavailable_reason,
            "dependencies": [postprocess_media.ffmpeg_status()],
            "providers": stt_provider_status_payload(),
        }

    @router.post("/api/media/dependencies/install/jobs")
    def start_media_dependency_install_job(request: MediaDependencyInstallRequest) -> dict[str, Any]:
        try:
            components = dependency_installer.normalize_components(request.components)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"ok": False, "error_code": str(exc).split(":", 1)[0]}) from exc

        def work(job: Job) -> JobOutcome:
            job.append_progress({"stage": "dependency_install_checking", "components": components})
            result = dependency_installer.install_missing_media_components(components, on_progress=job.append_progress, should_cancel=job.should_cancel)
            if not result.get("ok"):
                return JobOutcome.failed(str(result.get("error_code") or "dependency_install_failed"), result=result)
            return JobOutcome.succeeded(result)

        return ctx.runner.start(ctx.dependency_jobs, work, wait_seconds=QUICK_JOB_WAIT_SECONDS).snapshot()

    @router.get("/api/media/dependencies/install/jobs/{job_id}")
    def media_dependency_install_job_status(job_id: str) -> dict[str, Any]:
        return job_or_404(ctx.dependency_jobs, job_id, "dependency_install_job_not_found").snapshot()

    @router.post("/api/media/dependencies/install/jobs/{job_id}/cancel")
    def cancel_media_dependency_install_job(job_id: str) -> dict[str, Any]:
        job = job_or_404(ctx.dependency_jobs, job_id, "dependency_install_job_not_found")
        job.mark_cancel_requested()
        return job.snapshot()

    return router
