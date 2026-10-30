"""Download-folder tidy-up: legacy summary, preview, apply, undo, past runs (design 09 § 내부 API 계약).

Internal API of the desktop app. Plan/apply/undo are background jobs like downloads; the library lock
keeps them from running together with a download (409 `library_busy` / `library_tidy_running`).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from ssu_lecture_downloader.core.errors import TidyRunNotFound
from ssu_lecture_downloader.core.jobs import Job, JobOutcome
from ssu_lecture_downloader.core.library_migration import (
    RUN_ID_PATTERN,
    CatalogCourse,
    MigrationRunner,
    legacy_summary,
    run_journal_path,
)
from ssu_lecture_downloader.service.api_errors import (
    INVALID_RUN_ID_CODE,
    TIDY_PLAN_EXPIRED_CODE,
    TIDY_RUN_NOT_FOUND_CODE,
    tidy_error,
)
from ssu_lecture_downloader.service.app_context import AppContext, add_job_routes


class TidyPlanRequest(BaseModel):
    output_dir: str | None = None


class TidyApplyRequest(BaseModel):
    plan_id: str


class TidyUndoRequest(BaseModel):
    run_id: str
    # Where the run happened. Without it the folder the run was started or listed for in this session, else the default one.
    output_dir: str | None = None


class _LmsFailure(Exception):
    def __init__(self, error_code: str | None) -> None:
        super().__init__(error_code)
        self.error_code = error_code or "lms_unavailable"


class _ServiceCatalog:
    """The current courses and their content items, read from the LMS service (cached lists, read only)."""

    def __init__(self, service: Any, username: str, password: str) -> None:
        self._service = service
        self._credentials = (username, password)

    def courses(self) -> Sequence[CatalogCourse]:
        result = self._service.discover_courses(*self._credentials)
        if not result.ok:
            raise _LmsFailure(result.error_code)
        return [CatalogCourse(str(course.id), course.name) for course in result.courses]

    def content_items(self, course: CatalogCourse) -> Sequence[Any]:
        result = self._service.get_course_detail(*self._credentials, course_id=course.course_id, course_name=course.name)
        if not result.ok:
            raise _LmsFailure(result.error_code)
        return result.content_items or []


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    runner = MigrationRunner()

    @router.get("/api/library/legacy-summary")
    def library_legacy_summary(output_dir: str | None = None) -> dict[str, Any]:
        return legacy_summary(ctx.output_dir_for(output_dir))

    @router.post("/api/library/tidy/plan/jobs")
    def start_tidy_plan_job(request: TidyPlanRequest) -> dict[str, Any]:
        username, password = ctx.require_credentials()
        root = ctx.output_dir_for(request.output_dir)
        ctx.library_lock.check_idle()

        def work(job: Job) -> JobOutcome:
            try:
                plan = runner.plan(root, _ServiceCatalog(ctx.service, username, password))
            except _LmsFailure as failure:
                return JobOutcome.failed(failure.error_code)
            ctx.tidy_plans.put(plan)
            return JobOutcome.succeeded(plan.payload())

        return ctx.start_job(ctx.tidy_plan_jobs, work)

    @router.post("/api/library/tidy/apply/jobs")
    def start_tidy_apply_job(request: TidyApplyRequest) -> dict[str, Any]:
        with ctx.library_lock.admit_tidy_job():  # busy is answered before the plan is used up
            plan = ctx.tidy_plans.take(request.plan_id)
            if plan is None:
                raise tidy_error(TIDY_PLAN_EXPIRED_CODE)

            def work(job: Job) -> JobOutcome:
                result = runner.apply(plan, on_progress=job.append_progress, should_cancel=job.should_cancel)
                if result.run_id:
                    ctx.tidy_run_roots[result.run_id] = plan.root
                return JobOutcome.succeeded(result.payload())

            return ctx.start_job(ctx.tidy_jobs, work)

    @router.post("/api/library/tidy/undo/jobs")
    def start_tidy_undo_job(request: TidyUndoRequest) -> dict[str, Any]:
        if not RUN_ID_PATTERN.match(request.run_id):
            raise tidy_error(INVALID_RUN_ID_CODE)
        root = ctx.output_dir_for(request.output_dir) if request.output_dir else ctx.tidy_run_roots.get(request.run_id, ctx.default_output_dir)
        with ctx.library_lock.admit_tidy_job():
            try:
                run_journal_path(root, request.run_id)
            except TidyRunNotFound:
                raise tidy_error(TIDY_RUN_NOT_FOUND_CODE) from None

            def work(job: Job) -> JobOutcome:
                result = runner.undo(root, request.run_id, on_progress=job.append_progress, should_cancel=job.should_cancel)
                return JobOutcome.succeeded(result.payload())

            return ctx.start_job(ctx.tidy_jobs, work)

    @router.get("/api/library/tidy/runs")
    def tidy_runs(output_dir: str | None = None) -> dict[str, Any]:
        root = ctx.output_dir_for(output_dir)
        runs = runner.runs(root)
        for run in runs:
            ctx.tidy_run_roots[run["run_id"]] = root
        return {"ok": True, "runs": runs}

    add_job_routes(router, ctx.tidy_plan_jobs, path="/api/library/tidy/plan/jobs", not_found_code="tidy_job_not_found")
    add_job_routes(router, ctx.tidy_jobs, path="/api/library/tidy/apply/jobs", not_found_code="tidy_job_not_found")
    add_job_routes(router, ctx.tidy_jobs, path="/api/library/tidy/undo/jobs", not_found_code="tidy_job_not_found")
    return router
