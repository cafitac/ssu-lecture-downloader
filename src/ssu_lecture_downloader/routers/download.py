"""Lecture download routes: plan, native plan, direct download, background jobs, batches, artifacts."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ssu_lecture_downloader.api_errors import raise_for_result_error
from ssu_lecture_downloader.api_models import (
    ArtifactDownloadRequest,
    BatchDownloadRequest,
    DownloadPlanRequest,
    DownloadRequest,
    batch_payload,
    download_payload,
    native_download_payload,
    validate_artifact_request,
    validate_batch_request,
)
from ssu_lecture_downloader.app_context import AppContext, job_or_404
from ssu_lecture_downloader.artifacts import ArtifactPipeline
from ssu_lecture_downloader.jobs import Job, JobOutcome, JobRegistry, ProgressCollector
from ssu_lecture_downloader.routers.credentials import call_service


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    service = ctx.service

    def start_job(registry: JobRegistry, work) -> dict[str, Any]:
        return ctx.runner.start(registry, work).snapshot()

    # ------------------------------------------------------------ single item

    @router.post("/api/download/plan")
    def download_plan(request: DownloadPlanRequest) -> dict[str, object]:
        username, password = ctx.require_credentials()
        output_dir = ctx.output_dir_for(request.output_dir)
        result = call_service(
            ctx,
            lambda: service.download_lecture(
                username,
                password,
                course_id=request.course_id,
                module_item_id=request.module_item_id,
                course_name=request.course_name,
                output_dir=output_dir,
                dry_run=True,
                conflict_strategy=request.conflict_strategy,
            ),
        )
        if not result.ok:
            raise_for_result_error(result.error_code)
        return download_payload(result, [])

    @router.post("/api/download/native-plan")
    def native_download_plan(request: DownloadPlanRequest) -> dict[str, object]:
        username, password = ctx.require_credentials()
        output_dir = ctx.output_dir_for(request.output_dir)
        result = call_service(
            ctx,
            lambda: service.native_download_plan(
                username,
                password,
                course_id=request.course_id,
                module_item_id=request.module_item_id,
                course_name=request.course_name,
                output_dir=output_dir,
                conflict_strategy=request.conflict_strategy,
            ),
        )
        download_result = result["result"] if isinstance(result, dict) else result.result
        if not download_result.ok:
            raise_for_result_error(download_result.error_code)
        ctx.remember_outputs(download_result.output_path)
        return native_download_payload(result)

    @router.post("/api/download")
    def download(request: DownloadRequest) -> dict[str, object]:
        username, password = ctx.require_credentials()
        output_dir = ctx.output_dir_for(request.output_dir)
        progress = ProgressCollector()
        result = call_service(
            ctx,
            lambda: service.download_lecture(
                username,
                password,
                course_id=request.course_id,
                module_item_id=request.module_item_id,
                course_name=request.course_name,
                output_dir=output_dir,
                dry_run=False,
                on_progress=progress,
                conflict_strategy=request.conflict_strategy,
            ),
        )
        if not result.ok:
            raise_for_result_error(result.error_code)
        ctx.remember_outputs(result.output_path)
        return download_payload(result, progress.events())

    @router.post("/api/download/jobs")
    def start_download_job(request: DownloadRequest) -> dict[str, Any]:
        username, password = ctx.require_credentials()
        output_dir = ctx.output_dir_for(request.output_dir)

        def work(job: Job) -> JobOutcome:
            result = service.download_lecture(
                username,
                password,
                course_id=request.course_id,
                module_item_id=request.module_item_id,
                course_name=request.course_name,
                output_dir=output_dir,
                dry_run=False,
                on_progress=job.append_progress,
                should_cancel=job.should_cancel,
                conflict_strategy=request.conflict_strategy,
            )
            if not result.ok:
                return JobOutcome.failed(result.error_code or "download_failed")
            ctx.remember_outputs(result.output_path)
            return JobOutcome.succeeded(download_payload(result, job.progress_events()))

        return start_job(ctx.download_jobs, work)

    @router.get("/api/download/jobs/{job_id}")
    def download_job_status(job_id: str) -> dict[str, Any]:
        return job_or_404(ctx.download_jobs, job_id, "download_job_not_found").snapshot()

    @router.post("/api/download/jobs/{job_id}/cancel")
    def cancel_download_job(job_id: str) -> dict[str, Any]:
        job = job_or_404(ctx.download_jobs, job_id, "download_job_not_found")
        job.mark_cancel_requested()
        return job.snapshot()

    # ------------------------------------------------------------------ batch

    def run_batch_kwargs(request: BatchDownloadRequest, **extra: Any) -> dict[str, Any]:
        return {
            "course_id": request.course_id,
            "course_name": request.course_name,
            "output_dir": ctx.output_dir_for(request.output_dir),
            "module_item_ids": request.module_item_ids if request.mode == "selected" else None,
            "include_completed": request.mode == "all",
            "conflict_strategy": request.conflict_strategy,
            "max_parallel_downloads": request.max_parallel_downloads,
            **extra,
        }

    def run_batch(request: BatchDownloadRequest, *, dry_run: bool) -> dict[str, Any]:
        username, password = ctx.require_credentials()
        validate_batch_request(request)
        progress = ProgressCollector()
        kwargs = run_batch_kwargs(request, dry_run=dry_run, on_progress=progress)
        result = call_service(ctx, lambda: service.batch_download_lectures(username, password, **kwargs))
        if not result.ok:
            raise_for_result_error(result.error_code)
        ctx.remember_outputs(*(item.output_path for item in result.items))
        return batch_payload(result, progress.events())

    @router.post("/api/download/batch/jobs")
    def start_batch_download_job(request: BatchDownloadRequest) -> dict[str, Any]:
        username, password = ctx.require_credentials()
        validate_batch_request(request)
        kwargs = run_batch_kwargs(request, dry_run=False)

        def work(job: Job) -> JobOutcome:
            result = service.batch_download_lectures(
                username,
                password,
                **kwargs,
                on_progress=job.append_progress,
                should_cancel=job.should_cancel,
            )
            if not result.ok:
                return JobOutcome.failed(result.error_code or "download_failed")
            ctx.remember_outputs(*(item.output_path for item in result.items))
            return JobOutcome.succeeded(batch_payload(result, job.progress_events()))

        return start_job(ctx.download_jobs, work)

    @router.post("/api/download/batch/plan")
    def batch_download_plan(request: BatchDownloadRequest) -> dict[str, Any]:
        return run_batch(request, dry_run=True)

    @router.post("/api/download/batch")
    def batch_download(request: BatchDownloadRequest) -> dict[str, Any]:
        return run_batch(request, dry_run=False)

    # -------------------------------------------------------------- artifacts

    @router.post("/api/download/artifacts/jobs")
    def start_artifact_download_job(request: ArtifactDownloadRequest) -> dict[str, Any]:
        credentials = ctx.require_credentials()
        outputs = validate_artifact_request(request)
        output_dir = ctx.output_dir_for(request.output_dir)

        def work(job: Job) -> JobOutcome:
            return ArtifactPipeline(ctx, job, request, outputs, credentials, output_dir).run()

        return start_job(ctx.artifact_jobs, work)

    @router.get("/api/download/artifacts/jobs/{job_id}")
    def artifact_download_job_status(job_id: str) -> dict[str, Any]:
        return job_or_404(ctx.artifact_jobs, job_id, "artifact_job_not_found").snapshot()

    @router.post("/api/download/artifacts/jobs/{job_id}/cancel")
    def cancel_artifact_download_job(job_id: str) -> dict[str, Any]:
        job = job_or_404(ctx.artifact_jobs, job_id, "artifact_job_not_found")
        job.mark_cancel_requested()
        return job.snapshot()

    return router
