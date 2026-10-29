"""Lecture download routes: plan, native plan, direct download, background jobs, batches, artifacts."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ssu_lecture_downloader.service.api_errors import raise_for_result_error
from ssu_lecture_downloader.service.api_models import (
    ArtifactDownloadRequest,
    BatchDownloadRequest,
    DownloadPlanRequest,
    DownloadRequest,
    validate_artifact_request,
    validate_batch_request,
)
from ssu_lecture_downloader.service.app_context import AppContext, add_job_routes
from ssu_lecture_downloader.service.artifacts import ArtifactPipeline
from ssu_lecture_downloader.core.jobs import Job, JobOutcome, ProgressCollector
from ssu_lecture_downloader.service.payloads import (
    batch_payload,
    download_payload,
    native_download_payload,
    unpack_native_plan,
)


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    service = ctx.service

    # ------------------------------------------------------------ single item

    def item_kwargs(request: DownloadPlanRequest, **extra: Any) -> dict[str, Any]:
        return {
            "course_id": request.course_id,
            "module_item_id": request.module_item_id,
            "course_name": request.course_name,
            "output_dir": ctx.output_dir_for(request.output_dir),
            "conflict_strategy": request.conflict_strategy,
            **extra,
        }

    @router.post("/api/download/plan")
    def download_plan(request: DownloadPlanRequest) -> dict[str, object]:
        username, password = ctx.require_credentials()
        kwargs = item_kwargs(request, dry_run=True)
        result = ctx.call_service(lambda: service.download_lecture(username, password, **kwargs))
        if not result.ok:
            raise_for_result_error(result.error_code)
        return download_payload(result, [])

    @router.post("/api/download/native-plan")
    def native_download_plan(request: DownloadPlanRequest) -> dict[str, object]:
        username, password = ctx.require_credentials()
        kwargs = item_kwargs(request)
        result = ctx.call_service(lambda: service.native_download_plan(username, password, **kwargs))
        download_result, _ = unpack_native_plan(result)
        if not download_result.ok:
            raise_for_result_error(download_result.error_code)
        ctx.remember_outputs(download_result.output_path)
        return native_download_payload(result)

    @router.post("/api/download")
    def download(request: DownloadRequest) -> dict[str, object]:
        username, password = ctx.require_credentials()
        progress = ProgressCollector()
        kwargs = item_kwargs(request, dry_run=False, on_progress=progress)
        result = ctx.call_service(lambda: service.download_lecture(username, password, **kwargs))
        if not result.ok:
            raise_for_result_error(result.error_code)
        ctx.remember_outputs(result.output_path)
        return download_payload(result, progress.events())

    @router.post("/api/download/jobs")
    def start_download_job(request: DownloadRequest) -> dict[str, Any]:
        username, password = ctx.require_credentials()
        kwargs = item_kwargs(request, dry_run=False)

        def work(job: Job) -> JobOutcome:
            result = service.download_lecture(username, password, **kwargs, on_progress=job.append_progress, should_cancel=job.should_cancel)
            if not result.ok:
                return JobOutcome.failed(result.error_code or "download_failed")
            ctx.remember_outputs(result.output_path)
            return JobOutcome.succeeded(download_payload(result, job.progress_events()))

        return ctx.start_job(ctx.download_jobs, work)

    add_job_routes(router, ctx.download_jobs, path="/api/download/jobs", not_found_code="download_job_not_found")

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
        result = ctx.call_service(lambda: service.batch_download_lectures(username, password, **kwargs))
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
            result = service.batch_download_lectures(username, password, **kwargs, on_progress=job.append_progress, should_cancel=job.should_cancel)
            if not result.ok:
                return JobOutcome.failed(result.error_code or "download_failed")
            ctx.remember_outputs(*(item.output_path for item in result.items))
            return JobOutcome.succeeded(batch_payload(result, job.progress_events()))

        return ctx.start_job(ctx.download_jobs, work)

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

        return ctx.start_job(ctx.artifact_jobs, work)

    add_job_routes(router, ctx.artifact_jobs, path="/api/download/artifacts/jobs", not_found_code="artifact_job_not_found")

    return router
